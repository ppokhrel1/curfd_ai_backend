import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.openscad_agent.llm_provider import get_llm
from app.services.openscad_agent.prompts import AGENT_PROMPT
from app.services.openscad_agent.experiments import resolve_prompts
from app.services.openscad_agent.example_retriever import get_examples_for_prompt
from app.services.openscad_agent.tools import (
    apply_parameter_changes,
    build_parametric_model,
    search_reference_images,
    generate_3d_from_image,
)
from app.services.openscad_agent.tools.parameter_patcher import patch_code
from app.services.openscad_agent.tools.image_search import _fetch_ddg_images, _download_thumbnail
from app.schemas.openscad import OpenSCADResponse, OpenSCADParameter
from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Jewelry domain detection ─────────────────────────────────────────────────

_JEWELRY_KEYWORDS = frozenset({
    "ring", "band", "diamond", "gem", "gemstone", "prong", "bezel", "pave",
    "pavé", "halo", "solitaire", "setting", "carat", "earring", "pendant",
    "necklace", "bracelet", "brooch", "tiara", "crown ring", "eternity",
    "cocktail", "engagement", "wedding band", "signet", "jewelry", "jewellery",
    "filigree", "milgrain", "cathedral", "channel set", "brilliant cut",
    "princess cut", "emerald cut", "marquise", "cushion cut",
})


def _detect_jewelry(text: str) -> bool:
    """Check if user request is about jewelry design."""
    lower = text.lower()
    return any(kw in lower for kw in _JEWELRY_KEYWORDS)


# Tools available to the agent
_tools = [apply_parameter_changes, build_parametric_model, search_reference_images, generate_3d_from_image]
_tools_by_name = {t.name: t for t in _tools}


# ── Component factory ────────────────────────────────────────────────────────

def _get_components(provider: str | None = None, model: str | None = None, thinking: bool = False):
    """Create LLM components for a given provider/model pair.

    Uses a single LLM instance for both agent routing and code generation.
    Tool calling is enabled for all major providers.
    """
    resolved_provider = (provider or settings.llm_provider).lower()
    llm = get_llm(provider, model, thinking)

    tools_supported = resolved_provider in ("anthropic", "openai", "gemini", "groq")
    llm_with_tools = llm.bind_tools(_tools) if tools_supported else None

    return {
        "llm": llm,
        "code_llm": llm,  # Same LLM for code gen — simpler, consistent quality
        "llm_with_tools": llm_with_tools,
        "tools_supported": tools_supported,
        "provider": resolved_provider,
    }


# ── Structured output schema ─────────────────────────────────────────────────

class CodeParameter(BaseModel):
    """A single adjustable numeric parameter from generated CAD code."""
    name: str = Field(description="Variable name exactly as it appears in the code")
    value: float = Field(description="Current default numeric value")
    min_val: float = Field(description="Reasonable minimum value for this parameter")
    max_val: float = Field(description="Reasonable maximum value for this parameter")
    description: str = Field(description="Brief description of what this parameter controls")


class CodeGenResult(BaseModel):
    """Structured output from CAD code generation."""
    code: str = Field(description="Complete CAD code (OpenSCAD or CadQuery Python) without markdown fences")
    parameters: list[CodeParameter] = Field(description="All adjustable numeric parameters from the code with domain-appropriate min/max ranges")
    description: str = Field(description="Brief friendly description of what was built or modified")


def _get_structured_llm(components: dict):
    """Wrap the code LLM with structured output for deterministic extraction."""
    llm = components["code_llm"]
    provider = components["provider"]
    # Anthropic needs json_schema method for thinking mode compatibility
    if provider == "anthropic":
        method = "json_schema"
    elif provider == "gemini":
        method = "json_mode"
    else:
        method = "function_calling"
    return llm.with_structured_output(CodeGenResult, method=method)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_human_message(user_input: str, image_data_urls: list[str] | None = None) -> HumanMessage:
    """Build a HumanMessage, optionally with image content blocks."""
    if not image_data_urls:
        return HumanMessage(content=user_input)

    content_blocks: list[dict] = [{"type": "text", "text": user_input}]
    for url in image_data_urls:
        content_blocks.append({
            "type": "image_url",
            "image_url": {"url": url},
        })
    return HumanMessage(content=content_blocks)


def _extract_text_content(response) -> str:
    """Extract text from an AI response, handling both string and list content (thinking mode)."""
    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return content or ""


def _build_code_prompt(
    user_input: str, rag_context: str = "", code_language: str = "openscad",
) -> tuple[str, dict | None]:
    """Build the full code generation system prompt, injecting domain context if needed.

    Returns:
        (prompt_string, experiment_metadata_or_none)
    """
    if code_language == "cadquery":
        from app.services.openscad_agent.prompts import CADQUERY_CODE_PROMPT
        logger.info("[PROMPT] CadQuery code generation")
        prompt = CADQUERY_CODE_PROMPT
        if rag_context:
            prompt += rag_context
        return prompt, None

    code_prompt, jewelry_context, experiment_meta = resolve_prompts(user_input)

    prompt = code_prompt
    if _detect_jewelry(user_input) and jewelry_context:
        prompt += jewelry_context
        logger.info("[PROMPT] Jewelry domain context injected")
    if rag_context:
        prompt += rag_context
    return prompt, experiment_meta


def _build_direct_generation_messages(
    user_input: str,
    history: list[HumanMessage | AIMessage],
    rag_context: str = "",
    code_language: str = "openscad",
    image_data_urls: list[str] | None = None,
) -> tuple[list, dict | None]:
    """Build messages for direct code generation (no tools, uses CODE_PROMPT)."""
    prompt, experiment_meta = _build_code_prompt(user_input, rag_context, code_language)
    human_msg = _build_human_message(user_input, image_data_urls)
    return [SystemMessage(content=prompt)] + list(history) + [human_msg], experiment_meta


def _get_current_code_from_history(history: list[HumanMessage | AIMessage]) -> str:
    """Extract the most recent code (OpenSCAD or CadQuery) from conversation history.

    After the CADAM-style history format change, assistant messages contain
    raw code directly, so we check for code-like content first.
    """
    for msg in reversed(history):
        if isinstance(msg, AIMessage):
            text = _extract_text_content(msg)
            if not text:
                continue
            # History now contains raw code (CADAM pattern)
            if _looks_like_openscad(text) or _looks_like_cadquery(text):
                return text.strip()
            # Fallback: try extracting from code blocks (legacy format)
            code_match = _CODE_BLOCK_RE.search(text)
            if code_match:
                return code_match.group(1).strip()
    return ""


# ── Code generation (separate LLM call, like CADAM's handleToolCall) ─────────

async def _generate_code(
    tool_args: dict,
    history: list[HumanMessage | AIMessage],
    components: dict,
    user_input: str = "",
    rag_context: str = "",
    _experiment_meta_out: dict | None = None,
    code_language: str = "openscad",
) -> CodeGenResult:
    """Generate code using structured output for deterministic extraction.

    Uses LangChain's with_structured_output() to force the LLM to return
    a CodeGenResult with code, parameters, and description — no regex needed.
    """
    base_code = tool_args.get("baseCode", "")
    error = tool_args.get("error", "")

    # Build code generation messages with RAG + domain context
    prompt, experiment_meta = _build_code_prompt(user_input, rag_context, code_language)
    if experiment_meta and _experiment_meta_out is not None:
        _experiment_meta_out.update(experiment_meta)
    code_messages: list = [SystemMessage(content=prompt)]
    code_messages.extend(history)

    # Add base code context if modifying existing model
    if base_code:
        code_messages.append(AIMessage(content=base_code))

    # Always use the ORIGINAL user message, not the tool's paraphrased text
    user_text = user_input
    if error:
        error_label = "CadQuery Python" if code_language == "cadquery" else "OpenSCAD"
        user_text = f"{user_input}\n\nFix this {error_label} error: {error}"

    code_messages.append(HumanMessage(content=user_text))

    t_code = time.monotonic()
    try:
        structured_llm = _get_structured_llm(components)
        result = await structured_llm.ainvoke(code_messages)
        logger.info(f"[CODE_GEN] Structured output: {time.monotonic() - t_code:.2f}s — {len(result.code)} chars, {len(result.parameters)} params")
        return result
    except Exception as e:
        # Fallback: raw invoke + manual extraction
        logger.warning(f"[CODE_GEN] Structured output failed ({e}), falling back to raw invoke")
        llm = components["code_llm"]
        raw_result = await llm.ainvoke(code_messages)
        raw_text = _extract_text_content(raw_result)
        code = _strip_code_fences(raw_text)
        # Extract parameters with regex as fallback
        if _looks_like_cadquery(code):
            params = _extract_parameters_from_cadquery(code)
        else:
            params = _extract_parameters_from_code(code)
        logger.info(f"[CODE_GEN] Fallback: {time.monotonic() - t_code:.2f}s — {len(code)} chars")
        return CodeGenResult(
            code=code,
            parameters=[CodeParameter(**p) for p in params],
            description="Model generated.",
        )


def _handle_parameter_changes(
    tool_args: dict,
    history: list[HumanMessage | AIMessage],
) -> str:
    """Handle apply_parameter_changes by patching current code from history."""
    updates = tool_args.get("updates", [])
    current_code = _get_current_code_from_history(history)

    if not current_code:
        return "No existing code found to patch."

    patched_code, applied, not_found = patch_code(current_code, updates)

    if not applied:
        return "No parameters matched. The user may need a structural code change instead."

    logger.info(f"Patched {len(applied)} parameter(s): {', '.join(applied)}")
    if not_found:
        logger.warning(f"Parameters not found: {', '.join(not_found)}")

    return patched_code


# ── Tool-calling agent loop ──────────────────────────────────────────────────

async def _run_tool_loop(
    user_input: str,
    history: list[HumanMessage | AIMessage],
    max_iterations: int,
    components: dict,
    image_data_urls: list[str] | None = None,
    rag_context: str = "",
    code_language: str = "openscad",
) -> dict:
    """
    Manual tool-calling loop:
    - Outer agent uses AGENT_PROMPT with tools
    - build_parametric_model → structured output code gen
    - apply_parameter_changes → regex patching of current code

    Returns dict with openscad_code, parameters, model_type, message.
    """
    llm_with_tools = components["llm_with_tools"]
    llm = components["llm"]

    human_msg = _build_human_message(user_input, image_data_urls)
    if code_language == "cadquery":
        from app.services.openscad_agent.prompts import AGENT_PROMPT_CADQUERY
        agent_prompt = AGENT_PROMPT_CADQUERY
    else:
        agent_prompt = AGENT_PROMPT
    messages = [SystemMessage(content=agent_prompt)] + list(history) + [human_msg]

    for _ in range(max_iterations):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            text = _extract_text_content(response)
            return {"openscad_code": "", "parameters": [], "model_type": "chat", "message": text}

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_id = tool_call.get("id", tool_name)

            if tool_name == "generate_3d_from_image":
                image_query = tool_args.get("image_query", "")
                prompt = tool_args.get("prompt", "")
                # Search for a reference image
                image_url = None
                results = _fetch_ddg_images(f"{image_query} 3D reference", max_results=3)
                for r in results:
                    thumb = r.get("thumbnail")
                    if thumb:
                        data_url = _download_thumbnail(thumb)
                        if data_url:
                            image_url = data_url
                            break
                    img_url = r.get("image")
                    if img_url:
                        image_url = img_url
                        break
                return {
                    "openscad_code": "",
                    "parameters": [],
                    "model_type": "image_to_3d",
                    "message": f"Generating 3D model from image for: {image_query}",
                    "image_to_3d": {
                        "image_url": image_url,
                        "image_query": image_query,
                        "prompt": prompt,
                    },
                }

            elif tool_name == "build_parametric_model":
                gen_result = await _generate_code(tool_args, history, components, user_input, rag_context, code_language=code_language)
                logger.info(f"Code generation returned {len(gen_result.code)} chars, {len(gen_result.parameters)} params")
                return {
                    "openscad_code": gen_result.code,
                    "parameters": [p.model_dump() for p in gen_result.parameters],
                    "model_type": "mechanical",
                    "message": gen_result.description,
                }

            elif tool_name == "apply_parameter_changes":
                result = _handle_parameter_changes(tool_args, history)
                if _looks_like_openscad(result) or _looks_like_cadquery(result):
                    if _looks_like_cadquery(result):
                        params = _extract_parameters_from_cadquery(result)
                    else:
                        params = _extract_parameters_from_code(result)
                    return {"openscad_code": result, "parameters": params, "model_type": "mechanical", "message": "Parameters updated."}
                return {"openscad_code": "", "parameters": [], "model_type": "chat", "message": result}

            else:
                # Fallback: execute tool normally (e.g. search_reference_images)
                tool_fn = _tools_by_name.get(tool_name)
                if tool_fn is None:
                    tool_result = f"Unknown tool: {tool_name}"
                else:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as e:
                        tool_result = f"Tool error: {e}"

                logger.info(f"Tool {tool_name} returned: {str(tool_result)[:200]}")

                # Extract embedded image data URLs for multimodal context
                tool_text = str(tool_result)
                img_match = re.search(r"\[IMAGE_DATA_URL\](.*?)\[/IMAGE_DATA_URL\]", tool_text, re.DOTALL)
                if img_match:
                    clean_text = re.sub(r"\[IMAGE_DATA_URL\].*?\[/IMAGE_DATA_URL\]", "", tool_text, flags=re.DOTALL).strip()
                    content_blocks: list[dict] = [{"type": "text", "text": clean_text}]
                    content_blocks.append({"type": "image_url", "image_url": {"url": img_match.group(1)}})
                    messages.append(ToolMessage(content=content_blocks, tool_call_id=tool_id))
                else:
                    messages.append(ToolMessage(content=tool_text, tool_call_id=tool_id))

    response = await llm.ainvoke(messages)
    text = _extract_text_content(response)
    return {"openscad_code": "", "parameters": [], "model_type": "chat", "message": text}


# ── Extraction helpers (kept for patching and history detection) ──────────────

_CODE_BLOCK_RE = re.compile(r"```(?:openscad|python)?\s*\n(.*?)```", re.DOTALL)
_PARAM_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([-+]?[0-9]*\.?[0-9]+)\s*;",
    re.MULTILINE,
)
_IGNORED_VARS = frozenset({"$fn", "$fa", "$fs", "eps", "epsilon"})


def _strip_code_fences(code: str) -> str:
    """Strip markdown code fences from generated code (used only as fallback)."""
    code = code.strip()
    match = re.match(r'^```(?:openscad|python)?\n?([\s\S]*?)\n?```$', code)
    if match:
        return match.group(1).strip()
    code_match = _CODE_BLOCK_RE.search(code)
    if code_match:
        return code_match.group(1).strip()
    return code


def _extract_parameters_from_code(code: str) -> list[dict]:
    """Extract tunable parameters from OpenSCAD code (used for patched code fallback)."""
    params = []
    for m in _PARAM_RE.finditer(code):
        name = m.group(1)
        val = float(m.group(2))
        if name in _IGNORED_VARS or name.startswith("$"):
            continue
        min_val = val * 0.5 if val > 0 else val - 10
        max_val = val * 1.5 if val > 0 else val + 10
        if min_val == max_val:
            min_val -= 5
            max_val += 5
        params.append({
            "name": name,
            "default_val": val,
            "min_val": round(min_val, 2),
            "max_val": round(max_val, 2),
            "description": f"Parameter: {name}",
        })
    return params


def _score_openscad_code(code: str) -> int:
    """Score how likely text is to be OpenSCAD code."""
    if not code or len(code) < 20:
        return 0
    score = 0
    patterns = [
        r'\b(cube|sphere|cylinder|polyhedron)\s*\(',
        r'\b(union|difference|intersection)\s*\(\s*\)',
        r'\b(translate|rotate|scale|mirror)\s*\(',
        r'\b(linear_extrude|rotate_extrude)\s*\(',
        r'\b(module|function)\s+\w+\s*\(',
        r'\$fn\s*=',
        r'\bfor\s*\(\s*\w+\s*=\s*\[',
        r';\s*$',
    ]
    for p in patterns:
        matches = re.findall(p, code, re.MULTILINE)
        if matches:
            score += len(matches)
    var_decls = re.findall(r'^\s*\w+\s*=\s*[^;]+;', code, re.MULTILINE)
    if var_decls:
        score += min(len(var_decls), 5)
    return score


def _validate_code_quality(code: str) -> dict:
    """Validate generated OpenSCAD code and return quality metrics (logging only)."""
    metrics: dict = {}
    if not code or len(code) < 20:
        return metrics

    plan_parts = re.findall(r'//\s*PARTS:\s*(.+)', code)
    planned = [p.strip() for p in plan_parts[0].split(',')]  if plan_parts else []
    metrics["planned_parts"] = len(planned)

    defined = re.findall(r'^\s*module\s+([a-zA-Z_]\w*)\s*\(', code, re.MULTILINE)
    defined = [m for m in defined if m not in ('main',)]
    metrics["defined_modules"] = len(defined)

    main_match = re.search(r'module\s+main\s*\(\s*\)\s*\{', code)
    called_in_main = []
    call_count_in_main = 0
    if main_match:
        depth, i = 1, main_match.end()
        while i < len(code) and depth > 0:
            if code[i] == '{': depth += 1
            elif code[i] == '}': depth -= 1
            i += 1
        main_body = code[main_match.end():i - 1] if depth == 0 else ""
        all_calls = re.findall(r'\b([a-zA-Z_]\w*)\s*\(', main_body)
        called_in_main = list(set(c for c in all_calls if c in defined))
        call_count_in_main = sum(1 for c in all_calls if c in defined)
    metrics["called_in_main"] = len(called_in_main)
    metrics["call_count_in_main"] = call_count_in_main

    if main_match:
        mirror_count = len(re.findall(r'\bmirror\s*\(', main_body))
        call_count_in_main += mirror_count
    metrics["effective_parts"] = call_count_in_main

    conn_vars = re.findall(r'^\s*([a-zA-Z_]\w*(?:_[xyz]|_offset|_pos|_attach|_z|_y|_x))\s*=', code, re.MULTILINE)
    metrics["connection_vars"] = len(conn_vars)

    translates = re.findall(r'translate\(\[([^\]]+)\]', code)
    magic_count = 0
    for t in translates:
        nums = re.findall(r'(?<![a-zA-Z_\.])\d+\.?\d*', t)
        magic_count += sum(1 for n in nums if float(n) > 5)
    metrics["magic_numbers_in_translate"] = magic_count

    uncalled = [m for m in defined if m not in called_in_main]
    metrics["uncalled_modules"] = len(uncalled)
    metrics["uses_eps"] = bool(re.search(r'\beps\b', code))
    metrics["has_tree"] = bool(re.search(r'//\s*TREE:', code))

    issues = []
    if metrics["planned_parts"] > 0 and metrics["effective_parts"] < metrics["planned_parts"] - 1:
        issues.append("parts_mismatch")
    if metrics["defined_modules"] > 0 and metrics["called_in_main"] < metrics["defined_modules"]:
        issues.append("uncalled_modules")
    if metrics["connection_vars"] == 0:
        issues.append("no_connections")
    if metrics["magic_numbers_in_translate"] > 5:
        issues.append("magic_numbers")
    if not metrics["uses_eps"]:
        issues.append("no_eps")

    metrics["issues"] = issues
    metrics["status"] = "PASS" if not issues else "WARN"

    logger.info(
        "[QUALITY] %s | planned=%d modules=%d called=%d effective=%d conn=%d magic=%d tree=%s issues=%s",
        metrics["status"], metrics["planned_parts"], metrics["defined_modules"],
        metrics["called_in_main"], metrics["effective_parts"], metrics["connection_vars"],
        metrics["magic_numbers_in_translate"], metrics["has_tree"], issues or "none",
    )
    return metrics


def _looks_like_openscad(text: str) -> bool:
    """Quick heuristic to detect raw OpenSCAD code."""
    return _score_openscad_code(text) >= 3


def _looks_like_cadquery(text: str) -> bool:
    """Quick heuristic to detect CadQuery Python code."""
    return "import cadquery" in text or "cq.Workplane" in text


_PYTHON_PARAM_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([-+]?[0-9]*\.?[0-9]+)\s*(?:#.*)?$",
    re.MULTILINE,
)
_IGNORED_PYTHON_VARS = frozenset({"result", "cq", "pi", "tau", "e"})


def _extract_parameters_from_cadquery(code: str) -> list[dict]:
    """Extract tunable parameters from CadQuery code (used for patched code fallback)."""
    params = []
    for m in _PYTHON_PARAM_RE.finditer(code):
        name = m.group(1)
        val = float(m.group(2))
        if name in _IGNORED_PYTHON_VARS or name.startswith("_"):
            continue
        preceding = code[:m.start()]
        if "cq.Workplane" in preceding or "\ndef " in preceding:
            break
        min_val = val * 0.5 if val > 0 else val - 10
        max_val = val * 1.5 if val > 0 else val + 10
        if min_val == max_val:
            min_val -= 5
            max_val += 5
        params.append({
            "name": name,
            "default_val": val,
            "min_val": round(min_val, 2),
            "max_val": round(max_val, 2),
            "description": f"Parameter: {name}",
        })
    return params


# ── Public API ────────────────────────────────────────────────────────────────

async def run_agent(
    user_input: str,
    history: list[HumanMessage | AIMessage],
    image_data_urls: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    thinking: bool = False,
    db: AsyncSession | None = None,
    _extra_meta: dict | None = None,
    code_language: str = "openscad",
) -> OpenSCADResponse:
    """Run agent and return structured OpenSCADResponse.

    Args:
        _extra_meta: Optional mutable dict populated with experiment + quality_metrics data.
    """
    t_start = time.monotonic()
    c = _get_components(provider, model, thinking)
    logger.info(f"[AGENT] Starting request: provider={c['provider']}, model={model}, thinking={thinking}, code_language={code_language}")

    # Start RAG fetch in parallel with agent work
    rag_context = ""
    rag_task: asyncio.Task | None = None
    if db:
        async def _fetch_rag():
            try:
                t_rag = time.monotonic()
                is_jewelry = _detect_jewelry(user_input)
                ctx = await get_examples_for_prompt(db, user_input, is_jewelry=is_jewelry, code_language=code_language)
                logger.info(f"[AGENT][RAG] {time.monotonic() - t_rag:.2f}s — {len(ctx)} chars, jewelry={is_jewelry}")
                return ctx
            except Exception as e:
                logger.warning(f"[AGENT][RAG] Failed to fetch examples: {e}")
                return ""
        rag_task = asyncio.create_task(_fetch_rag())

    # Await RAG before code generation
    if rag_task is not None:
        rag_context = await rag_task

    experiment_meta = None
    if image_data_urls:
        # Images: use structured output for direct code gen
        human_msg = _build_human_message(user_input, image_data_urls)
        prompt, experiment_meta = _build_code_prompt(user_input, rag_context, code_language)
        messages = [SystemMessage(content=prompt)] + list(history) + [human_msg]
        try:
            structured_llm = _get_structured_llm(c)
            gen_result = await structured_llm.ainvoke(messages)
            data = {
                "openscad_code": gen_result.code,
                "parameters": [p.model_dump() for p in gen_result.parameters],
                "model_type": "mechanical",
                "message": gen_result.description,
            }
        except Exception as e:
            logger.warning(f"[AGENT] Structured output failed for image path: {e}")
            result = await c["code_llm"].ainvoke(messages)
            raw = _extract_text_content(result)
            code = _strip_code_fences(raw)
            params = _extract_parameters_from_cadquery(code) if _looks_like_cadquery(code) else _extract_parameters_from_code(code)
            data = {"openscad_code": code, "parameters": params, "model_type": "mechanical", "message": "Model generated."}
    elif c["tools_supported"] and c["llm_with_tools"] is not None:
        try:
            data = await _run_tool_loop(
                user_input, history, settings.agent_max_iterations,
                c, rag_context=rag_context, code_language=code_language,
            )
        except Exception as e:
            logger.error(f"Tool loop failed, falling back to direct generation: {e}")
            msgs, experiment_meta = _build_direct_generation_messages(user_input, history, rag_context, code_language)
            try:
                structured_llm = _get_structured_llm(c)
                gen_result = await structured_llm.ainvoke(msgs)
                data = {
                    "openscad_code": gen_result.code,
                    "parameters": [p.model_dump() for p in gen_result.parameters],
                    "model_type": "mechanical",
                    "message": gen_result.description,
                }
            except Exception as e2:
                logger.warning(f"[AGENT] Structured fallback also failed: {e2}")
                result = await c["code_llm"].ainvoke(msgs)
                raw = _extract_text_content(result)
                data = {"openscad_code": "", "parameters": [], "model_type": "chat", "message": raw[:500]}
    else:
        # No tools: direct structured code gen
        msgs, experiment_meta = _build_direct_generation_messages(user_input, history, rag_context, code_language)
        try:
            structured_llm = _get_structured_llm(c)
            gen_result = await structured_llm.ainvoke(msgs)
            data = {
                "openscad_code": gen_result.code,
                "parameters": [p.model_dump() for p in gen_result.parameters],
                "model_type": "mechanical",
                "message": gen_result.description,
            }
        except Exception as e:
            logger.warning(f"[AGENT] Structured output failed: {e}")
            result = await c["code_llm"].ainvoke(msgs)
            raw = _extract_text_content(result)
            code = _strip_code_fences(raw)
            params = _extract_parameters_from_cadquery(code) if _looks_like_cadquery(code) else _extract_parameters_from_code(code)
            data = {"openscad_code": code, "parameters": params, "model_type": "mechanical", "message": "Model generated."}

    t_total = time.monotonic() - t_start
    logger.info(f"[AGENT] Completed in {t_total:.2f}s")

    quality_metrics = None
    if data.get("openscad_code") and not _looks_like_cadquery(data["openscad_code"]):
        quality_metrics = _validate_code_quality(data["openscad_code"])

    if _extra_meta is not None:
        if experiment_meta:
            _extra_meta["experiment"] = experiment_meta
        if quality_metrics:
            _extra_meta["quality_metrics"] = quality_metrics

    return OpenSCADResponse(
        openscad_code=data["openscad_code"],
        parameters=[OpenSCADParameter(**p) for p in data["parameters"]],
        model_type=data["model_type"],
        message=data["message"],
    )


# ── Streaming approach ────────────────────────────────────────────────────────

async def run_agent_stream(
    user_input: str,
    history: list[HumanMessage | AIMessage],
    image_data_urls: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    thinking: bool = False,
    db: AsyncSession | None = None,
    code_language: str = "openscad",
) -> AsyncGenerator[dict, None]:
    """
    LLM streaming with structured output for code generation.

    Flow:
    1. Fetch RAG examples from DB (vector search)
    2. Stream outer agent (AGENT_PROMPT) text tokens
    3. On build_parametric_model → structured output code gen (deterministic)
    4. On apply_parameter_changes → regex patch current code
    5. Direct gen (no tools) → structured output

    Yields:
        {"type": "token", "text": "..."} for each text token
        {"type": "tool", "tool_name": ..., "status": ...} when a tool executes
        {"type": "done", "data": {...}} at the end with structured data
    """
    t_start = time.monotonic()
    c = _get_components(provider, model, thinking)
    logger.info(f"[STREAM] Starting: provider={c['provider']}, model={model}, thinking={thinking}, code_language={code_language}")

    human_msg = _build_human_message(user_input, image_data_urls)

    use_tools = (
        not image_data_urls
        and c["tools_supported"]
        and c["llm_with_tools"] is not None
    )

    # Start RAG fetch in parallel with agent routing
    rag_context = ""
    rag_task: asyncio.Task | None = None
    if db and use_tools:
        async def _fetch_rag():
            try:
                t_rag = time.monotonic()
                is_jewelry = _detect_jewelry(user_input)
                ctx = await get_examples_for_prompt(db, user_input, is_jewelry=is_jewelry)
                logger.info(f"[STREAM][RAG] {time.monotonic() - t_rag:.2f}s — {len(ctx)} chars, jewelry={is_jewelry}")
                return ctx
            except Exception as e:
                logger.warning(f"[STREAM][RAG] Failed: {e}")
                return ""
        rag_task = asyncio.create_task(_fetch_rag())
    elif db:
        try:
            t_rag = time.monotonic()
            is_jewelry = _detect_jewelry(user_input)
            rag_context = await get_examples_for_prompt(db, user_input, is_jewelry=is_jewelry, code_language=code_language)
            logger.info(f"[STREAM][RAG] {time.monotonic() - t_rag:.2f}s — {len(rag_context)} chars, jewelry={is_jewelry}")
        except Exception as e:
            logger.warning(f"[STREAM][RAG] Failed: {e}")

    experiment_meta = None
    _exp_meta_out: dict = {}

    # ── Direct generation path (no tools): use structured output ──
    if not use_tools:
        if rag_task is not None and not rag_task.done():
            rag_task.cancel()

        yield {"type": "tool", "tool_name": "build_parametric_model", "status": "generating"}
        try:
            msgs, experiment_meta = _build_direct_generation_messages(user_input, history, rag_context, code_language, image_data_urls)
            structured_llm = _get_structured_llm(c)
            gen_result = await structured_llm.ainvoke(msgs)
            code_text = gen_result.code
            parameters = [p.model_dump() for p in gen_result.parameters]
            msg = gen_result.description
        except Exception as e:
            logger.warning(f"[STREAM] Structured direct gen failed: {e}, falling back")
            msgs, experiment_meta = _build_direct_generation_messages(user_input, history, rag_context, code_language, image_data_urls)
            result = await c["code_llm"].ainvoke(msgs)
            raw = _extract_text_content(result)
            code_text = _strip_code_fences(raw)
            if _looks_like_cadquery(code_text):
                parameters = _extract_parameters_from_cadquery(code_text)
            else:
                parameters = _extract_parameters_from_code(code_text)
            msg = "Model generated."

        yield {"type": "tool", "tool_name": "build_parametric_model", "status": "completed"}

        quality_metrics = None
        if code_text and not _looks_like_cadquery(code_text):
            from app.cadquery.tasks import _clean_scad
            code_text = _clean_scad(code_text)
            quality_metrics = _validate_code_quality(code_text)

        response = {
            "openscad_code": code_text,
            "parameters": parameters,
            "model_type": "mechanical",
            "message": msg,
        }
        if experiment_meta:
            response["experiment"] = experiment_meta
        if quality_metrics:
            response["quality_metrics"] = quality_metrics

        t_total = time.monotonic() - t_start
        logger.info(f"[STREAM] Direct gen completed in {t_total:.2f}s — {len(code_text)} chars")
        yield {"type": "done", "data": response}
        return

    # ── Tool-based agent path: stream outer agent, structured output for code gen ──
    if code_language == "cadquery":
        from app.services.openscad_agent.prompts import AGENT_PROMPT_CADQUERY
        system_prompt = AGENT_PROMPT_CADQUERY
    else:
        system_prompt = AGENT_PROMPT
    messages = [SystemMessage(content=system_prompt)] + list(history) + [human_msg]

    agent_text = ""
    code_text = ""
    parameters: list[dict] = []
    gen_msg = ""

    for _iteration in range(settings.agent_max_iterations):
        collected_response = None

        try:
            async for chunk in c["llm_with_tools"].astream(messages):
                collected_response = chunk if collected_response is None else collected_response + chunk
                token = _extract_text_content(chunk)
                if token:
                    agent_text += token
                    yield {"type": "token", "text": token}
        except Exception as e:
            logger.error(f"Streaming failed: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}
            return

        # Check for tool calls
        if not (collected_response and hasattr(collected_response, "tool_calls") and collected_response.tool_calls):
            break

        for tool_call in collected_response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            if tool_name == "generate_3d_from_image":
                image_query = tool_args.get("image_query", "")
                prompt = tool_args.get("prompt", "")
                yield {"type": "tool", "tool_name": tool_name, "status": "searching"}
                # Search for a reference image
                image_url = None
                results = _fetch_ddg_images(f"{image_query} 3D reference", max_results=3)
                for r in results:
                    thumb = r.get("thumbnail")
                    if thumb:
                        data_url = _download_thumbnail(thumb)
                        if data_url:
                            image_url = data_url
                            break
                    img_url = r.get("image")
                    if img_url:
                        image_url = img_url
                        break
                yield {"type": "tool", "tool_name": tool_name, "status": "completed"}
                # Yield a special event for the WS handler to pick up
                yield {
                    "type": "image_to_3d.trigger",
                    "data": {
                        "image_url": image_url,
                        "image_query": image_query,
                        "prompt": prompt,
                    },
                }
                # Also yield done with marker so WS handler knows
                yield {
                    "type": "done",
                    "data": {
                        "openscad_code": "",
                        "parameters": [],
                        "model_type": "image_to_3d",
                        "message": f"Generating 3D model from image for: {image_query}",
                        "image_to_3d": {
                            "image_url": image_url,
                            "image_query": image_query,
                            "prompt": prompt,
                        },
                    },
                }
                return

            elif tool_name == "build_parametric_model":
                # Await RAG if running in parallel
                if rag_task is not None:
                    rag_context = await rag_task
                    rag_task = None

                yield {"type": "tool", "tool_name": tool_name, "status": "generating"}
                try:
                    gen_result = await _generate_code(
                        tool_args, history, c, user_input, rag_context,
                        _experiment_meta_out=_exp_meta_out, code_language=code_language,
                    )
                    code_text = gen_result.code
                    parameters = [p.model_dump() for p in gen_result.parameters]
                    gen_msg = gen_result.description
                    yield {"type": "tool", "tool_name": tool_name, "status": "completed"}
                except Exception as e:
                    logger.error(f"Code generation failed: {e}", exc_info=True)
                    yield {"type": "tool", "tool_name": tool_name, "status": "error"}
                    yield {"type": "error", "message": str(e)}
                    return

            elif tool_name == "apply_parameter_changes":
                yield {"type": "tool", "tool_name": tool_name, "status": "patching"}
                patched = _handle_parameter_changes(tool_args, history)
                if patched and (_looks_like_openscad(patched) or _looks_like_cadquery(patched)):
                    code_text = patched
                    if _looks_like_cadquery(patched):
                        parameters = _extract_parameters_from_cadquery(patched)
                    else:
                        parameters = _extract_parameters_from_code(patched)
                    gen_msg = "Parameters updated."
                    yield {"type": "token", "text": patched}
                    yield {"type": "tool", "tool_name": tool_name, "status": "completed"}
                else:
                    yield {"type": "tool", "tool_name": tool_name, "status": "error"}
                    yield {"type": "token", "text": patched}

            else:
                # Execute other tools (e.g. search_reference_images)
                tool_fn = _tools_by_name.get(tool_name)
                if tool_fn:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as e:
                        tool_result = f"Tool error: {e}"
                    logger.info(f"[STREAM] Tool {tool_name} returned: {str(tool_result)[:200]}")
                    yield {"type": "tool", "tool_name": tool_name, "status": "completed"}
                    messages.append(collected_response)
                    tool_id = tool_call.get("id", tool_name)

                    tool_text = str(tool_result)
                    img_match = re.search(r"\[IMAGE_DATA_URL\](.*?)\[/IMAGE_DATA_URL\]", tool_text, re.DOTALL)
                    if img_match:
                        clean_text = re.sub(r"\[IMAGE_DATA_URL\].*?\[/IMAGE_DATA_URL\]", "", tool_text, flags=re.DOTALL).strip()
                        content_blocks: list[dict] = [{"type": "text", "text": clean_text}]
                        content_blocks.append({"type": "image_url", "image_url": {"url": img_match.group(1)}})
                        messages.append(ToolMessage(content=content_blocks, tool_call_id=tool_id))
                    else:
                        messages.append(ToolMessage(content=tool_text, tool_call_id=tool_id))
                    collected_response = None
                    agent_text = ""
                    continue

        # After passthrough tools, continue so agent can act on results
        if collected_response is None:
            continue
        break

    # Cancel RAG task if still running
    if rag_task is not None and not rag_task.done():
        rag_task.cancel()

    # Merge experiment metadata
    final_experiment_meta = experiment_meta or _exp_meta_out or None

    # Build final response
    quality_metrics = None
    if code_text:
        if not _looks_like_cadquery(code_text):
            from app.cadquery.tasks import _clean_scad
            code_text = _clean_scad(code_text)
            quality_metrics = _validate_code_quality(code_text)

        msg = gen_msg or re.sub(r'```[\s\S]*?```', '', agent_text).strip() or "Model generated."
        if len(msg) > 500:
            msg = msg[:500]

        response = {
            "openscad_code": code_text,
            "parameters": parameters,
            "model_type": "mechanical",
            "message": msg,
        }
    else:
        # No code generated — pure chat response
        msg = agent_text.strip() or "Here to help!"
        if len(msg) > 500:
            msg = msg[:500]
        response = {
            "openscad_code": "",
            "parameters": [],
            "model_type": "chat",
            "message": msg,
        }

    if final_experiment_meta:
        response["experiment"] = final_experiment_meta
    if quality_metrics:
        response["quality_metrics"] = quality_metrics

    t_total = time.monotonic() - t_start
    logger.info(f"[STREAM] Completed in {t_total:.2f}s — code={len(code_text)} chars")
    yield {"type": "done", "data": response}

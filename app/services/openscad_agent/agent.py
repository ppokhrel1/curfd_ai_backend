import logging
import re
import time
from collections.abc import AsyncGenerator
from functools import lru_cache

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
)
from app.services.openscad_agent.tools.parameter_patcher import patch_code
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
_tools = [apply_parameter_changes, build_parametric_model, search_reference_images]
_tools_by_name = {t.name: t for t in _tools}


# ── Cached component factory ─────────────────────────────────────────────────

@lru_cache(maxsize=16)
def _get_components(provider: str | None = None, model: str | None = None, thinking: bool = False):
    """Lazily create and cache LLM + chains for a given provider/model pair."""
    llm = get_llm(provider, model, thinking)
    resolved_provider = (provider or settings.llm_provider).lower()

    tools_supported = resolved_provider in ("anthropic",)
    llm_with_tools = llm.bind_tools(_tools) if tools_supported else None

    # Separate code-gen LLM with lower temperature and higher token limit
    code_llm = get_llm(
        provider,
        settings.code_gen_model or model,
        thinking,
        temperature_override=settings.code_gen_temperature,
        max_tokens_override=settings.code_gen_max_tokens,
    )

    return {
        "llm": llm,
        "code_llm": code_llm,
        "llm_with_tools": llm_with_tools,
        "tools_supported": tools_supported,
        "provider": resolved_provider,
    }


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


def _build_code_prompt(user_input: str, rag_context: str = "") -> tuple[str, dict | None]:
    """Build the full code generation system prompt, injecting domain context if needed.

    Returns:
        (prompt_string, experiment_metadata_or_none)
    """
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
) -> tuple[list, dict | None]:
    """Build messages for direct code generation (no tools, uses CODE_PROMPT)."""
    prompt, experiment_meta = _build_code_prompt(user_input, rag_context)
    return [SystemMessage(content=prompt)] + list(history) + [HumanMessage(content=user_input)], experiment_meta


def _get_current_code_from_history(history: list[HumanMessage | AIMessage]) -> str:
    """Extract the most recent OpenSCAD code from conversation history.

    After the CADAM-style history format change, assistant messages contain
    raw code directly, so we check for OpenSCAD-like content first.
    """
    for msg in reversed(history):
        if isinstance(msg, AIMessage):
            text = _extract_text_content(msg)
            if not text:
                continue
            # History now contains raw code (CADAM pattern)
            if _looks_like_openscad(text):
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
) -> str:
    """Make a separate LLM call with CODE_PROMPT to generate OpenSCAD code.

    Matches CADAM's handleToolCall pattern:
    - System: STRICT_CODE_PROMPT (+ RAG examples if available)
    - ...full conversation history (history already excludes current user msg)
    - (optional) Assistant: baseCode
    - User: original user request (+ error context if present)
    """
    base_code = tool_args.get("baseCode", "")
    error = tool_args.get("error", "")

    # Build code generation messages (CADAM pattern) with RAG + domain context
    prompt, experiment_meta = _build_code_prompt(user_input, rag_context)
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
        user_text = f"{user_input}\n\nFix this OpenSCAD error: {error}"

    code_messages.append(HumanMessage(content=user_text))

    llm = components["code_llm"]
    t_code = time.monotonic()
    result = await llm.ainvoke(code_messages)
    code = _extract_text_content(result)
    logger.info(f"[CODE_GEN] {time.monotonic() - t_code:.2f}s — {len(code)} chars")

    # Strip markdown fences if present
    code = _strip_code_fences(code)

    return code


async def _generate_code_stream(
    tool_args: dict,
    history: list[HumanMessage | AIMessage],
    components: dict,
    user_input: str = "",
    rag_context: str = "",
    _experiment_meta_out: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Stream code generation tokens from a separate LLM call with CODE_PROMPT.

    Matches CADAM's pattern: uses original user_input, not tool's paraphrased text.
    RAG context is appended to CODE_PROMPT when available.
    """
    base_code = tool_args.get("baseCode", "")
    error = tool_args.get("error", "")

    prompt, experiment_meta = _build_code_prompt(user_input, rag_context)
    if experiment_meta and _experiment_meta_out is not None:
        _experiment_meta_out.update(experiment_meta)
    code_messages: list = [SystemMessage(content=prompt)]
    code_messages.extend(history)

    if base_code:
        code_messages.append(AIMessage(content=base_code))

    # Always use original user message
    user_text = user_input
    if error:
        user_text = f"{user_input}\n\nFix this OpenSCAD error: {error}"

    code_messages.append(HumanMessage(content=user_text))

    llm = components["code_llm"]
    async for chunk in llm.astream(code_messages):
        token = _extract_text_content(chunk)
        if token:
            yield token


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
) -> str:
    """
    Manual tool-calling loop matching CADAM's pattern:
    - Outer agent uses AGENT_PROMPT with tools
    - build_parametric_model → separate LLM call with CODE_PROMPT + RAG
    - apply_parameter_changes → regex patching of current code
    """
    llm_with_tools = components["llm_with_tools"]
    llm = components["llm"]

    human_msg = _build_human_message(user_input, image_data_urls)
    messages = [SystemMessage(content=AGENT_PROMPT)] + list(history) + [human_msg]

    for _ in range(max_iterations):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            return _extract_text_content(response)

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_id = tool_call.get("id", tool_name)

            if tool_name == "build_parametric_model":
                # CADAM pattern: separate LLM call with CODE_PROMPT + RAG context
                code = await _generate_code(tool_args, history, components, user_input, rag_context)
                logger.info(f"Code generation returned {len(code)} chars")
                return code

            elif tool_name == "apply_parameter_changes":
                # CADAM pattern: regex patching of current artifact code
                result = _handle_parameter_changes(tool_args, history)
                return result

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
    return _extract_text_content(response)


# ── Extraction (regex-based, no extra LLM call) ──────────────────────────────

_CODE_BLOCK_RE = re.compile(r"```(?:openscad)?\s*\n(.*?)```", re.DOTALL)
_PARAM_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([-+]?[0-9]*\.?[0-9]+)\s*;",
    re.MULTILINE,
)
_IGNORED_VARS = frozenset({"$fn", "$fa", "$fs", "eps", "epsilon"})


def _strip_code_fences(code: str) -> str:
    """Strip markdown code fences from generated code."""
    code = code.strip()
    # Try full-match first (entire response is a code block)
    match = re.match(r'^```(?:openscad)?\n?([\s\S]*?)\n?```$', code)
    if match:
        return match.group(1).strip()
    # Try extracting the best code block
    code_match = _CODE_BLOCK_RE.search(code)
    if code_match:
        return code_match.group(1).strip()
    return code


def _extract_parameters_from_code(code: str) -> list[dict]:
    """Extract tunable parameters from OpenSCAD top-level variable assignments."""
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
    """Score how likely text is to be OpenSCAD code (matches CADAM's scoreOpenSCADCode)."""
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
        r'\bimport\s*\(\s*"',
        r';\s*$',
    ]
    for p in patterns:
        matches = re.findall(p, code, re.MULTILINE)
        if matches:
            score += len(matches)

    # Variable declarations
    var_decls = re.findall(r'^\s*\w+\s*=\s*[^;]+;', code, re.MULTILINE)
    if var_decls:
        score += min(len(var_decls), 5)

    return score


def _validate_code_quality(code: str) -> dict:
    """Validate generated OpenSCAD code and return quality metrics."""
    metrics: dict = {}
    if not code or len(code) < 20:
        return metrics

    # 1. Extract PLAN comments
    plan_parts = re.findall(r'//\s*PARTS:\s*(.+)', code)
    planned = [p.strip() for p in plan_parts[0].split(',')]  if plan_parts else []
    metrics["planned_parts"] = len(planned)

    # 2. Defined modules (parameterless, excluding main)
    defined = re.findall(r'^\s*module\s+([a-zA-Z_]\w*)\s*\(\s*\)', code, re.MULTILINE)
    defined = [m for m in defined if m not in ('main',)]
    metrics["defined_modules"] = len(defined)

    # 3. Modules called in main()
    main_match = re.search(r'module\s+main\s*\(\s*\)\s*\{', code)
    called_in_main = []
    if main_match:
        depth, i = 1, main_match.end()
        while i < len(code) and depth > 0:
            if code[i] == '{': depth += 1
            elif code[i] == '}': depth -= 1
            i += 1
        main_body = code[main_match.end():i - 1] if depth == 0 else ""
        called_in_main = list(set(re.findall(r'\b([a-zA-Z_]\w*)\s*\(', main_body)))
        called_in_main = [c for c in called_in_main if c in defined]
    metrics["called_in_main"] = len(called_in_main)

    # 4. Connection point variables
    conn_vars = re.findall(r'^\s*([a-zA-Z_]\w*(?:_[xyz]|_offset|_pos|_attach|_z|_y|_x))\s*=', code, re.MULTILINE)
    metrics["connection_vars"] = len(conn_vars)

    # 5. Magic numbers in translate (numbers not from variables)
    translates = re.findall(r'translate\(\[([^\]]+)\]', code)
    magic_count = 0
    for t in translates:
        nums = re.findall(r'(?<![a-zA-Z_])\d+\.?\d*', t)
        # Allow 0, eps, and small constants
        magic_count += sum(1 for n in nums if float(n) > 1)
    metrics["magic_numbers_in_translate"] = magic_count

    # 6. Uncalled modules (defined but not called in main)
    uncalled = [m for m in defined if m not in called_in_main]
    metrics["uncalled_modules"] = len(uncalled)

    # 7. Has eps usage
    metrics["uses_eps"] = bool(re.search(r'\beps\b', code))

    # 8. Has TREE comment
    metrics["has_tree"] = bool(re.search(r'//\s*TREE:', code))

    # Log structured metrics
    issues = []
    if metrics["planned_parts"] > 0 and metrics["defined_modules"] < metrics["planned_parts"]:
        issues.append("parts_mismatch")
    if metrics["defined_modules"] > 0 and metrics["called_in_main"] < metrics["defined_modules"]:
        issues.append("uncalled_modules")
    if metrics["connection_vars"] == 0:
        issues.append("no_connections")
    if metrics["magic_numbers_in_translate"] > 3:
        issues.append("magic_numbers")
    if not metrics["uses_eps"]:
        issues.append("no_eps")

    metrics["issues"] = issues
    metrics["status"] = "PASS" if not issues else "WARN"

    logger.info(
        "[QUALITY] %s | planned=%d modules=%d called=%d conn=%d magic=%d tree=%s issues=%s",
        metrics["status"], metrics["planned_parts"], metrics["defined_modules"],
        metrics["called_in_main"], metrics["connection_vars"],
        metrics["magic_numbers_in_translate"], metrics["has_tree"], issues or "none",
    )

    return metrics


def _looks_like_openscad(text: str) -> bool:
    """Quick heuristic to detect raw OpenSCAD code without markdown fences."""
    return _score_openscad_code(text) >= 3


def _extract_openscad_from_text(text: str) -> str | None:
    """Extract OpenSCAD code from text response (CADAM's extractOpenSCADCodeFromText).

    Handles cases where the LLM outputs code directly instead of using tools.
    """
    if not text:
        return None

    # First try to extract from markdown code blocks
    best_code = None
    best_score = 0

    for match in re.finditer(r'```(?:openscad)?\s*\n?([\s\S]*?)\n?```', text):
        code = match.group(1).strip()
        score = _score_openscad_code(code)
        if score > best_score:
            best_score = score
            best_code = code

    # If we found code in a code block with a good score, return it
    if best_code and best_score >= 3:
        return best_code

    # If no code blocks, check if the entire text looks like OpenSCAD code
    raw_score = _score_openscad_code(text)
    if raw_score >= 5:  # Higher threshold for raw text
        return text.strip()

    return None


def _extract_from_text(text: str) -> dict:
    """Extract OpenSCAD code and metadata from LLM output via regex."""
    from app.cadquery.tasks import _clean_scad

    code_match = _CODE_BLOCK_RE.search(text)
    code = code_match.group(1).strip() if code_match else ""

    # Fallback: check for sentinel-delimited patched code from apply_parameter_changes
    if not code:
        patched_match = re.search(
            r"---PATCHED_CODE_START---\n(.*?)\n---PATCHED_CODE_END---",
            text, re.DOTALL,
        )
        if patched_match:
            code = patched_match.group(1).strip()

    # Fallback: try CADAM-style extraction (score-based)
    if not code:
        extracted = _extract_openscad_from_text(text)
        if extracted:
            code = extracted

    if code:
        code = _clean_scad(code)

    if code_match:
        message = text[: code_match.start()].strip()
        if not message:
            after = text[code_match.end() :].strip()
            message = after if after else ""
    else:
        message = "" if code else text.strip()

    # If we extracted code from raw text, clean the message
    if code and not code_match and message:
        # Remove the code from the text to get just the message
        cleaned = text.replace(code, "").strip()
        # Remove markdown code blocks
        cleaned = re.sub(r'```(?:openscad)?\s*\n?[\s\S]*?\n?```', '', cleaned).strip()
        message = cleaned if len(cleaned) >= 10 else ""

    if not message:
        message = "Model generated." if code else "Here to help!"

    if len(message) > 500:
        message = message[:500]

    parameters = _extract_parameters_from_code(code) if code else []
    model_type = "chat" if not code else "mechanical"

    return {
        "openscad_code": code,
        "parameters": parameters,
        "model_type": model_type,
        "message": message,
    }


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
) -> OpenSCADResponse:
    """Run agent and return structured OpenSCADResponse.

    Args:
        _extra_meta: Optional mutable dict populated with experiment + quality_metrics data.
    """
    t_start = time.monotonic()
    c = _get_components(provider, model, thinking)
    logger.info(f"[AGENT] Starting request: provider={c['provider']}, model={model}, thinking={thinking}")

    # Fetch RAG examples if DB session is available
    rag_context = ""
    if db:
        try:
            t_rag = time.monotonic()
            is_jewelry = _detect_jewelry(user_input)
            rag_context = await get_examples_for_prompt(db, user_input, is_jewelry=is_jewelry)
            logger.info(f"[AGENT][RAG] {time.monotonic() - t_rag:.2f}s — {len(rag_context)} chars, jewelry={is_jewelry}")
        except Exception as e:
            logger.warning(f"[AGENT][RAG] Failed to fetch examples: {e}")

    experiment_meta = None
    if image_data_urls:
        human_msg = _build_human_message(user_input, image_data_urls)
        prompt, experiment_meta = _build_code_prompt(user_input, rag_context)
        messages = [SystemMessage(content=prompt)] + list(history) + [human_msg]
        result = await c["code_llm"].ainvoke(messages)
        agent_output = _extract_text_content(result)
    elif c["tools_supported"] and c["llm_with_tools"] is not None:
        try:
            agent_output = await _run_tool_loop(
                user_input, history, settings.agent_max_iterations,
                c, rag_context=rag_context,
            )
        except Exception as e:
            logger.error(f"Tool loop failed, falling back to direct generation: {e}")
            msgs, experiment_meta = _build_direct_generation_messages(user_input, history, rag_context)
            result = await c["code_llm"].ainvoke(msgs)
            agent_output = _extract_text_content(result)
    else:
        msgs, experiment_meta = _build_direct_generation_messages(user_input, history, rag_context)
        result = await c["code_llm"].ainvoke(msgs)
        agent_output = _extract_text_content(result)

    t_total = time.monotonic() - t_start
    logger.info(f"[AGENT] Completed in {t_total:.2f}s — output {len(agent_output)} chars")

    data = _extract_from_text(agent_output)
    quality_metrics = None
    if data.get("openscad_code"):
        quality_metrics = _validate_code_quality(data["openscad_code"])

    # Populate extra metadata for caller (experiment + quality)
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
) -> AsyncGenerator[dict, None]:
    """
    LLM streaming with tool call support (CADAM pattern).

    Flow:
    1. Fetch RAG examples from DB (vector search) or web search
    2. Stream outer agent (AGENT_PROMPT) text tokens
    3. On build_parametric_model tool call → separate code gen with CODE_PROMPT + RAG
    4. On apply_parameter_changes tool call → regex patch current code
    5. Fallback: extract OpenSCAD from text if no tool was used

    Yields:
        {"type": "token", "text": "..."} for each text token
        {"type": "tool", "tool_name": ..., "status": ...} when a tool executes
        {"type": "done", "data": {...}} at the end with extracted structured data
    """
    t_start = time.monotonic()
    c = _get_components(provider, model, thinking)
    logger.info(f"[STREAM] Starting: provider={c['provider']}, model={model}, thinking={thinking}")

    # Fetch RAG context
    rag_context = ""
    if db:
        try:
            t_rag = time.monotonic()
            is_jewelry = _detect_jewelry(user_input)
            rag_context = await get_examples_for_prompt(db, user_input, is_jewelry=is_jewelry)
            logger.info(f"[STREAM][RAG] {time.monotonic() - t_rag:.2f}s — {len(rag_context)} chars, jewelry={is_jewelry}")
        except Exception as e:
            logger.warning(f"[STREAM][RAG] Failed: {e}")

    human_msg = _build_human_message(user_input, image_data_urls)

    use_tools = (
        not image_data_urls
        and c["tools_supported"]
        and c["llm_with_tools"] is not None
    )

    # Use AGENT_PROMPT for tool mode, CODE_PROMPT (+ domain + RAG) for direct generation
    experiment_meta = None
    if use_tools:
        system_prompt = AGENT_PROMPT
    else:
        system_prompt, experiment_meta = _build_code_prompt(user_input, rag_context)
    messages = [SystemMessage(content=system_prompt)] + list(history) + [human_msg]

    # Mutable dict for code-gen calls to write experiment metadata into
    _exp_meta_out: dict = {}

    full_text = ""
    agent_text = ""  # Text from outer agent (shown in chat)
    code_text = ""   # Code from dedicated code gen call

    for _iteration in range(settings.agent_max_iterations):
        collected_response = None

        if use_tools:
            stream_source = c["llm_with_tools"].astream(messages)
        elif image_data_urls:
            stream_source = c["code_llm"].astream(messages)
        else:
            msgs, _direct_exp_meta = _build_direct_generation_messages(user_input, history, rag_context)
            if _direct_exp_meta:
                _exp_meta_out.update(_direct_exp_meta)
            stream_source = c["code_llm"].astream(msgs)

        try:
            async for chunk in stream_source:
                if use_tools:
                    collected_response = chunk if collected_response is None else collected_response + chunk

                token = _extract_text_content(chunk)
                if token:
                    full_text += token
                    agent_text += token
                    yield {"type": "token", "text": token}
        except Exception as e:
            logger.error(f"Streaming generation failed: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}
            return

        # Check for tool calls
        if (
            use_tools
            and collected_response
            and hasattr(collected_response, "tool_calls")
            and collected_response.tool_calls
        ):
            for tool_call in collected_response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                if tool_name == "build_parametric_model":
                    # CADAM pattern: separate code generation call with RAG context
                    yield {"type": "tool", "tool_name": tool_name, "status": "generating"}

                    try:
                        code_tokens = []
                        async for token in _generate_code_stream(tool_args, history, c, user_input, rag_context, _experiment_meta_out=_exp_meta_out):
                            code_tokens.append(token)
                            yield {"type": "token", "text": token}

                        code_text = "".join(code_tokens)
                        code_text = _strip_code_fences(code_text)
                        full_text = code_text  # Code is the final output
                        yield {"type": "tool", "tool_name": tool_name, "status": "completed"}
                    except Exception as e:
                        logger.error(f"Code generation failed: {e}", exc_info=True)
                        yield {"type": "tool", "tool_name": tool_name, "status": "error"}
                        yield {"type": "error", "message": str(e)}
                        return

                elif tool_name == "apply_parameter_changes":
                    yield {"type": "tool", "tool_name": tool_name, "status": "patching"}

                    patched = _handle_parameter_changes(tool_args, history)
                    if patched and _looks_like_openscad(patched):
                        code_text = patched
                        full_text = patched
                        yield {"type": "token", "text": patched}
                        yield {"type": "tool", "tool_name": tool_name, "status": "completed"}
                    else:
                        yield {"type": "tool", "tool_name": tool_name, "status": "error"}
                        yield {"type": "token", "text": patched}
                        full_text = patched

                else:
                    # Fallback: execute other tools normally (e.g. search_reference_images)
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
                        continue

            # Don't loop after intercepted tools — we have the result
            break
        else:
            break

    # Fallback: if full_text contains OpenSCAD code but wasn't extracted via tools
    if not code_text and full_text:
        extracted = _extract_openscad_from_text(full_text)
        if extracted:
            logger.info("Fallback: Extracted OpenSCAD code from text response")
            code_text = extracted

    logger.info(f"[STREAM] Complete, text_length={len(full_text)}, code_length={len(code_text)}")

    # Merge experiment metadata from all sources
    final_experiment_meta = experiment_meta or _exp_meta_out or None

    # Build final response
    if code_text:
        from app.cadquery.tasks import _clean_scad
        code_text = _clean_scad(code_text)
        quality_metrics = _validate_code_quality(code_text)
        parameters = _extract_parameters_from_code(code_text)
        # Strip code blocks from agent text to get clean conversational message
        msg = re.sub(r'```[\s\S]*?```', '', agent_text).strip()
        if not msg:
            msg = "Model generated."
        if len(msg) > 500:
            msg = msg[:500]
        response = {
            "openscad_code": code_text,
            "parameters": parameters,
            "model_type": "mechanical",
            "message": msg,
        }
    else:
        response = _extract_from_text(full_text)
        quality_metrics = None
        if response.get("openscad_code"):
            quality_metrics = _validate_code_quality(response["openscad_code"])

    # Attach experiment + quality data to response for persistence
    if final_experiment_meta:
        response["experiment"] = final_experiment_meta
    if quality_metrics:
        response["quality_metrics"] = quality_metrics

    t_total = time.monotonic() - t_start
    logger.info(f"[STREAM] Completed in {t_total:.2f}s — {len(full_text)} chars generated")
    yield {"type": "done", "data": response}

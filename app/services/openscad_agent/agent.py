import logging
import re
from collections.abc import AsyncGenerator
from functools import lru_cache

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.services.openscad_agent.llm_provider import get_llm
from app.services.openscad_agent.prompts import AGENT_PROMPT, CODE_PROMPT
from app.services.openscad_agent.tools import (
    apply_parameter_changes,
    build_parametric_model,
)
from app.services.openscad_agent.tools.parameter_patcher import patch_code
from app.schemas.openscad import OpenSCADResponse, OpenSCADParameter
from app.core.config import settings

logger = logging.getLogger(__name__)

# Tools — only the two that matter
_tools = [apply_parameter_changes, build_parametric_model]
_tools_by_name = {t.name: t for t in _tools}


# ── Cached component factory ─────────────────────────────────────────────────

@lru_cache(maxsize=16)
def _get_components(provider: str | None = None, model: str | None = None, thinking: bool = False):
    """Lazily create and cache LLM + chains for a given provider/model pair."""
    llm = get_llm(provider, model, thinking)
    resolved_provider = (provider or settings.llm_provider).lower()

    tools_supported = resolved_provider not in ("groq",)
    llm_with_tools = llm.bind_tools(_tools) if tools_supported else None

    return {
        "llm": llm,
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


def _build_direct_generation_messages(
    user_input: str,
    history: list[HumanMessage | AIMessage],
) -> list:
    """Build messages for direct code generation (no tools, uses CODE_PROMPT)."""
    return [SystemMessage(content=CODE_PROMPT)] + list(history) + [HumanMessage(content=user_input)]


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
) -> str:
    """Make a separate LLM call with CODE_PROMPT to generate OpenSCAD code.

    Matches CADAM's handleToolCall pattern:
    - System: STRICT_CODE_PROMPT
    - ...full conversation history (history already excludes current user msg)
    - (optional) Assistant: baseCode
    - User: original user request (+ error context if present)
    """
    base_code = tool_args.get("baseCode", "")
    error = tool_args.get("error", "")

    # Build code generation messages (CADAM pattern)
    code_messages: list = [SystemMessage(content=CODE_PROMPT)]
    code_messages.extend(history)

    # Add base code context if modifying existing model
    if base_code:
        code_messages.append(AIMessage(content=base_code))

    # Always use the ORIGINAL user message, not the tool's paraphrased text
    user_text = user_input
    if error:
        user_text = f"{user_input}\n\nFix this OpenSCAD error: {error}"

    code_messages.append(HumanMessage(content=user_text))

    llm = components["llm"]
    result = await llm.ainvoke(code_messages)
    code = _extract_text_content(result)

    # Strip markdown fences if present
    code = _strip_code_fences(code)

    return code


async def _generate_code_stream(
    tool_args: dict,
    history: list[HumanMessage | AIMessage],
    components: dict,
    user_input: str = "",
) -> AsyncGenerator[str, None]:
    """Stream code generation tokens from a separate LLM call with CODE_PROMPT.

    Matches CADAM's pattern: uses original user_input, not tool's paraphrased text.
    """
    base_code = tool_args.get("baseCode", "")
    error = tool_args.get("error", "")

    code_messages: list = [SystemMessage(content=CODE_PROMPT)]
    code_messages.extend(history)

    if base_code:
        code_messages.append(AIMessage(content=base_code))

    # Always use original user message
    user_text = user_input
    if error:
        user_text = f"{user_input}\n\nFix this OpenSCAD error: {error}"

    code_messages.append(HumanMessage(content=user_text))

    llm = components["llm"]
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
) -> str:
    """
    Manual tool-calling loop matching CADAM's pattern:
    - Outer agent uses AGENT_PROMPT with tools
    - build_parametric_model → separate LLM call with CODE_PROMPT
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
                # CADAM pattern: separate LLM call with CODE_PROMPT
                code = await _generate_code(tool_args, history, components, user_input)
                logger.info(f"Code generation returned {len(code)} chars")
                return code

            elif tool_name == "apply_parameter_changes":
                # CADAM pattern: regex patching of current artifact code
                result = _handle_parameter_changes(tool_args, history)
                return result

            else:
                # Fallback: execute tool normally
                tool_fn = _tools_by_name.get(tool_name)
                if tool_fn is None:
                    tool_result = f"Unknown tool: {tool_name}"
                else:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as e:
                        tool_result = f"Tool error: {e}"

                logger.info(f"Tool {tool_name} returned: {tool_result[:200]}")
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

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
) -> OpenSCADResponse:
    """Run agent and return structured OpenSCADResponse."""
    c = _get_components(provider, model, thinking)

    if image_data_urls:
        human_msg = _build_human_message(user_input, image_data_urls)
        messages = [SystemMessage(content=CODE_PROMPT)] + list(history) + [human_msg]
        result = await c["llm"].ainvoke(messages)
        agent_output = _extract_text_content(result)
    elif c["tools_supported"] and c["llm_with_tools"] is not None:
        try:
            agent_output = await _run_tool_loop(
                user_input, history, settings.agent_max_iterations,
                c,
            )
        except Exception as e:
            logger.error(f"Tool loop failed, falling back to direct generation: {e}")
            msgs = _build_direct_generation_messages(user_input, history)
            result = await c["llm"].ainvoke(msgs)
            agent_output = _extract_text_content(result)
    else:
        msgs = _build_direct_generation_messages(user_input, history)
        result = await c["llm"].ainvoke(msgs)
        agent_output = _extract_text_content(result)

    logger.info(f"Agent output (first 300 chars): {agent_output[:300]}")

    data = _extract_from_text(agent_output)
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
) -> AsyncGenerator[dict, None]:
    """
    LLM streaming with tool call support (CADAM pattern).

    Flow:
    1. Stream outer agent (AGENT_PROMPT) text tokens
    2. On build_parametric_model tool call → separate code gen with CODE_PROMPT
    3. On apply_parameter_changes tool call → regex patch current code
    4. Fallback: extract OpenSCAD from text if no tool was used

    Yields:
        {"type": "token", "text": "..."} for each text token
        {"type": "tool", "tool_name": ..., "status": ...} when a tool executes
        {"type": "done", "data": {...}} at the end with extracted structured data
    """
    c = _get_components(provider, model, thinking)
    logger.info(f"[STREAM] provider={c['provider']}, model={model}, thinking={thinking}")

    human_msg = _build_human_message(user_input, image_data_urls)

    use_tools = (
        not image_data_urls
        and c["tools_supported"]
        and c["llm_with_tools"] is not None
    )

    # Use AGENT_PROMPT for tool mode, CODE_PROMPT for direct generation
    system_prompt = AGENT_PROMPT if use_tools else CODE_PROMPT
    messages = [SystemMessage(content=system_prompt)] + list(history) + [human_msg]

    full_text = ""
    agent_text = ""  # Text from outer agent (shown in chat)
    code_text = ""   # Code from dedicated code gen call

    for _iteration in range(settings.agent_max_iterations):
        collected_response = None

        if use_tools:
            stream_source = c["llm_with_tools"].astream(messages)
        elif image_data_urls:
            stream_source = c["llm"].astream(messages)
        else:
            msgs = _build_direct_generation_messages(user_input, history)
            stream_source = c["llm"].astream(msgs)

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
                    # CADAM pattern: separate code generation call
                    yield {"type": "tool", "tool_name": tool_name, "status": "generating"}

                    try:
                        code_tokens = []
                        async for token in _generate_code_stream(tool_args, history, c, user_input):
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
                    # Fallback: execute unknown tools normally
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
                        messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))
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

    # Build final response
    if code_text:
        from app.cadquery.tasks import _clean_scad
        code_text = _clean_scad(code_text)
        parameters = _extract_parameters_from_code(code_text)
        # Use agent_text as message if we have it, otherwise generic
        msg = agent_text.strip() if agent_text.strip() and agent_text.strip() != code_text.strip() else "Model generated."
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

    yield {"type": "done", "data": response}

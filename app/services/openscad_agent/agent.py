import logging
import re
from collections.abc import AsyncGenerator
from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.services.openscad_agent.llm_provider import get_llm
from app.services.openscad_agent.prompts import AGENT_PROMPT, CODE_PROMPT
from app.services.openscad_agent.tools import (
    apply_parameter_changes,
    build_parametric_model,
)
from app.schemas.openscad import OpenSCADResponse, OpenSCADParameter
from app.core.config import settings

logger = logging.getLogger(__name__)

# Tools — only the two that matter
_tools = [apply_parameter_changes, build_parametric_model]
_tools_by_name = {t.name: t for t in _tools}

# Code generation prompt template (direct generation, no tools)
_generate_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", CODE_PROMPT),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{input}"),
    ]
)


# ── Cached component factory ─────────────────────────────────────────────────

@lru_cache(maxsize=16)
def _get_components(provider: str | None = None, model: str | None = None, thinking: bool = False):
    """Lazily create and cache LLM + chains for a given provider/model pair."""
    llm = get_llm(provider, model, thinking)
    resolved_provider = (provider or settings.llm_provider).lower()

    generate_chain = _generate_prompt | llm

    tools_supported = resolved_provider not in ("groq",)
    llm_with_tools = llm.bind_tools(_tools) if tools_supported else None

    return {
        "llm": llm,
        "generate_chain": generate_chain,
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


async def _run_tool_loop(
    user_input: str,
    history: list[HumanMessage | AIMessage],
    max_iterations: int,
    llm_with_tools,
    llm,
    image_data_urls: list[str] | None = None,
) -> str:
    """
    Manual tool-calling loop. Calls LLM, executes tool calls, feeds results back.
    Returns the final text response.
    """
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

    # Fallback: if no code blocks found, check if the text itself is raw OpenSCAD
    if not code and _looks_like_openscad(text):
        code = text.strip()

    if code:
        code = _clean_scad(code)

    if code_match:
        message = text[: code_match.start()].strip()
        if not message:
            after = text[code_match.end() :].strip()
            message = after if after else ""
    else:
        message = text.strip() if not code else ""

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


def _looks_like_openscad(text: str) -> bool:
    """Quick heuristic to detect raw OpenSCAD code without markdown fences."""
    if not text or len(text) < 20:
        return False
    score = 0
    patterns = [
        r'\b(cube|sphere|cylinder|polyhedron)\s*\(',
        r'\b(union|difference|intersection)\s*\(\s*\)',
        r'\b(translate|rotate|scale)\s*\(',
        r'\b(linear_extrude|rotate_extrude)\s*\(',
        r'\bmodule\s+\w+\s*\(',
        r'\$fn\s*=',
    ]
    for p in patterns:
        if re.search(p, text):
            score += 1
    return score >= 3


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
                c["llm_with_tools"], c["llm"],
            )
        except Exception as e:
            logger.error(f"Tool loop failed, falling back to direct generation: {e}")
            result = await c["generate_chain"].ainvoke(
                {"input": user_input, "history": history}
            )
            agent_output = _extract_text_content(result)
    else:
        result = await c["generate_chain"].ainvoke(
            {"input": user_input, "history": history}
        )
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
    LLM streaming with tool call support.
    Yields: {"type": "token", "text": "..."} for each text token
    Yields: {"type": "tool", "tool_name": ..., "status": ...} when a tool executes
    Yields: {"type": "done", "data": {...}} at the end with extracted structured data
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

    for _iteration in range(settings.agent_max_iterations):
        collected_response = None

        if use_tools:
            stream_source = c["llm_with_tools"].astream(messages)
        elif image_data_urls:
            stream_source = c["llm"].astream(messages)
        else:
            stream_source = c["generate_chain"].astream(
                {"input": user_input, "history": history}
            )

        try:
            async for chunk in stream_source:
                if use_tools:
                    collected_response = chunk if collected_response is None else collected_response + chunk

                token = _extract_text_content(chunk)
                if token:
                    full_text += token
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
            messages.append(collected_response)
            for tool_call in collected_response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_id = tool_call.get("id", tool_name)
                tool_fn = _tools_by_name.get(tool_name)

                if tool_fn is None:
                    tool_result = f"Unknown tool: {tool_name}"
                else:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as e:
                        tool_result = f"Tool error: {e}"

                logger.info(f"[STREAM] Tool {tool_name} returned: {str(tool_result)[:200]}")
                yield {"type": "tool", "tool_name": tool_name, "status": "completed"}
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))
            # Loop to get LLM's next response after tool execution
            continue
        else:
            break

    logger.info(f"[STREAM] Complete, length={len(full_text)}")

    response = _extract_from_text(full_text)
    yield {"type": "done", "data": response}

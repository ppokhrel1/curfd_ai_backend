import json
import logging

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.services.openscad_agent.llm_provider import get_llm
from app.services.openscad_agent.prompts import SYSTEM_PROMPT
from app.services.openscad_agent.tools import (
    validate_openscad_code,
    analyze_openscad_parameters,
    search_openscad_reference,
)
from app.schemas.openscad import OpenSCADResponse
from app.core.config import settings

logger = logging.getLogger(__name__)

# Build components once at module level
_llm = get_llm()
_tools = [validate_openscad_code, analyze_openscad_parameters, search_openscad_reference]
_tools_by_name = {t.name: t for t in _tools}

# LLM with tools bound (replaces create_tool_calling_agent)
_llm_with_tools = _llm.bind_tools(_tools)

# Structured output LLM for extraction step
_structured_llm = _llm.with_structured_output(OpenSCADResponse)

_extraction_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Extract the final OpenSCAD response from the assistant's message. "
            "Return a JSON object with: openscad_code, parameters, model_type, message.\n\n"
            "Rules:\n"
            "- openscad_code: The FINAL complete version of the OpenSCAD code (after any error fixes). "
            "Include the full script, not a summary.\n"
            "- parameters: A list of objects with name, min_val, max_val, default_val, description. "
            "Extract these from the top-level variables in the code. Use the parameter analysis "
            "results if available for ranges, otherwise infer sensible ranges.\n"
            "- model_type: Category like 'mechanical', 'organic', 'architectural', or 'chat' for non-code replies.\n"
            "- message: A brief 1-2 sentence summary of what was built and its key features. "
            "Mention the main components (e.g. 'Created a spur gear with 20 teeth, a center bore, and keyway').\n"
            "- If the response was conversational (no code), set openscad_code to empty string "
            "and model_type to 'chat'.",
        ),
        ("human", "{agent_output}"),
    ]
)

_extraction_chain = _extraction_prompt | _structured_llm


async def _run_tool_loop(
    user_input: str,
    history: list[HumanMessage | AIMessage],
    max_iterations: int,
) -> str:
    """
    Manual tool-calling loop using llm.bind_tools().
    Calls the LLM, executes any tool calls, feeds results back, repeats.
    Returns the final text response from the LLM.
    """
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(history) + [HumanMessage(content=user_input)]

    for _ in range(max_iterations):
        response = await _llm_with_tools.ainvoke(messages)
        messages.append(response)

        # If no tool calls, we're done
        if not response.tool_calls:
            return response.content or ""

        # Execute each tool call and append results
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

    # Max iterations reached — get a final response without tools
    response = await _llm.ainvoke(messages)
    return response.content or ""


async def run_agent(
    user_input: str,
    history: list[HumanMessage | AIMessage],
) -> OpenSCADResponse:
    """
    Two-step agent invocation:
    1. Agent reasons with tools (validation, parameter analysis, doc search)
    2. Structured output extraction from the agent's final answer
    """
    # Step 1: Agent execution with tools
    try:
        agent_output = await _run_tool_loop(
            user_input, history, settings.agent_max_iterations
        )
    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        # Fallback: run without tools
        fallback_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", SYSTEM_PROMPT),
                    MessagesPlaceholder(variable_name="history"),
                    ("human", "{input}"),
                ]
            )
            | _structured_llm
        )
        return await fallback_chain.ainvoke(
            {"input": user_input, "history": history}
        )

    # Step 2: Extract structured output
    try:
        response: OpenSCADResponse = await _extraction_chain.ainvoke(
            {"agent_output": agent_output}
        )
        return response
    except Exception as e:
        logger.error(f"Structured extraction failed: {e}", exc_info=True)
        try:
            data = json.loads(agent_output)
            return OpenSCADResponse(**data)
        except (json.JSONDecodeError, Exception):
            return OpenSCADResponse(
                openscad_code="",
                parameters=[],
                model_type="chat",
                message=agent_output[:500],
            )

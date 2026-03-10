import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.openscad_agent.llm_provider import get_llm

logger = logging.getLogger(__name__)

_CREATIVE_SYSTEM = (
    "You generate creative prompts for 3D-printable OpenSCAD models.\n"
    "Focus on organic shapes, figurines, architectural features, toys, gadgets, and decorative objects.\n"
    "Be short and specific. Include interesting geometric features.\n"
    "Return ONLY the prompt text — no preamble, no markdown, no quotes."
)

_PARAMETRIC_SYSTEM = (
    "You generate prompts for parametric OpenSCAD models — functional parts and household objects.\n"
    "Include specific dimensions (mm), hole counts, angles, wall thicknesses.\n"
    "Think brackets, enclosures, gears, organizers, stands, clips.\n"
    "Return ONLY the prompt text — no preamble, no markdown, no quotes."
)

_ENHANCE_CREATIVE_SYSTEM = (
    "You enhance 3D modeling prompts to be more creative and detailed.\n"
    "Add artistic styling, sculptural details, and visual flair while keeping the original intent.\n"
    "Return ONLY the enhanced prompt text — no preamble, no markdown, no quotes."
)

_ENHANCE_PARAMETRIC_SYSTEM = (
    "You enhance parametric 3D modeling prompts to be more functional and precise.\n"
    "Add specific dimensions (mm), tolerances, hole sizes, wall thicknesses, and practical features.\n"
    "Return ONLY the enhanced prompt text — no preamble, no markdown, no quotes."
)


async def suggest_prompt(
    prompt_type: str,
    existing_text: str | None = None,
) -> str:
    llm = get_llm(thinking=False)

    if existing_text:
        system = _ENHANCE_CREATIVE_SYSTEM if prompt_type == "creative" else _ENHANCE_PARAMETRIC_SYSTEM
        user_msg = f"Enhance this prompt:\n{existing_text}"
    else:
        system = _CREATIVE_SYSTEM if prompt_type == "creative" else _PARAMETRIC_SYSTEM
        user_msg = "Generate a single 3D modeling prompt."

    messages = [SystemMessage(content=system), HumanMessage(content=user_msg)]
    response = await llm.ainvoke(messages)

    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        parts = [
            b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
            else (b if isinstance(b, str) else "")
            for b in content
        ]
        content = "".join(parts)

    return (content or "").strip().strip('"').strip("'")

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.openscad_agent.llm_provider import get_llm

logger = logging.getLogger(__name__)

# Matches CADAM's PROMPT_SYSTEM_PROMPT
_CREATIVE_SYSTEM = """You are a helpful assistant that generates creative prompts for organic 3D forms and artistic objects. Your prompts should be:
1. Focus on organic shapes, characters, figurines, and artistic forms
2. Be short and creative
3. Avoid technical dimensions - focus on form and aesthetics
4. Think sculptures, characters, animals, artistic objects
5. Return ONLY the prompt text without any introductory phrases or quotes

Here are some examples:

User: "Generate a creative prompt for a 3D form."
Assistant: "a table top figurine of sonic the hedgehog"
User: "Generate a creative prompt for a 3D form."
Assistant: "a dragon sculpture with spread wings"
User: "Generate a creative prompt for a 3D form."
Assistant: "a decorative elephant statue"
User: "Generate a creative prompt for a 3D form."
Assistant: "a cartoon character bust of mario"
User: "Generate a creative prompt for a 3D form."
Assistant: "a stylized tree with twisted branches"
User: "Generate a creative prompt for a 3D form."
Assistant: "a miniature castle with towers"
"""

# Matches CADAM's PARAMETRIC_SYSTEM_PROMPT
_PARAMETRIC_SYSTEM = """You are a helpful assistant that generates prompts for dimensional household objects and functional parts. Your prompts should be:
1. Focus on practical household items and functional parts
2. Include specific dimensions when relevant
3. Be concise and practical
4. Think containers, holders, brackets, everyday objects
5. Return ONLY the prompt text without any introductory phrases or quotes

Here are some examples:

User: "Generate a parametric modeling prompt."
Assistant: "a plant pot with 4 drainage holes and a 30mm diameter"
User: "Generate a parametric modeling prompt."
Assistant: "a phone stand with 15 degree angle and cable slot"
User: "Generate a parametric modeling prompt."
Assistant: "a pen holder cup 80mm diameter with pencil slots"
User: "Generate a parametric modeling prompt."
Assistant: "a wall bracket 120mm wide with two 6mm screw holes"
User: "Generate a parametric modeling prompt."
Assistant: "a drawer organizer tray 200x100mm with compartments"
User: "Generate a parametric modeling prompt."
Assistant: "a cable management clip for 8mm cables"
"""

# Matches CADAM's parametric augmentation prompt
_ENHANCE_PARAMETRIC_SYSTEM = """You are a technical writing assistant specialized in enhancing prompts for dimensional household objects and functional parts. When given an existing prompt, you should:

1. Add specific dimensions (in mm) where practical and missing
2. Include functional details like holes, slots, angles, or compartments
3. Focus on practical household use cases and functionality
4. Make it more precise for creating useful everyday objects
5. Maintain the original intent and core concept
6. Keep it concise and practical
7. Return ONLY the enhanced prompt text without any introductory phrases, explanations, or quotes

The enhanced prompt should be more functional and dimensional while staying true to the user's vision."""

# Matches CADAM's creative augmentation prompt
_ENHANCE_CREATIVE_SYSTEM = """You are a creative writing assistant specialized in enhancing prompts for 3D game assets and 3D printable characters. When given an existing prompt, you should:

1. Expand with more vivid artistic and organic details
2. Add character traits, poses, or artistic styling
3. Include sculptural or decorative elements
4. Focus on form, aesthetics, and visual appeal
5. Maintain the original intent and core concept
6. Make it more engaging and visually interesting
7. Return ONLY the enhanced prompt text without any introductory phrases, explanations, or quotes

The enhanced prompt should be more artistic and visually compelling while staying true to the user's vision."""


async def suggest_prompt(
    prompt_type: str,
    existing_text: str | None = None,
) -> str:
    llm = get_llm(thinking=False)

    if existing_text and existing_text.strip():
        # Augment existing text
        if prompt_type == "parametric":
            system = _ENHANCE_PARAMETRIC_SYSTEM
            user_msg = (
                f"Please enhance and expand this household object prompt to make it "
                f"more functional, dimensional, and practical for everyday use:\n\n"
                f"{existing_text}\n\n"
                f"Return only the enhanced prompt text, no introductory phrases."
            )
        else:
            system = _ENHANCE_CREATIVE_SYSTEM
            user_msg = (
                f"Please enhance and expand this artistic 3D form prompt to make it "
                f"more detailed, creative, and visually compelling:\n\n"
                f"{existing_text}\n\n"
                f"Return only the enhanced prompt text, no introductory phrases."
            )
    else:
        # Generate new prompt
        if prompt_type == "parametric":
            system = _PARAMETRIC_SYSTEM
            user_msg = "Generate a parametric modeling prompt."
        else:
            system = _CREATIVE_SYSTEM
            user_msg = "Generate a creative prompt for a 3D form."

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

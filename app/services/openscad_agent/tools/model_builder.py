import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def build_parametric_model(
    text: str,
    baseCode: str = "",
    error: str = "",
) -> str:
    """Generate or update an OpenSCAD model from user intent and context.
    Include parameters and ensure the model is manifold and 3D-printable.

    Use this when:
    - The user requests a NEW model
    - The user wants STRUCTURAL changes (new components, different topology)
    - Parameter patching is not sufficient

    Args:
        text: User request for the model.
        baseCode: Existing code to modify (if any).
        error: Error to fix (if any).

    Returns:
        Instruction to proceed with code generation.
    """
    # This tool is intercepted by the agent loop which makes a separate
    # LLM call with CODE_PROMPT. This return value is only a fallback.
    return (
        f"PROCEED WITH FULL CODE GENERATION.\n"
        f"Model: {text}\n"
        f"Requirements: {baseCode or 'new model'}\n"
        f"Generate the complete OpenSCAD code in your response."
    )

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def build_parametric_model(
    description: str,
    requirements: str,
) -> str:
    """Signal that a full parametric model generation is needed.
    Use this when:
    - The user requests a NEW model
    - The user wants STRUCTURAL changes (new components, different topology)
    - Parameter patching is not sufficient

    After calling this tool, generate the complete OpenSCAD code in your response.

    Args:
        description: Brief description of the model to build.
        requirements: Key requirements and constraints.

    Returns:
        Instruction to proceed with full code generation.
    """
    return (
        f"PROCEED WITH FULL CODE GENERATION.\n"
        f"Model: {description}\n"
        f"Requirements: {requirements}\n"
        f"Generate the complete OpenSCAD code in your response."
    )

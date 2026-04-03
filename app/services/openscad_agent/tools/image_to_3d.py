from langchain_core.tools import tool


@tool
def generate_3d_from_image(image_query: str, prompt: str = "") -> str:
    """Generate a 3D model from a reference image using AI image-to-3D generation.

    Use this when the user wants a realistic 3D mesh from an image,
    not parametric CAD code. The system will search for a reference image,
    then send it to a 3D generation service.

    Args:
        image_query: Search query to find a reference image (e.g., "dragon sculpture", "Naruto kunai")
        prompt: Optional text description to guide 3D generation

    Returns:
        A trigger string that the agent loop intercepts to start the RunPod job.
    """
    return f"TRIGGER_IMAGE_TO_3D|image_query={image_query}|prompt={prompt}"

"""Gemini-powered image generation + editing tools for the langchain agent.

Uses the `google-genai` SDK with `gemini-2.5-flash-image-preview`, which is
the only Gemini model that returns inline image bytes (Imagen 3 is text-only
in the chat path). Supports both:
  - Pure text → image (generate_image)
  - Reference image + text → image (edit_image)

Tool results are formatted with the `[IMAGE_DATA_URL]…[/IMAGE_DATA_URL]`
sentinel so the existing multimodal handoff in the agent loop feeds the
result back into the LLM as a ToolMessage with image_url content. The
agent's stream wrapper additionally emits an `image.generated` event so
the chat UI can render the image inline.
"""

from __future__ import annotations

import base64
import io
import logging
import os
from typing import Optional

import httpx
from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

# Single image-capable Gemini model. If Google ships a newer one, swap here.
_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image-preview")


def _client():
    """Lazy `google.genai` client init — raises a clean error if key missing."""
    api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Gemini API key not configured (settings.gemini_api_key / GEMINI_API_KEY)."
        )
    from google import genai
    return genai.Client(api_key=api_key)


def _extract_image_bytes(response) -> tuple[bytes, str] | None:
    """Pull (image_bytes, media_type) out of a genai response.

    The image is delivered as an inline_data part on the first candidate.
    """
    try:
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    media_type = getattr(inline, "mime_type", "image/png") or "image/png"
                    data = inline.data
                    # google-genai returns raw bytes; older builds returned base64 str.
                    if isinstance(data, str):
                        data = base64.b64decode(data)
                    return data, media_type
    except Exception as e:
        logger.warning(f"[image_gen] Failed to extract image bytes: {e}")
    return None


def _bytes_to_data_url(image_bytes: bytes, media_type: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{media_type};base64,{b64}"


def _data_url_to_pil(data_url: str):
    """Decode a `data:image/...;base64,...` URL into a PIL Image."""
    from PIL import Image
    if not data_url.startswith("data:"):
        raise ValueError("expected a data: URL")
    _, payload = data_url.split(",", 1)
    return Image.open(io.BytesIO(base64.b64decode(payload)))


def _fetch_url_to_pil(url: str, timeout: float = 15.0):
    """GET an http(s) URL and return a PIL Image."""
    from PIL import Image
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content))


def _load_reference_image(image_url: str):
    """Accept either a data URL or http(s) URL and return a PIL.Image."""
    if image_url.startswith("data:"):
        return _data_url_to_pil(image_url)
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return _fetch_url_to_pil(image_url)
    raise ValueError(f"Unsupported image_url scheme: {image_url[:30]}…")


def _format_tool_result(prompt: str, image_bytes: bytes, media_type: str, action: str) -> str:
    """Wrap result so the agent loop's image-extraction regex picks it up."""
    data_url = _bytes_to_data_url(image_bytes, media_type)
    return (
        f"{action.capitalize()}d image for prompt: {prompt!r}.\n"
        f"[IMAGE_DATA_URL]{data_url}[/IMAGE_DATA_URL]"
    )


@tool
def generate_image(prompt: str) -> str:
    """Generate a brand-new image from a text description using Gemini.

    Use when the user asks for an image, illustration, photo, mock-up, or
    any other visual that does NOT yet exist. For modifying an existing
    image, use `edit_image` instead.

    Args:
        prompt: Detailed visual description of the image to create.

    Returns:
        A short caption plus an `[IMAGE_DATA_URL]…[/IMAGE_DATA_URL]`
        block carrying a base64 data URL of the generated PNG.
    """
    if not prompt or not prompt.strip():
        return "generate_image error: prompt is empty."
    try:
        client = _client()
        response = client.models.generate_content(
            model=_IMAGE_MODEL,
            contents=prompt,
        )
    except Exception as e:
        logger.error(f"[image_gen] Gemini generate_content failed: {e}")
        return f"Image generation failed: {e}"

    extracted = _extract_image_bytes(response)
    if extracted is None:
        text = getattr(response, "text", None) or "no text"
        logger.warning(f"[image_gen] No image in response. Text={text[:200]}")
        return f"Gemini returned no image. Text response: {text[:300]}"

    image_bytes, media_type = extracted
    logger.info(
        f"[image_gen] generate_image OK ({len(image_bytes)} bytes, {media_type}) "
        f"prompt={prompt[:80]!r}"
    )
    return _format_tool_result(prompt, image_bytes, media_type, "generate")


@tool
def edit_image(image_url: str, prompt: str) -> str:
    """Modify an existing image using Gemini multimodal editing.

    Use when the user asks to change, restyle, add to, or remove from an
    image they already have. Pass the source image as either a data URL
    (`data:image/...;base64,...`) or an http(s) URL.

    Args:
        image_url: Source image (data: URL or http(s) URL).
        prompt: Description of the change to apply
                (e.g. "make it night-time", "remove the background",
                "add a red ribbon to the box").

    Returns:
        A short caption plus an `[IMAGE_DATA_URL]…[/IMAGE_DATA_URL]`
        block carrying the edited image as a base64 data URL.
    """
    if not prompt or not prompt.strip():
        return "edit_image error: prompt is empty."
    if not image_url or not image_url.strip():
        return "edit_image error: image_url is empty."
    try:
        ref_image = _load_reference_image(image_url)
    except Exception as e:
        return f"edit_image error: could not load source image: {e}"

    try:
        client = _client()
        response = client.models.generate_content(
            model=_IMAGE_MODEL,
            contents=[prompt, ref_image],
        )
    except Exception as e:
        logger.error(f"[image_gen] Gemini edit failed: {e}")
        return f"Image edit failed: {e}"

    extracted = _extract_image_bytes(response)
    if extracted is None:
        text = getattr(response, "text", None) or "no text"
        logger.warning(f"[image_gen] No image in edit response. Text={text[:200]}")
        return f"Gemini returned no edited image. Text response: {text[:300]}"

    image_bytes, media_type = extracted
    logger.info(
        f"[image_gen] edit_image OK ({len(image_bytes)} bytes, {media_type}) "
        f"prompt={prompt[:80]!r}"
    )
    return _format_tool_result(prompt, image_bytes, media_type, "edit")

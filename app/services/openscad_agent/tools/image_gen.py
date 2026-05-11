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
import uuid
from typing import Optional

import httpx
from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

# Image-capable Gemini models. Naming has churned: the original "nano
# banana" preview was `gemini-2.5-flash-image-preview`; some accounts /
# regions / API versions only expose the GA name `gemini-2.5-flash-image`,
# and the older v2 preview is still common. We try the user-configured one
# first, then fall back to known siblings on 404 so a regional naming
# change doesn't break the tool. Override with GEMINI_IMAGE_MODEL.
_DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
_PRIMARY_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", _DEFAULT_IMAGE_MODEL)
_IMAGE_MODEL_FALLBACKS = [
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
    "gemini-2.0-flash-preview-image-generation",
    "gemini-2.0-flash-exp-image-generation",
]


def _candidate_models() -> list[str]:
    """Configured model first, then fallbacks (de-duplicated, order preserved)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for name in [_PRIMARY_IMAGE_MODEL, *_IMAGE_MODEL_FALLBACKS]:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _is_model_404(exc: Exception) -> bool:
    """Detect a 404 NOT_FOUND from google-genai for an unknown model name."""
    msg = str(exc).lower()
    return ("404" in msg or "not_found" in msg) and "model" in msg


def _generate_with_fallback(client, contents) -> tuple[object, str]:
    """Call generate_content trying each candidate model in order.

    Returns (response, model_used). Raises the last exception if every
    candidate fails. Failures other than 404-not-found re-raise immediately,
    since a quota/safety/network error won't be fixed by switching model."""
    last_exc: Exception | None = None
    for model in _candidate_models():
        try:
            response = client.models.generate_content(model=model, contents=contents)
            if model != _PRIMARY_IMAGE_MODEL:
                logger.info(
                    f"[image_gen] Primary model unavailable; succeeded with {model!r}"
                )
            return response, model
        except Exception as exc:
            if not _is_model_404(exc):
                raise
            logger.info(f"[image_gen] {model!r} 404, trying next candidate")
            last_exc = exc
    raise last_exc or RuntimeError("No image-capable Gemini model is reachable")


def _upload_to_r2(image_bytes: bytes, media_type: str) -> str | None:
    """Upload image bytes to Cloudflare R2 and return the public URL.

    Returns None if R2 isn't configured on this deployment — caller falls
    back to a base64 data URL in that case so the agent flow still works.
    """
    try:
        from app.api.routes.storage_proxy import _get_r2_client
    except Exception as e:  # storage_proxy importable but R2 helper changed
        logger.warning(f"[image_gen] R2 helper unavailable: {e}")
        return None

    client = _get_r2_client()
    if client is None or not settings.r2_bucket_name:
        return None

    ext = media_type.split("/")[-1].split(";")[0].strip() or "png"
    if ext == "jpeg":
        ext = "jpg"
    key = f"generated_images/{uuid.uuid4().hex[:16]}.{ext}"
    try:
        client.put_object(
            Bucket=settings.r2_bucket_name,
            Key=key,
            Body=image_bytes,
            ContentType=media_type,
        )
    except Exception as e:
        logger.error(f"[image_gen] R2 put_object failed: {e}")
        return None

    url = (
        f"https://{settings.r2_account_id}.r2.cloudflarestorage.com/"
        f"{settings.r2_bucket_name}/{key}"
    )
    logger.info(f"[image_gen] R2 upload OK ({len(image_bytes)} bytes) → {url}")
    return url


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


def _extract_text_commentary(response) -> str:
    """Pull text parts out of a genai response. The model often returns a
    short explanation alongside the image (e.g. why it refused, or what it
    chose to render). Surface this so the agent can pass it to the user.
    """
    fragments: list[str] = []
    try:
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                txt = getattr(part, "text", None)
                if txt:
                    fragments.append(txt)
    except Exception:
        pass
    return " ".join(f.strip() for f in fragments if f.strip())


_FINISH_REASON_HUMAN = {
    "PROHIBITED_CONTENT": (
        "Gemini blocked this prompt as prohibited content "
        "(commonly: copyrighted/trademarked characters, real people, "
        "or restricted subjects). Try a generic description instead."
    ),
    "SAFETY": (
        "Gemini blocked this prompt for safety reasons. "
        "Rephrase to avoid sensitive content."
    ),
    "RECITATION": (
        "Gemini refused to recite copyrighted material. "
        "Describe the object generically instead of naming a brand or character."
    ),
    "BLOCKLIST": (
        "Gemini blocked one of the words in this prompt. "
        "Try alternative wording."
    ),
    "SPII": (
        "Gemini refused because the prompt may contain personal information. "
        "Remove names or identifying details."
    ),
    "IMAGE_SAFETY": (
        "Gemini refused on image-safety grounds. "
        "Try a different reference image."
    ),
    "MAX_TOKENS": (
        "Gemini hit the response length limit before producing an image. "
        "Shorten the prompt and retry."
    ),
}


def _refusal_summary(response) -> str:
    """Human-readable explanation of why Gemini returned no image.

    Returns a single sentence the picker can show end-users directly.
    Falls back to a generic message + raw finish_reason if we don't
    recognize the code, so we never swallow the failure silently.
    """
    finish_reason_raw: str | None = None
    flagged_categories: list[str] = []
    text_commentary = _extract_text_commentary(response)

    try:
        for cand in getattr(response, "candidates", []) or []:
            fr = getattr(cand, "finish_reason", None)
            if fr is not None and finish_reason_raw is None:
                # google-genai usually exposes an enum; .name is the code.
                finish_reason_raw = getattr(fr, "name", None) or str(fr)
            for sr in getattr(cand, "safety_ratings", []) or []:
                prob = getattr(sr, "probability", None)
                prob_name = getattr(prob, "name", None) or str(prob or "")
                if prob_name in {"HIGH", "MEDIUM"}:
                    cat = getattr(sr, "category", "?")
                    cat_name = getattr(cat, "name", None) or str(cat)
                    flagged_categories.append(cat_name)
    except Exception:
        pass

    # Strip enum prefix if present, e.g. "FinishReason.PROHIBITED_CONTENT".
    code = (finish_reason_raw or "").rsplit(".", 1)[-1].upper()
    human = _FINISH_REASON_HUMAN.get(code)
    if human:
        if text_commentary:
            return f"{human} (Gemini said: \"{text_commentary[:160]}\")"
        return human

    if flagged_categories:
        cats = ", ".join(sorted(set(flagged_categories)))
        return (
            f"Gemini's safety filter flagged this prompt ({cats}). "
            f"Try rephrasing."
        )
    if text_commentary:
        return f"Gemini returned no image. It said: \"{text_commentary[:200]}\""
    if code:
        return f"Gemini returned no image (reason: {code}). Try rephrasing."
    return "Gemini returned no image. Try rephrasing the prompt."


# Allowed values per Gemini Image API.
_VALID_ASPECTS = {"1:1", "3:4", "4:3", "9:16", "16:9"}


def _compose_prompt(prompt: str, aspect_ratio: Optional[str]) -> str:
    """Inject an aspect-ratio hint into the prompt — gemini-2.5-flash-image
    honors natural-language sizing instructions reliably."""
    if not aspect_ratio:
        return prompt
    aspect_ratio = aspect_ratio.strip()
    if aspect_ratio not in _VALID_ASPECTS:
        logger.info(
            f"[image_gen] Ignoring unsupported aspect_ratio={aspect_ratio!r}; "
            f"valid: {sorted(_VALID_ASPECTS)}"
        )
        return prompt
    return f"{prompt}\n\nAspect ratio: {aspect_ratio}"


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
    """Wrap result so the agent loop's image-extraction regex picks it up.

    Two sentinels:
      [IMAGE_DATA_URL]<data:…>[/IMAGE_DATA_URL]  — fed to the LLM as a
        multimodal ToolMessage so it can see the image on this turn
        (R2 buckets are private behind our /storage proxy, so external
        LLMs can't fetch the http URL directly).
      [PUBLIC_URL]<https://…>[/PUBLIC_URL]       — handed to the chat
        UI via the image.generated stream event so messages don't carry
        multi-MB base64 in localStorage. Only present when R2 upload
        succeeded; the chat UI falls back to the data URL otherwise.
    """
    data_url = _bytes_to_data_url(image_bytes, media_type)
    public_url = _upload_to_r2(image_bytes, media_type)

    parts = [
        f"{action.capitalize()}d image for prompt: {prompt!r}.",
        f"[IMAGE_DATA_URL]{data_url}[/IMAGE_DATA_URL]",
    ]
    if public_url:
        parts.append(f"[PUBLIC_URL]{public_url}[/PUBLIC_URL]")
    return "\n".join(parts)


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
        response, _model_used = _generate_with_fallback(client, prompt)
    except Exception as e:
        logger.error(f"[image_gen] Gemini generate_content failed: {e}")
        return f"Image generation failed: {e}"

    extracted = _extract_image_bytes(response)
    if extracted is None:
        reason = _refusal_summary(response)
        logger.warning(f"[image_gen] generate_image refused: {reason}")
        # Agent-visible string: the LLM uses this verbatim in its reply
        # to the user, so phrase it as a complete user-facing sentence.
        return f"Image generation failed. {reason}"

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
        response, _model_used = _generate_with_fallback(client, [prompt, ref_image])
    except Exception as e:
        logger.error(f"[image_gen] Gemini edit failed: {e}")
        return f"Image edit failed: {e}"

    extracted = _extract_image_bytes(response)
    if extracted is None:
        reason = _refusal_summary(response)
        logger.warning(f"[image_gen] edit_image refused: {reason}")
        return f"Image edit failed. {reason}"

    image_bytes, media_type = extracted
    logger.info(
        f"[image_gen] edit_image OK ({len(image_bytes)} bytes, {media_type}) "
        f"prompt={prompt[:80]!r}"
    )
    return _format_tool_result(prompt, image_bytes, media_type, "edit")

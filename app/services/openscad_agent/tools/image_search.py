import base64
import logging
import time

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _fetch_ddg_images(query: str, max_results: int = 3, retries: int = 2) -> list[dict]:
    """Search DuckDuckGo Images and return image metadata. Retries on 403."""
    try:
        from ddgs import DDGS
    except ImportError:
        logger.warning("ddgs not installed; image search unavailable")
        return []

    for attempt in range(retries + 1):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=max_results))
            if results:
                return results
        except Exception as e:
            if attempt < retries:
                wait = 1.5 * (attempt + 1)
                logger.info(f"[IMAGE] DDG images attempt {attempt + 1} failed, retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                logger.warning(f"DDG image search failed after {retries + 1} attempts: {e}")
    return []


def _fetch_ddg_text(query: str, max_results: int = 3) -> list[dict]:
    """Fallback: text search for shape descriptions when image search is blocked."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as e:
        logger.warning(f"DDG text search failed: {e}")
        return []


def _download_thumbnail(url: str, timeout: float = 8.0) -> str | None:
    """Download a thumbnail image and return as base64 data URL.
    Uses small images to minimize token usage (~50-100 tokens per thumbnail).
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            if "image" not in content_type:
                return None
            # Reject images > 200KB (thumbnails should be much smaller)
            if len(resp.content) > 200 * 1024:
                return None
            b64 = base64.b64encode(resp.content).decode("utf-8")
            media_type = content_type.split(";")[0].strip()
            return f"data:{media_type};base64,{b64}"
    except Exception as e:
        logger.debug(f"Failed to download thumbnail {url}: {e}")
        return None


@tool
def search_reference_images(query: str) -> str:
    """Search for reference images of real-world objects to understand their shape.

    Use this when the user asks to build something you're not sure about visually,
    like anime characters, specific weapons, brand products, animals, etc.

    Args:
        query: Descriptive search query for the object (e.g., "Naruto kunai knife shape", "Gundam head 3D model reference")

    Returns:
        Description of found reference images with key shape details.
    """
    # Try image search first
    results = _fetch_ddg_images(f"{query} 3D shape reference", max_results=3)

    if results:
        descriptions = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            descriptions.append(f"{i}. {title}")

        summary = f"Found {len(results)} reference images for '{query}':\n"
        summary += "\n".join(descriptions)
        summary += "\n\nUse these references to inform the shape's proportions and key features."

        # Use thumbnail URL (small ~150px) instead of full image to save tokens
        for r in results:
            thumb_url = r.get("thumbnail")
            if thumb_url:
                data_url = _download_thumbnail(thumb_url)
                if data_url:
                    summary += f"\n\n[IMAGE_DATA_URL]{data_url}[/IMAGE_DATA_URL]"
                    break
        return summary

    # Fallback: text search for shape descriptions
    logger.info(f"[IMAGE] Image search failed, falling back to text search for '{query}'")
    text_results = _fetch_ddg_text(f"{query} character design proportions features description", max_results=3)

    if text_results:
        descriptions = []
        for i, r in enumerate(text_results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            descriptions.append(f"{i}. {title}: {body[:200]}")

        summary = f"Text references for '{query}' (images unavailable):\n"
        summary += "\n".join(descriptions)
        summary += "\n\nUse these descriptions to understand the character's key visual features and proportions."
        return summary

    return f"No reference found for '{query}'. Use your best knowledge of the shape."

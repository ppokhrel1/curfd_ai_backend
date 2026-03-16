import base64
import logging

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _fetch_ddg_images(query: str, max_results: int = 3) -> list[dict]:
    """Search DuckDuckGo Images and return image metadata."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=max_results))
        return results
    except ImportError:
        logger.warning("duckduckgo-search not installed; image search unavailable")
        return []
    except Exception as e:
        logger.warning(f"DuckDuckGo image search failed: {e}")
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
    results = _fetch_ddg_images(f"{query} 3D shape reference", max_results=3)
    if not results:
        return f"No reference images found for '{query}'. Use your best knowledge of the shape."

    descriptions = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        desc = f"{i}. {title}"
        descriptions.append(desc)

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

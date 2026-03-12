"""Retrieve relevant OpenSCAD examples for RAG-augmented code generation.

Flow:
1. Generate embedding for user query
2. Vector similarity search in openscad_examples table (pgvector)
3. Fetch matched code from Supabase Storage
4. If no DB matches, fall back to web search for the object shape
5. Return formatted examples to inject into the code generation prompt
"""

import json
import logging
import re

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.openscad_example import OpenscadExample

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 2
MAX_CODE_LENGTH = 4000
STORAGE_BUCKET = "openscad-examples"
EMBEDDING_MODEL = "gemini-embedding-001"
SIMILARITY_THRESHOLD = 0.3  # Minimum cosine similarity to consider a match

# ── Embedding generation ────────────────────────────────────────────────────


def _generate_embedding(text_input: str) -> list[float] | None:
    """Generate embedding for a query string using Gemini API."""
    api_key = settings.gemini_api_key
    if not api_key:
        logger.warning("[RAG] No GEMINI_API_KEY set, skipping vector search")
        return None

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "model": f"models/{EMBEDDING_MODEL}",
                    "content": {"parts": [{"text": text_input}]},
                },
            )
            resp.raise_for_status()
            return resp.json()["embedding"]["values"]
    except Exception as e:
        logger.warning(f"[RAG] Embedding generation failed: {e}")
        return None


# ── Supabase Storage fetch ───────────────────────────────────────────────────


def _fetch_from_storage(storage_path: str) -> str | None:
    """Download a .scad file from Supabase Storage."""
    url = settings.supabase_url
    if not url:
        return None
    if not url.startswith("http"):
        url = f"https://{url}"

    download_url = f"{url}/storage/v1/object/public/{STORAGE_BUCKET}/{storage_path}"

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(download_url)
            if resp.status_code == 200:
                return resp.text
            logger.warning(f"[RAG] Storage fetch failed for {storage_path}: {resp.status_code}")
    except Exception as e:
        logger.warning(f"[RAG] Storage fetch error: {e}")
    return None


# ── Vector search ────────────────────────────────────────────────────────────


async def _vector_search(db: AsyncSession, user_input: str) -> list[dict]:
    """Search openscad_examples using pgvector cosine similarity."""
    embedding = _generate_embedding(user_input)
    if embedding is None:
        return []

    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

    # pgvector cosine distance: 1 - cosine_similarity
    # Lower distance = more similar. Use cast() for asyncpg compatibility.
    result = await db.execute(
        text("""
            SELECT id, name, category, prompt, storage_path,
                   1 - (embedding <=> cast(:embedding as vector)) AS similarity
            FROM openscad_examples
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> cast(:embedding as vector)
            LIMIT :limit
        """),
        {"embedding": embedding_str, "limit": MAX_EXAMPLES},
    )

    rows = result.mappings().all()
    matches = [
        dict(row) for row in rows
        if row["similarity"] >= SIMILARITY_THRESHOLD
    ]

    return matches


# ── Web search fallback ─────────────────────────────────────────────────────


def _extract_keywords(user_input: str) -> list[str]:
    """Extract meaningful keywords from user input for web search."""
    stopwords = {
        "a", "an", "the", "make", "create", "build", "design", "generate",
        "me", "please", "can", "you", "i", "want", "need", "would", "like",
        "with", "and", "or", "for", "to", "of", "in", "on", "that", "this",
        "is", "it", "my", "some", "simple", "basic", "parametric", "3d",
        "model", "openscad", "print", "printable",
    }
    words = re.findall(r'[a-zA-Z]+', user_input.lower())
    return [w for w in words if w not in stopwords and len(w) > 2]


async def _web_search_examples(user_input: str) -> str:
    """Fall back to web search for the shape of the object."""
    try:
        from langchain_community.tools import DuckDuckGoSearchRun

        search = DuckDuckGoSearchRun()
        keywords = _extract_keywords(user_input)
        query = f"OpenSCAD code {' '.join(keywords[:4])} shape dimensions mm"
        logger.info(f"[RAG] Web search fallback: {query}")
        results = await search.ainvoke(query)
        if results:
            return results[:3000]
    except ImportError:
        logger.warning("[RAG] duckduckgo-search not installed, skipping web search fallback")
    except Exception as e:
        logger.warning(f"[RAG] Web search failed: {e}")
    return ""


# ── Formatting ───────────────────────────────────────────────────────────────


def _format_examples(examples: list[tuple[dict, str]]) -> str:
    """Format vector search results (with fetched code) for prompt injection."""
    if not examples:
        return ""

    parts = ["\n# REFERENCE EXAMPLES (from similar designs — adapt the structure, not copy)\n"]
    for i, (meta, code) in enumerate(examples, 1):
        if len(code) > MAX_CODE_LENGTH:
            code = code[:MAX_CODE_LENGTH] + "\n// ... (truncated)"
        sim = meta.get("similarity", 0)
        parts.append(f"## Reference {i}: {meta['name']} (similarity: {sim:.2f})\n{code}\n")

    return "\n".join(parts)


def _format_web_results(web_text: str) -> str:
    """Format web search results as context for prompt injection."""
    if not web_text:
        return ""
    return (
        "\n# REFERENCE CONTEXT (from web search — use for shape/dimension inspiration only)\n"
        f"{web_text}\n"
    )


# ── Public API ───────────────────────────────────────────────────────────────


async def get_examples_for_prompt(
    db: AsyncSession,
    user_input: str,
) -> str:
    """Retrieve relevant OpenSCAD examples and return formatted text for prompt injection.

    1. Vector similarity search in DB
    2. Fetch code from Supabase Storage for each match
    3. If no DB matches, fall back to web search for the object shape
    4. Return formatted string to append to CODE_PROMPT
    """
    # Try vector search first
    matches = await _vector_search(db, user_input)

    if matches:
        logger.info(f"[RAG] Vector search found {len(matches)} examples: {[m['name'] for m in matches]}")

        # Fetch code from storage
        examples_with_code = []
        for meta in matches:
            code = _fetch_from_storage(meta["storage_path"])
            if code:
                examples_with_code.append((meta, code))
            else:
                logger.warning(f"[RAG] Could not fetch code for {meta['name']}")

        if examples_with_code:
            return _format_examples(examples_with_code)

    # Fallback: web search for the shape of the object
    logger.info("[RAG] No DB matches, trying web search for shape")
    web_text = await _web_search_examples(user_input)
    if web_text:
        return _format_web_results(web_text)

    logger.info("[RAG] No examples found from DB or web search")
    return ""

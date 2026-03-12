#!/usr/bin/env python3
"""Fetch OpenSCAD examples from HuggingFace, upload code to Supabase Storage,
generate embeddings, and save references in the openscad_examples DB table.

Usage:
    python -m scripts.populate_openscad_examples [--dry-run] [--limit N]

Requires: pip install datasets
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from datasets import load_dataset
from sqlalchemy import select, func, delete, text

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.openscad_example import OpenscadExample

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HF_DATASET = "redcathode/thingiverse-openscad"
STORAGE_BUCKET = "openscad-examples"
STORAGE_FOLDER = "examples"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768

# ── Embedding helpers ────────────────────────────────────────────────────────


def get_embeddings(texts: list[str], api_key: str) -> list[list[float]]:
    """Generate embeddings using Gemini API (one at a time for reliability)."""
    all_embeddings = []
    with httpx.Client(timeout=60.0) as client:
        for t in texts:
            resp = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "model": f"models/{EMBEDDING_MODEL}",
                    "content": {"parts": [{"text": t}]},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            all_embeddings.append(data["embedding"]["values"])
    return all_embeddings


# ── Supabase Storage helpers ─────────────────────────────────────────────────


def _storage_headers() -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }


def _storage_url() -> str:
    """Build the Supabase Storage REST API base URL."""
    url = settings.supabase_url
    if not url:
        raise RuntimeError("SUPABASE_URL is not set")
    if not url.startswith("http"):
        url = f"https://{url}"
    return f"{url}/storage/v1"


def ensure_bucket(client: httpx.Client) -> None:
    """Create the storage bucket if it doesn't exist."""
    base = _storage_url()
    resp = client.get(f"{base}/bucket/{STORAGE_BUCKET}", headers=_storage_headers())
    if resp.status_code == 200:
        logger.info(f"Bucket '{STORAGE_BUCKET}' already exists")
        return

    resp = client.post(
        f"{base}/bucket",
        headers={**_storage_headers(), "Content-Type": "application/json"},
        json={"id": STORAGE_BUCKET, "name": STORAGE_BUCKET, "public": True},
    )
    if resp.status_code in (200, 201):
        logger.info(f"Created bucket '{STORAGE_BUCKET}'")
    else:
        logger.warning(f"Bucket creation response: {resp.status_code} {resp.text}")


def upload_to_storage(client: httpx.Client, path: str, content: str) -> str:
    """Upload a .scad file to Supabase Storage. Returns the storage path."""
    base = _storage_url()
    full_path = f"{STORAGE_FOLDER}/{path}"
    url = f"{base}/object/{STORAGE_BUCKET}/{full_path}"

    headers = {
        **_storage_headers(),
        "Content-Type": "text/plain",
        "x-upsert": "true",
    }

    resp = client.post(url, headers=headers, content=content.encode("utf-8"))
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed for {path}: {resp.status_code} {resp.text}")

    return full_path


# ── Category classification ──────────────────────────────────────────────────

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "bracket": ["bracket", "mount", "clamp", "holder", "hanger", "clip", "hook"],
    "enclosure": ["enclosure", "box", "case", "housing", "container", "cover", "lid"],
    "gear": ["gear", "pulley", "sprocket", "cog", "rack", "pinion"],
    "fastener": ["screw", "bolt", "nut", "washer", "spacer", "standoff", "rivet"],
    "pipe_fitting": ["pipe", "tube", "fitting", "connector", "coupler", "elbow", "tee", "flange", "bushing", "grommet"],
    "hinge_joint": ["hinge", "joint", "bearing", "pivot", "axle"],
    "wheel_vehicle": ["wheel", "tire", "cart", "car", "vehicle", "tractor", "wagon"],
    "knob_handle": ["knob", "handle", "lever", "grip", "dial", "button"],
    "plate_panel": ["plate", "panel", "base", "tray", "shelf", "rack"],
    "structural": ["beam", "strut", "column", "frame", "rail", "channel", "extrusion"],
    "tool": ["wrench", "tool", "jig", "fixture", "gauge", "ruler"],
    "electronics": ["arduino", "raspberry", "pcb", "led", "battery", "sensor", "fan", "vent"],
    "household": ["cup", "mug", "vase", "bowl", "funnel", "bottle", "cap", "planter"],
    "organizer": ["organizer", "stand", "dock", "caddy", "divider", "slot"],
}


def classify_category(name: str, description: str) -> str:
    """Classify an example into a category based on name and description."""
    text_lower = f"{name} {description}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return category
    return "general"


# ── Quality filters ──────────────────────────────────────────────────────────

def is_quality_code(code: str) -> bool:
    """Filter for code that follows good OpenSCAD patterns."""
    if not code or len(code) < 200:
        return False
    if len(code) > 8000:
        return False
    if "module" not in code:
        return False
    primitives = re.findall(r'\b(cube|cylinder|sphere|linear_extrude|rotate_extrude)\s*\(', code)
    if len(primitives) < 1:
        return False
    var_decls = re.findall(r'^\s*[a-zA-Z_]\w*\s*=\s*[^;]+;', code, re.MULTILINE)
    if len(var_decls) < 2:
        return False
    if re.search(r'\binclude\s*<', code) or re.search(r'\buse\s*<', code):
        return False
    return True


def clean_code(raw: str) -> str:
    """Clean raw code from the dataset — strip filename headers and markdown fences."""
    code = re.sub(r'^[^\n]+\.scad:\s*\n```\s*\n?', '', raw, flags=re.MULTILINE)
    code = re.sub(r'\n```\s*$', '', code)
    code = re.sub(r'^```(?:openscad)?\s*\n?', '', code)
    code = re.sub(r'\n?```\s*$', '', code)
    return code.strip()


# ── Main population logic ────────────────────────────────────────────────────

async def populate(dry_run: bool = False, limit: int | None = None) -> None:
    """Fetch from HuggingFace, upload to Supabase Storage, generate embeddings, save refs in DB."""
    # Validate required settings
    gemini_key = settings.gemini_api_key
    if not gemini_key and not dry_run:
        raise RuntimeError("GEMINI_API_KEY is required for embedding generation")

    logger.info(f"Loading dataset: {HF_DATASET}")
    ds = load_dataset(HF_DATASET, split="train")
    logger.info(f"Dataset loaded: {len(ds)} rows")

    # Process and filter
    examples = []
    category_counts: dict[str, int] = {}
    max_per_category = 20

    for row in ds:
        raw_code = row["scad"]
        code = clean_code(raw_code)

        if not is_quality_code(code):
            continue

        name = row["name"]
        prompt = row["fakeprompt"] or row["description"] or name
        category = classify_category(name, row.get("description", ""))

        if category_counts.get(category, 0) >= max_per_category:
            continue

        example_id = str(uuid.uuid4())
        examples.append({
            "id": example_id,
            "name": name,
            "category": category,
            "prompt": prompt[:2000],
            "code": code,
            "source": "huggingface",
        })
        category_counts[category] = category_counts.get(category, 0) + 1

        if limit and len(examples) >= limit:
            break

    logger.info(f"Filtered to {len(examples)} quality examples")
    logger.info(f"Categories: {dict(sorted(category_counts.items()))}")

    if dry_run:
        logger.info("[DRY RUN] Would upload and insert:")
        for ex in examples[:10]:
            logger.info(f"  [{ex['category']}] {ex['name']} ({len(ex['code'])} chars)")
        logger.info(f"  ... and {max(0, len(examples) - 10)} more")
        return

    # Generate embeddings in batches
    logger.info("Generating embeddings...")
    embedding_batch_size = 50
    all_embeddings: list[list[float]] = []
    for i in range(0, len(examples), embedding_batch_size):
        batch_texts = [
            f"{ex['name']}. {ex['category']}. {ex['prompt'][:500]}"
            for ex in examples[i : i + embedding_batch_size]
        ]
        batch_embeddings = get_embeddings(batch_texts, gemini_key)
        all_embeddings.extend(batch_embeddings)
        logger.info(f"Embedded batch {i // embedding_batch_size + 1}/{(len(examples) + embedding_batch_size - 1) // embedding_batch_size}")

    # Upload to Supabase Storage and insert references into DB
    with httpx.Client(timeout=30.0) as http:
        ensure_bucket(http)

        async with SessionLocal() as db:
            # Clear existing HuggingFace examples
            await db.execute(
                delete(OpenscadExample).where(OpenscadExample.source == "huggingface")
            )
            await db.commit()
            logger.info("Cleared existing HuggingFace examples from DB")

            batch_size = 25
            uploaded = 0
            for i in range(0, len(examples), batch_size):
                batch = examples[i : i + batch_size]
                batch_embeddings = all_embeddings[i : i + batch_size]

                for j, ex in enumerate(batch):
                    # Upload .scad file to storage
                    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', ex["name"])[:80]
                    file_path = f"{ex['category']}/{safe_name}_{ex['id'][:8]}.scad"

                    try:
                        storage_path = upload_to_storage(http, file_path, ex["code"])
                    except Exception as e:
                        logger.warning(f"Failed to upload {ex['name']}: {e}")
                        continue

                    # Insert row with embedding via raw SQL (pgvector)
                    # Use string format for vector since asyncpg doesn't support ::vector cast in params
                    embedding_str = "[" + ",".join(str(v) for v in batch_embeddings[j]) + "]"
                    await db.execute(
                        text("""
                            INSERT INTO openscad_examples (id, name, category, prompt, storage_path, source, embedding)
                            VALUES (:id, :name, :category, :prompt, :storage_path, :source, cast(:embedding as vector))
                        """),
                        {
                            "id": ex["id"],
                            "name": ex["name"],
                            "category": ex["category"],
                            "prompt": ex["prompt"],
                            "storage_path": storage_path,
                            "source": ex["source"],
                            "embedding": embedding_str,
                        },
                    )
                    uploaded += 1

                await db.commit()
                logger.info(f"Batch {i // batch_size + 1}: uploaded {len(batch)} files")

            # Verify
            result = await db.execute(select(func.count(OpenscadExample.id)))
            final_count = result.scalar()
            logger.info(f"Done! Uploaded {uploaded} files, {final_count} references in DB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate OpenSCAD examples from HuggingFace")
    parser.add_argument("--dry-run", action="store_true", help="Don't upload, just show what would be processed")
    parser.add_argument("--limit", type=int, default=None, help="Max examples to process")
    args = parser.parse_args()

    asyncio.run(populate(dry_run=args.dry_run, limit=args.limit))

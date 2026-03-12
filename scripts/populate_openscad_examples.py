#!/usr/bin/env python3
"""Fetch OpenSCAD examples from multiple HuggingFace datasets, upload code to
Supabase Storage, generate embeddings, and save references in the
openscad_examples DB table.

Usage:
    python -m scripts.populate_openscad_examples [--dry-run] [--limit N] [--dataset NAME]

Requires: pip install datasets
"""

import argparse
import asyncio
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

STORAGE_BUCKET = "openscad-examples"
STORAGE_FOLDER = "examples"
EMBEDDING_MODEL = "gemini-embedding-001"

# ── Dataset definitions ─────────────────────────────────────────────────────

DATASETS = {
    "thingiverse": {
        "hf_name": "redcathode/thingiverse-openscad",
        "source": "huggingface",
        "extract": lambda row: {
            "name": row["name"],
            "code": row["scad"],
            "prompt": row.get("fakeprompt") or row.get("description") or row["name"],
            "description": row.get("description", ""),
        },
    },
    "thomasthemaker": {
        "hf_name": "ThomasTheMaker/OpenSCAD",
        "source": "thomasthemaker",
        "extract": lambda row: {
            "name": row["object"],
            "code": row["scad"],
            "prompt": row.get("description") or row["object"],
            "description": row.get("description", ""),
        },
    },
    "synthetic-v29": {
        "hf_name": "ThomasTheMaker/Synthetic-Openscad-v29",
        "source": "synthetic-v29",
        "extract": lambda row: {
            "name": row["name"],
            "code": row.get("original_code") or row.get("code", ""),
            "prompt": row["name"],
            "description": row["name"],
            "valid": row.get("valid", True),
        },
    },
}

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
    code = re.sub(r'^```(?:openscad|scad)?\s*\n?', '', code)
    code = re.sub(r'\n?```\s*$', '', code)
    return code.strip()


# ── Main population logic ────────────────────────────────────────────────────

async def populate(
    dry_run: bool = False,
    limit: int | None = None,
    dataset_name: str | None = None,
) -> None:
    """Fetch from HuggingFace, upload to Supabase Storage, generate embeddings, save refs in DB."""
    gemini_key = settings.gemini_api_key
    if not gemini_key and not dry_run:
        raise RuntimeError("GEMINI_API_KEY is required for embedding generation")

    # Determine which datasets to process
    if dataset_name:
        if dataset_name not in DATASETS:
            raise ValueError(f"Unknown dataset: {dataset_name}. Available: {list(DATASETS.keys())}")
        datasets_to_process = {dataset_name: DATASETS[dataset_name]}
    else:
        datasets_to_process = DATASETS

    all_examples: list[dict] = []
    category_counts: dict[str, int] = {}
    max_per_category = 20  # per dataset

    for ds_key, ds_config in datasets_to_process.items():
        hf_name = ds_config["hf_name"]
        source = ds_config["source"]
        extract = ds_config["extract"]

        logger.info(f"Loading dataset: {hf_name}")
        try:
            ds = load_dataset(hf_name, split="train")
        except Exception as e:
            logger.warning(f"Failed to load {hf_name}: {e}")
            continue
        logger.info(f"  {hf_name}: {len(ds)} rows")

        ds_category_counts: dict[str, int] = {}
        ds_count = 0

        for row in ds:
            extracted = extract(row)

            # Skip invalid rows from synthetic dataset
            if not extracted.get("valid", True):
                continue

            raw_code = extracted["code"]
            code = clean_code(raw_code)

            if not is_quality_code(code):
                continue

            name = extracted["name"]
            prompt = extracted["prompt"]
            description = extracted.get("description", "")
            category = classify_category(name, description)

            if ds_category_counts.get(category, 0) >= max_per_category:
                continue

            example_id = str(uuid.uuid4())
            all_examples.append({
                "id": example_id,
                "name": name,
                "category": category,
                "prompt": prompt[:2000],
                "code": code,
                "source": source,
            })
            ds_category_counts[category] = ds_category_counts.get(category, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
            ds_count += 1

            if limit and len(all_examples) >= limit:
                break

        logger.info(f"  {hf_name}: kept {ds_count} quality examples")

        if limit and len(all_examples) >= limit:
            break

    logger.info(f"Total: {len(all_examples)} quality examples")
    logger.info(f"Categories: {dict(sorted(category_counts.items()))}")

    if dry_run:
        logger.info("[DRY RUN] Would upload and insert:")
        for ex in all_examples[:15]:
            logger.info(f"  [{ex['source']}] [{ex['category']}] {ex['name']} ({len(ex['code'])} chars)")
        logger.info(f"  ... and {max(0, len(all_examples) - 15)} more")
        return

    # Generate embeddings in batches
    logger.info("Generating embeddings...")
    embedding_batch_size = 50
    all_embeddings: list[list[float]] = []
    for i in range(0, len(all_examples), embedding_batch_size):
        batch_texts = [
            f"{ex['name']}. {ex['category']}. {ex['prompt'][:500]}"
            for ex in all_examples[i : i + embedding_batch_size]
        ]
        batch_embeddings = get_embeddings(batch_texts, gemini_key)
        all_embeddings.extend(batch_embeddings)
        logger.info(f"Embedded batch {i // embedding_batch_size + 1}/{(len(all_examples) + embedding_batch_size - 1) // embedding_batch_size}")

    # Upload to Supabase Storage and insert references into DB
    with httpx.Client(timeout=30.0) as http:
        ensure_bucket(http)

        async with SessionLocal() as db:
            # Clear existing examples for the sources being processed
            sources = [ds_config["source"] for ds_config in datasets_to_process.values()]
            for source in sources:
                await db.execute(
                    delete(OpenscadExample).where(OpenscadExample.source == source)
                )
            await db.commit()
            logger.info(f"Cleared existing examples for sources: {sources}")

            batch_size = 25
            uploaded = 0
            for i in range(0, len(all_examples), batch_size):
                batch = all_examples[i : i + batch_size]
                batch_embeddings = all_embeddings[i : i + batch_size]

                for j, ex in enumerate(batch):
                    # Upload .scad file to storage
                    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', ex["name"])[:80]
                    file_path = f"{ex['source']}/{ex['category']}/{safe_name}_{ex['id'][:8]}.scad"

                    try:
                        storage_path = upload_to_storage(http, file_path, ex["code"])
                    except Exception as e:
                        logger.warning(f"Failed to upload {ex['name']}: {e}")
                        continue

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
            logger.info(f"Done! Uploaded {uploaded} files, {final_count} total references in DB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate OpenSCAD examples from HuggingFace")
    parser.add_argument("--dry-run", action="store_true", help="Don't upload, just show what would be processed")
    parser.add_argument("--limit", type=int, default=None, help="Max total examples to process")
    parser.add_argument("--dataset", type=str, default=None,
                        choices=list(DATASETS.keys()),
                        help="Only process a specific dataset (default: all)")
    args = parser.parse_args()

    asyncio.run(populate(dry_run=args.dry_run, limit=args.limit, dataset_name=args.dataset))

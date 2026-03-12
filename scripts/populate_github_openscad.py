#!/usr/bin/env python3
"""Scrape standalone OpenSCAD (.scad) files from GitHub repositories,
upload code to Supabase Storage, generate embeddings, and save references
in the openscad_examples DB table.

Usage:
    python -m scripts.populate_github_openscad [--dry-run] [--limit N] [--repo NAME]

Requires: git CLI available on PATH
"""

import argparse
import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select, func, delete, text

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.openscad_example import OpenscadExample

# Reuse helpers from the HuggingFace populate script
from scripts.populate_openscad_examples import (
    get_embeddings,
    ensure_bucket,
    upload_to_storage,
    classify_category,
    is_quality_code,
    STORAGE_BUCKET,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SOURCE_NAME = "github"

# ── GitHub repos to scrape ──────────────────────────────────────────────────

REPOS = {
    "code-solutions": {
        "url": "https://github.com/ProgrammingWithOpenSCAD/CodeSolutions.git",
        "description": "Book examples — progressive OpenSCAD from basics to complex projects",
        "exclude_dirs": [],
    },
    "scadexamples": {
        "url": "https://github.com/cloudymike/scadexamples.git",
        "description": "180+ real-world functional parametric designs",
        "exclude_dirs": ["lib", "libraries"],
    },
    "hzeller-things": {
        "url": "https://github.com/hzeller/openscad-things.git",
        "description": "Clean standalone practical designs — no dependencies",
        "exclude_dirs": [],
    },
    "nmasse-examples": {
        "url": "https://github.com/nmasse-itix/OpenSCAD-Examples.git",
        "description": "Pedagogical examples from basics to enclosures (MIT)",
        "exclude_dirs": [],
    },
    "simonwaldherr": {
        "url": "https://github.com/SimonWaldherr/openscad-examples.git",
        "description": "Creative examples — vases, gears, Mandelbrot (MIT)",
        "exclude_dirs": [],
    },
    "hugokernel-things": {
        "url": "https://github.com/hugokernel/OpenSCAD_Things.git",
        "description": "Practical functional mechanical designs",
        "exclude_dirs": ["lib", "library"],
    },
}

# ── Clone + collect .scad files ─────────────────────────────────────────────


def clone_repo(url: str, dest: Path) -> bool:
    """Shallow clone a repo. Returns True on success."""
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, check=True, timeout=120,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Failed to clone {url}: {e}")
        return False


def collect_scad_files(
    repo_dir: Path,
    exclude_dirs: list[str],
    repo_key: str,
) -> list[dict]:
    """Walk a cloned repo and collect quality .scad files."""
    examples = []
    exclude_lower = {d.lower() for d in exclude_dirs}
    # Also always exclude common library vendored dirs
    exclude_lower |= {"bosl", "bosl2", "mcad", "nutsnbolts", "threads", ".git"}

    for scad_file in repo_dir.rglob("*.scad"):
        # Skip files in excluded directories
        rel = scad_file.relative_to(repo_dir)
        parts_lower = [p.lower() for p in rel.parts[:-1]]
        if any(ex in p for p in parts_lower for ex in exclude_lower):
            continue

        try:
            code = scad_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if not is_quality_code(code):
            continue

        # Derive name from filename
        name = scad_file.stem.replace("_", " ").replace("-", " ").strip()
        if not name:
            continue

        # Use relative path for description context
        description = f"{repo_key}: {'/'.join(rel.parts)}"

        examples.append({
            "id": str(uuid.uuid4()),
            "name": name,
            "category": classify_category(name, description),
            "prompt": name,
            "code": code,
            "source": SOURCE_NAME,
            "repo": repo_key,
        })

    return examples


# ── Main ────────────────────────────────────────────────────────────────────


async def populate_github(
    dry_run: bool = False,
    limit: int | None = None,
    repo_name: str | None = None,
) -> None:
    """Clone repos, filter quality .scad files, embed, upload, save to DB."""
    gemini_key = settings.gemini_api_key
    if not gemini_key and not dry_run:
        raise RuntimeError("GEMINI_API_KEY is required for embedding generation")

    repos_to_process = (
        {repo_name: REPOS[repo_name]} if repo_name else REPOS
    )

    all_examples: list[dict] = []
    category_counts: dict[str, int] = {}
    max_per_category = 15  # per repo to keep diversity

    tmp_base = Path(tempfile.mkdtemp(prefix="openscad_github_"))

    try:
        for repo_key, repo_config in repos_to_process.items():
            repo_dir = tmp_base / repo_key
            logger.info(f"Cloning {repo_key}: {repo_config['url']}")

            if not clone_repo(repo_config["url"], repo_dir):
                continue

            examples = collect_scad_files(
                repo_dir, repo_config.get("exclude_dirs", []), repo_key
            )
            logger.info(f"  {repo_key}: {len(examples)} quality .scad files found")

            # Cap per category per repo
            repo_cat_counts: dict[str, int] = {}
            for ex in examples:
                cat = ex["category"]
                if repo_cat_counts.get(cat, 0) >= max_per_category:
                    continue
                repo_cat_counts[cat] = repo_cat_counts.get(cat, 0) + 1
                category_counts[cat] = category_counts.get(cat, 0) + 1
                all_examples.append(ex)

                if limit and len(all_examples) >= limit:
                    break

            if limit and len(all_examples) >= limit:
                break

    finally:
        # Clean up cloned repos
        shutil.rmtree(tmp_base, ignore_errors=True)

    logger.info(f"Total: {len(all_examples)} quality examples from GitHub")
    logger.info(f"Categories: {dict(sorted(category_counts.items()))}")

    if dry_run:
        logger.info("[DRY RUN] Would upload and insert:")
        for ex in all_examples[:20]:
            logger.info(
                f"  [{ex['repo']}] [{ex['category']}] {ex['name']} ({len(ex['code'])} chars)"
            )
        logger.info(f"  ... and {max(0, len(all_examples) - 20)} more")
        return

    # Generate embeddings
    logger.info("Generating embeddings...")
    batch_size = 50
    all_embeddings: list[list[float]] = []
    for i in range(0, len(all_examples), batch_size):
        batch_texts = [
            f"{ex['name']}. {ex['category']}. {ex['prompt'][:500]}"
            for ex in all_examples[i : i + batch_size]
        ]
        embs = get_embeddings(batch_texts, gemini_key)
        all_embeddings.extend(embs)
        logger.info(
            f"Embedded batch {i // batch_size + 1}/"
            f"{(len(all_examples) + batch_size - 1) // batch_size}"
        )

    # Upload to Supabase Storage + insert into DB
    with httpx.Client(timeout=30.0) as http:
        ensure_bucket(http)

        async with SessionLocal() as db:
            # Clear previous github-sourced examples
            await db.execute(
                delete(OpenscadExample).where(OpenscadExample.source == SOURCE_NAME)
            )
            await db.commit()
            logger.info(f"Cleared existing '{SOURCE_NAME}' examples")

            upload_batch = 25
            uploaded = 0
            for i in range(0, len(all_examples), upload_batch):
                batch = all_examples[i : i + upload_batch]
                batch_embs = all_embeddings[i : i + upload_batch]

                for j, ex in enumerate(batch):
                    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", ex["name"])[:80]
                    file_path = (
                        f"{SOURCE_NAME}/{ex['repo']}/{ex['category']}/"
                        f"{safe_name}_{ex['id'][:8]}.scad"
                    )

                    try:
                        storage_path = upload_to_storage(http, file_path, ex["code"])
                    except Exception as e:
                        logger.warning(f"Failed to upload {ex['name']}: {e}")
                        continue

                    embedding_str = "[" + ",".join(str(v) for v in batch_embs[j]) + "]"
                    await db.execute(
                        text("""
                            INSERT INTO openscad_examples
                                (id, name, category, prompt, storage_path, source, embedding)
                            VALUES
                                (:id, :name, :category, :prompt, :storage_path, :source,
                                 cast(:embedding as vector))
                        """),
                        {
                            "id": ex["id"],
                            "name": ex["name"],
                            "category": ex["category"],
                            "prompt": ex["prompt"],
                            "storage_path": storage_path,
                            "source": SOURCE_NAME,
                            "embedding": embedding_str,
                        },
                    )
                    uploaded += 1

                await db.commit()
                logger.info(f"Batch {i // upload_batch + 1}: uploaded {len(batch)} files")

            result = await db.execute(select(func.count(OpenscadExample.id)))
            final_count = result.scalar()
            logger.info(f"Done! Uploaded {uploaded} GitHub files, {final_count} total in DB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Populate OpenSCAD examples from GitHub repositories"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't upload, just show what would be processed",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max total examples to process",
    )
    parser.add_argument(
        "--repo", type=str, default=None,
        choices=list(REPOS.keys()),
        help="Only process a specific repo (default: all)",
    )
    args = parser.parse_args()

    asyncio.run(populate_github(dry_run=args.dry_run, limit=args.limit, repo_name=args.repo))

"""One-shot migration: copy every object from Backblaze B2 to Cloudflare R2,
then rewrite Asset.uri rows that point at B2 to point at R2 instead.

Idempotent: skips files that already exist in R2 with the same size.
Safe: dry-run mode is the default — pass `--commit` to actually do it.

Usage (from repo root):
    python -m scripts.migrate_b2_to_r2              # dry run
    python -m scripts.migrate_b2_to_r2 --commit     # actually copy + update DB

Requires both R2_* and B2_* env vars set.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
from typing import Iterator, Tuple

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("migrate")


# ─── B2 source ─────────────────────────────────────────────────────────────
def b2_authorize(key_id: str, app_key: str) -> dict:
    auth = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
    r = httpx.get(
        "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
        headers={"Authorization": f"Basic {auth}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def b2_iter_files(api_url: str, token: str, bucket_id: str) -> Iterator[dict]:
    """Yield every file version in the bucket (paginated)."""
    start_name = None
    start_id = None
    while True:
        body: dict = {"bucketId": bucket_id, "maxFileCount": 1000}
        if start_name:
            body["startFileName"] = start_name
        if start_id:
            body["startFileId"] = start_id
        r = httpx.post(
            f"{api_url}/b2api/v3/b2_list_file_versions",
            json=body,
            headers={"Authorization": token},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        for f in data.get("files", []):
            if f.get("action") == "upload":  # skip hide markers
                yield f
        start_name = data.get("nextFileName")
        start_id = data.get("nextFileId")
        if not start_name:
            return


def b2_download(download_url: str, token: str, bucket: str, key: str) -> bytes:
    r = httpx.get(
        f"{download_url}/file/{bucket}/{key}",
        headers={"Authorization": token},
        timeout=300,
        follow_redirects=True,
    )
    r.raise_for_status()
    return r.content


# ─── R2 destination ────────────────────────────────────────────────────────
def r2_client(account_id: str, access_key: str, secret_key: str):
    import boto3
    from botocore.config import Config as BotoConfig
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
    )


def r2_head(client, bucket: str, key: str) -> Tuple[bool, int]:
    """Return (exists, content_length)."""
    from botocore.exceptions import ClientError
    try:
        resp = client.head_object(Bucket=bucket, Key=key)
        return True, int(resp.get("ContentLength", 0))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False, 0
        raise


def guess_content_type(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return {
        "glb": "model/gltf-binary",
        "gltf": "model/gltf+json",
        "stl": "model/stl",
        "obj": "model/obj",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "zip": "application/zip",
        "scad": "text/plain",
    }.get(ext, "application/octet-stream")


# ─── DB rewrite ────────────────────────────────────────────────────────────
async def rewrite_db_uris(b2_bucket: str, r2_account_id: str, r2_bucket: str, commit: bool) -> int:
    """Rewrite asset.uri rows that point at B2 to point at R2.

    Old: https://f005.backblazeb2.com/file/<b2_bucket>/<key>
    New: https://<r2_account_id>.r2.cloudflarestorage.com/<r2_bucket>/<key>

    Uses raw SQL via SQLAlchemy Core to avoid importing ORM models
    (which can break across Python/SQLAlchemy version skews).
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    rewritten = 0
    marker = f"/file/{b2_bucket}/"

    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, uri FROM assets WHERE uri LIKE :pat"),
                {"pat": "%backblazeb2.com%"},
            )
        ).fetchall()

        for asset_id, uri in rows:
            if not uri or marker not in uri:
                continue
            key = uri.split(marker, 1)[1].split("?", 1)[0]
            new = f"https://{r2_account_id}.r2.cloudflarestorage.com/{r2_bucket}/{key}"
            log.info(f"  DB rewrite: asset {asset_id} → {new}")
            if commit:
                await conn.execute(
                    text("UPDATE assets SET uri = :new WHERE id = :id"),
                    {"new": new, "id": asset_id},
                )
            rewritten += 1

    await engine.dispose()
    return rewritten


# ─── Main ──────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Actually copy files and update DB (default: dry run)")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N files (0 = no limit)")
    parser.add_argument("--prefix", default="", help="Only migrate keys starting with this prefix")
    args = parser.parse_args()

    from app.core.config import settings

    if not all([settings.b2_key_id, settings.b2_application_key, settings.b2_bucket_name, settings.b2_bucket_id]):
        log.error("B2_* env vars must be set in .env")
        return 1
    if not all([settings.r2_account_id, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket_name]):
        log.error("R2_* env vars must be set in .env")
        return 1

    log.info(f"Migrating from B2 bucket={settings.b2_bucket_name} → R2 bucket={settings.r2_bucket_name}")
    log.info(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")

    auth = b2_authorize(settings.b2_key_id, settings.b2_application_key)
    b2_token = auth["authorizationToken"]
    b2_api = auth["apiUrl"]
    b2_dl = auth["downloadUrl"]

    r2 = r2_client(settings.r2_account_id, settings.r2_access_key_id, settings.r2_secret_access_key)

    copied = 0
    skipped = 0
    failed = 0
    seen_keys: set[str] = set()  # dedupe across B2 versions

    for f in b2_iter_files(b2_api, b2_token, settings.b2_bucket_id):
        key = f["fileName"]
        size = f.get("contentLength", 0)

        if args.prefix and not key.startswith(args.prefix):
            continue
        if key in seen_keys:  # already migrated newer version this run
            continue
        seen_keys.add(key)

        exists, r2_size = r2_head(r2, settings.r2_bucket_name, key)
        if exists and r2_size == size:
            log.info(f"[skip] {key} (already in R2, {size} bytes)")
            skipped += 1
            continue

        log.info(f"[copy] {key} ({size} bytes)")
        if not args.commit:
            copied += 1
        else:
            try:
                body = b2_download(b2_dl, b2_token, settings.b2_bucket_name, key)
                r2.put_object(
                    Bucket=settings.r2_bucket_name,
                    Key=key,
                    Body=body,
                    ContentType=guess_content_type(key),
                )
                copied += 1
            except Exception as exc:
                log.error(f"  failed: {exc}")
                failed += 1

        if args.limit and (copied + skipped) >= args.limit:
            log.info(f"Hit --limit={args.limit}, stopping.")
            break

    log.info(f"\nFile copy: copied={copied} skipped={skipped} failed={failed}")

    log.info("\nRewriting DB asset URIs...")
    db_rewritten = asyncio.run(
        rewrite_db_uris(
            settings.b2_bucket_name,
            settings.r2_account_id,
            settings.r2_bucket_name,
            commit=args.commit,
        )
    )
    log.info(f"DB rewrite: {db_rewritten} rows {'updated' if args.commit else 'would be updated'}")

    if not args.commit:
        log.info("\n(dry run — no files copied, no DB changes. Re-run with --commit to apply.)")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""Wipe user-generated content from the database for a fresh start.

Truncates: assets, asset_meta, messages, chats, jobs, sessions, scad_versions.
Preserves: users, revoked_token, openscad_example, assembly, scad_optimization.

DRY RUN by default. Pass --commit to actually delete.

Usage:
    python -m scripts.wipe_user_content              # dry run, show counts
    python -m scripts.wipe_user_content --commit     # actually wipe

Optional:
    --include-storage   Also delete every file in the configured B2 + R2
                        buckets. Off by default — leaves storage intact so
                        you can sanity-check after the DB is clean.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("wipe")

# Order matters: child tables first because TRUNCATE ... CASCADE will recurse
# but listing them explicitly makes the intent obvious in the log output.
TABLES_TO_WIPE = [
    "asset_meta",
    "assets",
    "messages",
    "scad_versions",
    "chats",
    "jobs",
    "sessions",
]


async def count_rows() -> dict[str, int]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        for t in TABLES_TO_WIPE:
            try:
                r = await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))
                counts[t] = r.scalar() or 0
            except Exception as exc:
                log.warning(f"  {t}: skipped ({exc})")
                counts[t] = -1
    await engine.dispose()
    return counts


async def truncate_all() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        joined = ", ".join(TABLES_TO_WIPE)
        log.info(f"  TRUNCATE {joined} RESTART IDENTITY CASCADE")
        await conn.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    await engine.dispose()


def wipe_b2() -> int:
    """Delete every file version in the B2 bucket. Returns number deleted."""
    import base64
    import httpx
    from app.core.config import settings
    if not (settings.b2_key_id and settings.b2_application_key and settings.b2_bucket_id):
        log.info("  B2 not configured, skipping")
        return 0
    auth = base64.b64encode(f"{settings.b2_key_id}:{settings.b2_application_key}".encode()).decode()
    r = httpx.get(
        "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
        headers={"Authorization": f"Basic {auth}"}, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    token, api_url = data["authorizationToken"], data["apiUrl"]

    deleted = 0
    start_name = start_id = None
    while True:
        body = {"bucketId": settings.b2_bucket_id, "maxFileCount": 1000}
        if start_name: body["startFileName"] = start_name
        if start_id: body["startFileId"] = start_id
        r = httpx.post(f"{api_url}/b2api/v3/b2_list_file_versions", json=body,
                       headers={"Authorization": token}, timeout=60)
        r.raise_for_status()
        j = r.json()
        for f in j.get("files", []):
            httpx.post(f"{api_url}/b2api/v3/b2_delete_file_version",
                       json={"fileName": f["fileName"], "fileId": f["fileId"]},
                       headers={"Authorization": token}, timeout=30)
            deleted += 1
        start_name, start_id = j.get("nextFileName"), j.get("nextFileId")
        if not start_name:
            break
    return deleted


def wipe_r2() -> int:
    """Delete every object in the R2 bucket. Returns number deleted."""
    from app.core.config import settings
    if not all([settings.r2_account_id, settings.r2_access_key_id,
                settings.r2_secret_access_key, settings.r2_bucket_name]):
        log.info("  R2 not configured, skipping")
        return 0
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.r2_bucket_name):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if not objects:
            continue
        client.delete_objects(Bucket=settings.r2_bucket_name, Delete={"Objects": objects})
        deleted += len(objects)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="Actually delete (default: dry run)")
    parser.add_argument("--include-storage", action="store_true",
                        help="Also wipe every file in B2 + R2 buckets")
    args = parser.parse_args()

    log.info(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")
    log.info("Tables that will be wiped (other tables preserved):")
    for t in TABLES_TO_WIPE:
        log.info(f"  - {t}")

    counts = asyncio.run(count_rows())
    total = sum(c for c in counts.values() if c >= 0)
    log.info(f"\nCurrent row counts:")
    for t, n in counts.items():
        log.info(f"  {t}: {n}")
    log.info(f"  TOTAL: {total}\n")

    if args.commit:
        log.info("Wiping DB...")
        asyncio.run(truncate_all())
        log.info("DB wipe complete.")

        if args.include_storage:
            log.info("Wiping B2 storage...")
            b2_count = wipe_b2()
            log.info(f"B2: deleted {b2_count} file versions")

            log.info("Wiping R2 storage...")
            r2_count = wipe_r2()
            log.info(f"R2: deleted {r2_count} objects")
        else:
            log.info("(Storage left intact — re-run with --include-storage to wipe B2/R2 too.)")
    else:
        log.info("(dry run — nothing was deleted. Re-run with --commit to apply.)")
        if args.include_storage:
            log.info("(--include-storage would also wipe every file in B2 + R2 buckets.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

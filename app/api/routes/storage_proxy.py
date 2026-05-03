"""Proxy endpoint to serve files from object storage.

Reads from Cloudflare R2 first (when configured) and falls back to Backblaze
B2 on miss. The fallback exists so that during the B2→R2 migration period,
old asset rows still pointing at B2 paths keep working without manual fixes.
"""

import base64
import logging
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── R2 (primary) ──────────────────────────────────────────────────────────
_r2_client = None  # boto3 S3 client, lazily constructed


def _r2_configured() -> bool:
    return all([
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
        settings.r2_bucket_name,
    ])


def _get_r2_client():
    """Return a cached boto3 S3 client pointing at R2, or None if not configured."""
    global _r2_client
    if _r2_client is not None:
        return _r2_client
    if not _r2_configured():
        return None
    import boto3
    from botocore.config import Config as BotoConfig
    _r2_client = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=BotoConfig(
            retries={"max_attempts": 2, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        ),
    )
    return _r2_client


def _fetch_r2(bucket: str, key: str):
    """Fetch an object from R2. Returns (body_bytes, content_type) or None on miss.
    Raises HTTPException for non-404 errors."""
    client = _get_r2_client()
    if client is None:
        return None
    from botocore.exceptions import ClientError
    target_bucket = settings.r2_bucket_name or bucket
    try:
        resp = client.get_object(Bucket=target_bucket, Key=key)
        body = resp["Body"].read()
        content_type = resp.get("ContentType") or "application/octet-stream"
        return body, content_type
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        status = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)
        if code in ("NoSuchKey", "404") or status == 404:
            return None
        logger.error(f"[R2] get_object {target_bucket}/{key} failed: {code} {e}")
        raise HTTPException(status_code=502, detail=f"R2 fetch failed: {code}")


def _diagnose_r2_404(bucket: str, key: str) -> None:
    """Log keys that exist near the requested one — surfaces path mismatches."""
    client = _get_r2_client()
    if client is None:
        return
    target_bucket = settings.r2_bucket_name or bucket
    prefix = "/".join(key.split("/")[:-1])
    if prefix:
        prefix += "/"
    try:
        resp = client.list_objects_v2(Bucket=target_bucket, Prefix=prefix, MaxKeys=20)
        names = [o["Key"] for o in resp.get("Contents", [])]
        logger.warning(
            f"[R2] 404 diagnosis — bucket={target_bucket} prefix={prefix!r} "
            f"requested={key!r} found={names}"
        )
    except Exception as exc:
        logger.warning(f"[R2] 404 diagnosis error: {exc}")


# ─── B2 (legacy fallback) ──────────────────────────────────────────────────
_B2_AUTH_TTL_SECONDS = 23 * 60 * 60

_b2_auth_token: Optional[str] = None
_b2_api_url: Optional[str] = None
_b2_download_url: Optional[str] = None
_b2_auth_expires_at: float = 0.0


def _b2_configured() -> bool:
    return bool(settings.b2_key_id and settings.b2_application_key)


def _ensure_b2_auth(force: bool = False) -> None:
    global _b2_auth_token, _b2_api_url, _b2_download_url, _b2_auth_expires_at
    if _b2_auth_token and not force and time.monotonic() < _b2_auth_expires_at:
        return
    if not _b2_configured():
        raise HTTPException(status_code=500, detail="B2 credentials not configured")
    auth_string = base64.b64encode(
        f"{settings.b2_key_id}:{settings.b2_application_key}".encode()
    ).decode()
    try:
        resp = httpx.get(
            "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
            headers={"Authorization": f"Basic {auth_string}"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        logger.error(f"B2 auth network error: {exc}")
        raise HTTPException(status_code=502, detail="B2 auth network error")
    if not resp.is_success:
        logger.error(f"B2 auth failed: {resp.status_code} {resp.text[:200]}")
        raise HTTPException(status_code=502, detail="B2 auth failed")
    data = resp.json()
    _b2_auth_token = data["authorizationToken"]
    _b2_api_url = data["apiUrl"]
    _b2_download_url = data["downloadUrl"]
    _b2_auth_expires_at = time.monotonic() + _B2_AUTH_TTL_SECONDS


async def _download_from_b2(client: httpx.AsyncClient, bucket: str, key: str) -> httpx.Response:
    return await client.get(
        f"{_b2_download_url}/file/{bucket}/{key}",
        headers={"Authorization": _b2_auth_token},
        follow_redirects=True,
    )


async def _fetch_b2(bucket_path_arg: str, key: str):
    """Fetch from B2. Returns (body, content_type) or None on 404. Raises on other errors."""
    if not _b2_configured():
        return None
    _ensure_b2_auth()
    bucket_name = settings.b2_bucket_name or bucket_path_arg
    if settings.b2_bucket_name and bucket_path_arg != settings.b2_bucket_name:
        key = f"{bucket_path_arg}/{key}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await _download_from_b2(client, bucket_name, key)
        if resp.status_code in (401, 403):
            _ensure_b2_auth(force=True)
            resp = await _download_from_b2(client, bucket_name, key)
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            logger.error(f"[B2] {resp.status_code}: {resp.text[:200]}")
            raise HTTPException(status_code=502, detail=f"B2 download failed ({resp.status_code})")
        return resp.content, resp.headers.get("content-type", "application/octet-stream")


# ─── Endpoint ──────────────────────────────────────────────────────────────
@router.get("/debug/storage")
async def debug_storage():
    """Report which storage backends are configured."""
    return {
        "r2_configured": _r2_configured(),
        "r2_bucket": settings.r2_bucket_name,
        "b2_configured": _b2_configured(),
        "b2_bucket": settings.b2_bucket_name,
    }


@router.api_route("/{bucket}/{file_path:path}", methods=["GET", "HEAD"])
async def proxy_storage_file(
    bucket: str = Path(...),
    file_path: str = Path(...),
):
    """Stream a file from storage. R2 first, B2 fallback during migration.

    Error semantics:
      - 404 → genuinely missing in every configured backend
      - 502 → backend auth/network/transport failure
    """
    logger.info(f"[storage] Request: bucket={bucket} file_path={file_path}")

    # Try R2 first.
    if _r2_configured():
        result = _fetch_r2(bucket, file_path)
        if result is not None:
            body, content_type = result
            return _stream(body, content_type, file_path)
        logger.info(f"[storage] R2 miss for {bucket}/{file_path}")

    # Fall back to B2.
    if _b2_configured():
        try:
            result = await _fetch_b2(bucket, file_path)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"[storage] B2 fetch error: {exc}")
            raise HTTPException(status_code=502, detail="Storage backend error")
        if result is not None:
            body, content_type = result
            return _stream(body, content_type, file_path)

    # Both backends missed — diagnose against R2 (the future-of-truth).
    if _r2_configured():
        _diagnose_r2_404(bucket, file_path)
    raise HTTPException(status_code=404, detail=f"File not found: {bucket}/{file_path}")


async def fetch_object_bytes(url_or_path: str) -> tuple[bytes, str]:
    """Fetch a file's bytes from storage by URL or relative path.

    Accepts:
      - A full B2 URL ("https://f005.backblazeb2.com/file/<bucket>/<key>")
      - A full R2 URL ("https://<acct>.r2.cloudflarestorage.com/<bucket>/<key>")
      - A relative path ("generated_models/foo.glb") — uses the configured
        primary bucket
    Tries R2 first, falls back to B2. Raises HTTPException on miss/error.
    """
    bucket, key = _parse_storage_url(url_or_path)

    if _r2_configured():
        result = _fetch_r2(bucket, key)
        if result is not None:
            return result

    if _b2_configured():
        result = await _fetch_b2(bucket, key)
        if result is not None:
            return result

    raise HTTPException(status_code=404, detail=f"File not found: {bucket}/{key}")


def _parse_storage_url(url_or_path: str) -> tuple[str, str]:
    """Extract (bucket, key) from a storage URL or relative path."""
    if url_or_path.startswith("https://") or url_or_path.startswith("http://"):
        try:
            parsed = httpx.URL(url_or_path)
            path = parsed.path.lstrip("/")
        except Exception:
            path = url_or_path
        # B2 friendly URL: /file/<bucket>/<key>
        if path.startswith("file/"):
            parts = path[len("file/"):].split("/", 1)
            if len(parts) == 2:
                return parts[0], parts[1]
        # Generic S3-style: /<bucket>/<key>
        parts = path.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
    # Relative path — use primary bucket (R2 if configured, else B2)
    primary = settings.r2_bucket_name or settings.b2_bucket_name or ""
    return primary, url_or_path


def _stream(body: bytes, content_type: str, file_path: str) -> StreamingResponse:
    return StreamingResponse(
        iter([body]),
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_path.split("/")[-1]}"',
            "Cache-Control": "public, max-age=3600",
        },
    )

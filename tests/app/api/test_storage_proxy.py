"""Tests for the storage proxy: R2 primary + B2 fallback."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.api.routes import storage_proxy as sp


BUCKET = "nooriat-models"
KEY = "generated_models/foo.glb"
URL = f"/api/v1/storage/{BUCKET}/{KEY}"


@pytest.fixture(autouse=True)
def reset_module_state():
    """Each test starts with a fresh proxy module state."""
    sp._r2_client = None
    sp._b2_auth_token = None
    sp._b2_api_url = None
    sp._b2_download_url = None
    sp._b2_auth_expires_at = 0.0
    yield


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as ac:
        yield ac


def _r2_settings(monkeypatch):
    monkeypatch.setattr(sp.settings, "r2_account_id", "acct")
    monkeypatch.setattr(sp.settings, "r2_access_key_id", "ak")
    monkeypatch.setattr(sp.settings, "r2_secret_access_key", "sk")
    monkeypatch.setattr(sp.settings, "r2_bucket_name", BUCKET)


def _b2_settings(monkeypatch):
    monkeypatch.setattr(sp.settings, "b2_key_id", "kid")
    monkeypatch.setattr(sp.settings, "b2_application_key", "kappk")
    monkeypatch.setattr(sp.settings, "b2_bucket_name", BUCKET)
    monkeypatch.setattr(sp.settings, "b2_bucket_id", "bid")


def _make_r2_client(*, hit_body: bytes | None = None, hit_content_type: str = "model/gltf-binary"):
    """Return a fake boto3 client. If hit_body is None, all reads return NoSuchKey."""
    from botocore.exceptions import ClientError

    client = MagicMock()
    if hit_body is None:
        err = ClientError(
            {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "GetObject",
        )
        client.get_object.side_effect = err
        client.list_objects_v2.return_value = {"Contents": []}
    else:
        body = MagicMock()
        body.read.return_value = hit_body
        client.get_object.return_value = {"Body": body, "ContentType": hit_content_type}
    return client


# ─── R2-only path ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_r2_hit_returns_file(monkeypatch, client):
    _r2_settings(monkeypatch)
    fake = _make_r2_client(hit_body=b"GLB_BYTES")
    monkeypatch.setattr(sp, "_get_r2_client", lambda: fake)

    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.content == b"GLB_BYTES"
    assert resp.headers["content-type"].startswith("model/gltf-binary")
    fake.get_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)


@pytest.mark.asyncio
async def test_r2_miss_returns_404_when_b2_not_configured(monkeypatch, client):
    _r2_settings(monkeypatch)
    fake = _make_r2_client(hit_body=None)
    monkeypatch.setattr(sp, "_get_r2_client", lambda: fake)

    resp = await client.get(URL)
    assert resp.status_code == 404
    # diagnostic listing was attempted
    fake.list_objects_v2.assert_called_once()


# ─── R2 miss → B2 fallback ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_r2_miss_falls_back_to_b2(monkeypatch, client):
    _r2_settings(monkeypatch)
    _b2_settings(monkeypatch)

    r2_fake = _make_r2_client(hit_body=None)
    monkeypatch.setattr(sp, "_get_r2_client", lambda: r2_fake)

    # Stub B2 auth + download
    monkeypatch.setattr(sp, "_ensure_b2_auth", lambda force=False: None)
    sp._b2_auth_token = "tok"
    sp._b2_api_url = "https://api005.example"
    sp._b2_download_url = "https://f005.example"

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"B2_BYTES"
    fake_resp.headers = {"content-type": "model/gltf-binary"}
    fake_resp.is_success = True
    monkeypatch.setattr(sp, "_download_from_b2", AsyncMock(return_value=fake_resp))

    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.content == b"B2_BYTES"


@pytest.mark.asyncio
async def test_b2_404_returns_404_after_r2_miss(monkeypatch, client):
    _r2_settings(monkeypatch)
    _b2_settings(monkeypatch)

    r2_fake = _make_r2_client(hit_body=None)
    monkeypatch.setattr(sp, "_get_r2_client", lambda: r2_fake)

    monkeypatch.setattr(sp, "_ensure_b2_auth", lambda force=False: None)
    sp._b2_auth_token = "tok"
    sp._b2_download_url = "https://f005.example"

    miss_resp = MagicMock()
    miss_resp.status_code = 404
    miss_resp.is_success = False
    monkeypatch.setattr(sp, "_download_from_b2", AsyncMock(return_value=miss_resp))

    resp = await client.get(URL)
    assert resp.status_code == 404


# ─── B2-only path (R2 unconfigured) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_b2_only_serves_when_r2_not_configured(monkeypatch, client):
    _b2_settings(monkeypatch)
    # explicitly leave r2_* unset — _r2_configured() should be False
    monkeypatch.setattr(sp.settings, "r2_account_id", None)
    monkeypatch.setattr(sp.settings, "r2_access_key_id", None)
    monkeypatch.setattr(sp.settings, "r2_secret_access_key", None)
    monkeypatch.setattr(sp.settings, "r2_bucket_name", None)

    monkeypatch.setattr(sp, "_ensure_b2_auth", lambda force=False: None)
    sp._b2_auth_token = "tok"
    sp._b2_download_url = "https://f005.example"

    hit = MagicMock()
    hit.status_code = 200
    hit.content = b"LEGACY"
    hit.headers = {"content-type": "application/octet-stream"}
    hit.is_success = True
    monkeypatch.setattr(sp, "_download_from_b2", AsyncMock(return_value=hit))

    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.content == b"LEGACY"


# ─── Debug endpoint ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_debug_endpoint_reports_backends(monkeypatch, client):
    _r2_settings(monkeypatch)
    _b2_settings(monkeypatch)

    resp = await client.get("/api/v1/storage/debug/storage")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "r2_configured": True,
        "r2_bucket": BUCKET,
        "b2_configured": True,
        "b2_bucket": BUCKET,
    }


# ─── _parse_storage_url helper ─────────────────────────────────────────────
class TestParseStorageUrl:
    def test_b2_friendly_url(self, monkeypatch):
        bucket, key = sp._parse_storage_url(
            "https://f005.backblazeb2.com/file/nooriat-models/generated_models/foo.glb"
        )
        assert bucket == "nooriat-models"
        assert key == "generated_models/foo.glb"

    def test_r2_s3_url(self, monkeypatch):
        bucket, key = sp._parse_storage_url(
            "https://acct.r2.cloudflarestorage.com/nooriat-models/generated_models/foo.glb"
        )
        assert bucket == "nooriat-models"
        assert key == "generated_models/foo.glb"

    def test_relative_path_uses_primary_bucket(self, monkeypatch):
        monkeypatch.setattr(sp.settings, "r2_bucket_name", "primary-r2")
        monkeypatch.setattr(sp.settings, "b2_bucket_name", "fallback-b2")
        bucket, key = sp._parse_storage_url("generated_models/foo.glb")
        assert bucket == "primary-r2"
        assert key == "generated_models/foo.glb"

    def test_relative_path_without_r2_falls_back_to_b2_name(self, monkeypatch):
        monkeypatch.setattr(sp.settings, "r2_bucket_name", None)
        monkeypatch.setattr(sp.settings, "b2_bucket_name", "fallback-b2")
        bucket, key = sp._parse_storage_url("generated_models/foo.glb")
        assert bucket == "fallback-b2"

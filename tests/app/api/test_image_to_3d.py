"""Tests for image-to-3D pipeline: search query extraction, image search, storage proxy, image selection."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.routes.chat_stream import (
    _clean_search_query,
    _search_images_sync,
    _fetch_image_candidates,
    _download_image_as_base64,
)
from app.services import image_search_pending


# --- Query Cleaning ---

class TestCleanSearchQuery:
    def test_strips_generate_3d_model(self):
        assert _clean_search_query("generate a 3d model of a dragon") == "a dragon"

    def test_strips_create_image(self):
        assert _clean_search_query("create an image of pashupatinath temple") == "pashupatinath temple"

    def test_strips_make_mesh(self):
        assert _clean_search_query("make a 3d model for a sports car") == "a sports car"

    def test_strips_build(self):
        assert _clean_search_query("build a shape of a ring") == "a ring"

    def test_preserves_plain_query(self):
        assert _clean_search_query("nepali temple pashupatinath") == "nepali temple pashupatinath"

    def test_case_insensitive(self):
        assert _clean_search_query("Generate A 3D Model Of A Chair") == "A Chair"

    def test_empty_after_strip_returns_original(self):
        result = _clean_search_query("generate a 3d model")
        assert len(result) > 0  # Should return something, not empty


# --- Image Search (mocked at httpx module level) ---

class TestSearchImagesSync:
    @patch("httpx.get")
    def test_brave_search_returns_results(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = '''
            <div>
                <img src="https://example.com/photo1.jpg">
                <img src="https://example.com/photo2.png">
                <img src="https://brave.com/logo.png">
                <img src="https://imgs.search.brave/thumb.jpg">
            </div>
        '''
        mock_get.return_value = mock_resp

        results = _search_images_sync("temple 3D render")
        urls = [r["image"] for r in results]
        assert len(urls) > 0
        assert any("example.com" in u for u in urls)
        assert not any("brave.com" in u for u in urls)
        assert not any("imgs.search.brave" in u for u in urls)

    @patch("httpx.get")
    def test_brave_search_empty_page_falls_back_to_ddg(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "<html><body>No images here</body></html>"
        mock_get.return_value = mock_resp

        # Brave returns nothing, DDG fallback also mocked to fail
        with patch("duckduckgo_search.DDGS", side_effect=Exception("mocked")), \
             patch("ddgs.DDGS", side_effect=Exception("mocked"), create=True):
            results = _search_images_sync("nonexistent thing xyz")
            assert results == []

    @patch("httpx.get", side_effect=Exception("Network error"))
    def test_brave_network_error_falls_back(self, _mock_get):
        with patch("duckduckgo_search.DDGS", side_effect=Exception("mocked")), \
             patch("ddgs.DDGS", side_effect=Exception("mocked"), create=True):
            results = _search_images_sync("temple")
            assert isinstance(results, list)
            assert results == []


# --- Keyword Extraction ---

@pytest.mark.asyncio
async def test_extract_search_keywords_success():
    from app.api.routes.chat_stream import _extract_search_keywords

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MagicMock(content="pashupatinath temple 3D render")

    with patch("app.services.openscad_agent.llm_provider.get_llm", return_value=mock_llm):
        result = await _extract_search_keywords("generate 3d model of nepali temple pashupatinath")
        assert "pashupatinath" in result.lower()


@pytest.mark.asyncio
async def test_extract_search_keywords_llm_failure_falls_back():
    from app.api.routes.chat_stream import _extract_search_keywords

    with patch("app.services.openscad_agent.llm_provider.get_llm", side_effect=Exception("LLM down")):
        result = await _extract_search_keywords("generate a 3d model of a ring")
        assert "ring" in result.lower()


# --- Resolve Search Image ---

@pytest.mark.asyncio
async def test_resolve_search_image_success():
    from app.api.routes.chat_stream import _resolve_search_image

    with patch("app.api.routes.chat_stream._extract_search_keywords", new_callable=AsyncMock) as mock_kw, \
         patch("app.api.routes.chat_stream._search_images_sync") as mock_search:

        mock_kw.return_value = "ring 3D render"
        mock_search.return_value = [{"image": "https://example.com/ring.jpg"}]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b"\xff\xd8\xff" * 100
        mock_resp.headers = {"content-type": "image/jpeg"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_ac:
            mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _resolve_search_image("gold ring")
            assert result is not None
            assert result.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_resolve_search_image_no_results():
    from app.api.routes.chat_stream import _resolve_search_image

    with patch("app.api.routes.chat_stream._extract_search_keywords", new_callable=AsyncMock) as mock_kw, \
         patch("app.api.routes.chat_stream._search_images_sync") as mock_search:

        mock_kw.return_value = "nonexistent xyz"
        mock_search.return_value = []

        result = await _resolve_search_image("nonexistent xyz")
        assert result is None


# --- Storage Proxy ---

@pytest.mark.asyncio
async def test_storage_proxy_endpoint_exists():
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/storage/generated_models/test.glb")
        # Endpoint exists (not 404/405). May return 400/500/502 without real supabase.
        assert resp.status_code != 405, "Storage proxy route not found"
        assert resp.status_code != 404 or "File not found" in resp.text


# --- Image Search Pending Registry ---

class TestImageSearchPending:
    def test_register_creates_future(self):
        image_search_pending.cleanup_all()
        request_id = "test-123"
        fut = image_search_pending.register(request_id)
        assert isinstance(fut, asyncio.Future)
        assert not fut.done()

    def test_resolve_sets_result(self):
        image_search_pending.cleanup_all()
        request_id = "test-456"
        fut = image_search_pending.register(request_id)
        success = image_search_pending.resolve(request_id, "https://example.com/image.jpg")
        assert success is True
        assert fut.done()
        assert fut.result() == "https://example.com/image.jpg"

    def test_resolve_unknown_id_returns_false(self):
        image_search_pending.cleanup_all()
        success = image_search_pending.resolve("unknown-id", "https://example.com/image.jpg")
        assert success is False

    def test_cancel_cancels_future(self):
        image_search_pending.cleanup_all()
        request_id = "test-cancel"
        fut = image_search_pending.register(request_id)
        success = image_search_pending.cancel(request_id)
        assert success is True
        assert fut.done()
        assert fut.cancelled()

    def test_cancel_unknown_id_returns_false(self):
        image_search_pending.cleanup_all()
        success = image_search_pending.cancel("unknown-id")
        assert success is False

    def test_cleanup_all_cancels_all(self):
        image_search_pending.cleanup_all()
        fut1 = image_search_pending.register("id1")
        fut2 = image_search_pending.register("id2")
        image_search_pending.cleanup_all()
        assert fut1.cancelled()
        assert fut2.cancelled()


# --- Fetch Image Candidates ---

@pytest.mark.asyncio
async def test_fetch_image_candidates_returns_urls():
    with patch("app.api.routes.chat_stream._extract_search_keywords", new_callable=AsyncMock) as mock_kw, \
         patch("app.api.routes.chat_stream._search_images_sync") as mock_search:

        mock_kw.return_value = "ring product photo"
        mock_search.return_value = [
            {"image": "https://example.com/ring1.jpg"},
            {"image": "https://example.com/ring2.jpg"},
            {"image": "https://example.com/ring3.jpg"},
        ]

        urls, search_query = await _fetch_image_candidates("gold ring")
        assert len(urls) == 3
        assert all(isinstance(u, str) for u in urls)
        assert "example.com/ring1.jpg" in urls[0]
        assert search_query == "ring product photo"


@pytest.mark.asyncio
async def test_fetch_image_candidates_no_results():
    with patch("app.api.routes.chat_stream._extract_search_keywords", new_callable=AsyncMock) as mock_kw, \
         patch("app.api.routes.chat_stream._search_images_sync") as mock_search:

        mock_kw.return_value = "nonexistent xyz 123"
        mock_search.return_value = []

        urls, search_query = await _fetch_image_candidates("nonexistent xyz 123")
        assert urls == []
        assert search_query == "nonexistent xyz 123"


# --- Download Image as Base64 ---

@pytest.mark.asyncio
async def test_download_image_as_base64_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = b"\xff\xd8\xff" * 100
    mock_resp.headers = {"content-type": "image/jpeg"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _download_image_as_base64("https://example.com/ring.jpg")
        assert result is not None
        assert result.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_download_image_as_base64_binary_octet_stream():
    """CDNs often return binary/octet-stream — should infer type from URL extension."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = b"\xff\xd8\xff" * 100
    mock_resp.headers = {"content-type": "binary/octet-stream"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _download_image_as_base64("https://cdn.example.com/photo.webp")
        assert result is not None
        assert result.startswith("data:image/webp;base64,")


@pytest.mark.asyncio
async def test_download_image_as_base64_invalid_content_type():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = b"<html>not an image</html>"
    mock_resp.headers = {"content-type": "text/html"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _download_image_as_base64("https://example.com/notimage.html")
        assert result is None


@pytest.mark.asyncio
async def test_download_image_as_base64_network_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("Network error"))

    with patch("httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _download_image_as_base64("https://example.com/image.jpg")
        assert result is None

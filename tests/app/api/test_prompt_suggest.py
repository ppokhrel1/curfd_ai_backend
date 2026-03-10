"""Tests for the prompt suggestion endpoint."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.session import get_db
from app.core.deps import get_current_user_id_async

ENDPOINT_URL = "/api/v1/prompts/suggest"
MOCK_USER_ID = "user-456"


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    return session


@pytest.fixture
async def client(mock_db_session):
    async def override_get_db():
        yield mock_db_session

    async def override_get_user():
        return MOCK_USER_ID

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_id_async] = override_get_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.api.routes.prompts.suggest_prompt")
async def test_suggest_creative_prompt(mock_suggest, client):
    """Test generating a creative prompt."""
    mock_suggest.return_value = "A spiraling seashell vase with fibonacci ridges"

    response = await client.post(
        ENDPOINT_URL, json={"type": "creative"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["prompt"] == "A spiraling seashell vase with fibonacci ridges"
    mock_suggest.assert_called_once_with(
        prompt_type="creative", existing_text=None
    )


@pytest.mark.asyncio
@patch("app.api.routes.prompts.suggest_prompt")
async def test_suggest_parametric_prompt(mock_suggest, client):
    """Test generating a parametric prompt."""
    mock_suggest.return_value = "A 50mm x 30mm cable management clip with 3mm wall thickness"

    response = await client.post(
        ENDPOINT_URL, json={"type": "parametric"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "cable management clip" in data["prompt"]
    mock_suggest.assert_called_once_with(
        prompt_type="parametric", existing_text=None
    )


@pytest.mark.asyncio
@patch("app.api.routes.prompts.suggest_prompt")
async def test_suggest_enhance_existing(mock_suggest, client):
    """Test enhancing an existing prompt."""
    mock_suggest.return_value = "A detailed spiral gear with 24 involute teeth, module 2, 20-degree pressure angle, and 8mm center bore"

    response = await client.post(
        ENDPOINT_URL,
        json={"type": "parametric", "existing_text": "a gear"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "gear" in data["prompt"].lower()
    mock_suggest.assert_called_once_with(
        prompt_type="parametric", existing_text="a gear"
    )


@pytest.mark.asyncio
async def test_suggest_invalid_type(client):
    """Test validation rejects invalid prompt type."""
    response = await client.post(
        ENDPOINT_URL, json={"type": "invalid"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@patch("app.api.routes.prompts.suggest_prompt")
async def test_suggest_llm_failure(mock_suggest, client):
    """Test error handling when LLM fails."""
    mock_suggest.side_effect = Exception("LLM connection failed")

    response = await client.post(
        ENDPOINT_URL, json={"type": "creative"}
    )

    assert response.status_code == 500
    assert "LLM connection failed" in response.json()["detail"]

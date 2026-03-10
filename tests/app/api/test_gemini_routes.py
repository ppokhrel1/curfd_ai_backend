import json
from httpx import ASGITransport, AsyncClient
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.main import app
from app.db.session import get_db
from app.core.deps import get_current_user_id_async
from app.models.chat import Chat
from app.models.session import Session as UserSession
from app.schemas.openscad import OpenSCADResponse, OpenSCADParameter

ENDPOINT_URL = "/api/v1/openscad/process_requirements"

MOCK_CHAT_ID = "chat-123"
MOCK_USER_ID = "user-456"
VALID_PAYLOAD = {
    "chat_id": MOCK_CHAT_ID,
    "content": "Build a simple cube",
    "role": "user"
}

@pytest.fixture
def mock_db_session():
    """Creates a fake AsyncSession that mocks all DB methods."""
    session = AsyncMock()
    mock_result = MagicMock()
    session.execute.return_value = mock_result
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def fake_refresh(instance):
        instance.id = "mock-uuid-1234"
        instance.created_at = datetime.now()
        instance.updated_at = datetime.now()

    session.refresh.side_effect = fake_refresh
    return session

@pytest.fixture
async def client(mock_db_session):
    """Overrides dependencies and provides an AsyncClient."""
    async def override_get_db():
        yield mock_db_session

    async def override_get_user():
        return MOCK_USER_ID

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_id_async] = override_get_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as ac:
        yield ac

    app.dependency_overrides.clear()

@pytest.mark.asyncio
@patch("app.api.routes.gemini_openscad_generate_route.run_agent")
async def test_process_requirements_success(mock_run_agent, client, mock_db_session):
    """Test successful OpenSCAD code generation with structured output."""
    # Setup Mock DB (Chat exists)
    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = MOCK_CHAT_ID
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = MOCK_USER_ID
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    # Mock the agent to return a structured OpenSCADResponse
    mock_response = OpenSCADResponse(
        openscad_code="cube([10, 10, 10]);",
        parameters=[OpenSCADParameter(name="size", min_val=5.0, max_val=20.0, default_val=10.0, description="Cube size")],
        model_type="mechanical",
        message="Created a simple cube"
    )
    mock_run_agent.return_value = mock_response

    # Execute
    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)

    # Assertions
    assert response.status_code == 200, f"Error: {response.text}"
    data = response.json()
    assert data["role"] == "assistant"
    assert data["content"] == "Created a simple cube"

    # Verify DB interactions
    # add: user message + assistant message + (parts assets via _save_parts_as_assets)
    assert mock_db_session.add.call_count >= 2
    # commit: user message + assistant message + (parts commit)
    assert mock_db_session.commit.call_count >= 2
    # refresh: after assistant commit + after _save_parts_as_assets commit (expires ORM obj)
    assert mock_db_session.refresh.call_count == 2

@pytest.mark.asyncio
async def test_process_requirements_chat_not_found(client, mock_db_session):
    """Test error when chat doesn't exist."""
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = None
    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_process_requirements_forbidden(client, mock_db_session):
    """Test error when user doesn't have access to chat."""
    mock_chat = MagicMock(spec=Chat)
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = "other-user"
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)
    assert response.status_code == 403

@pytest.mark.asyncio
@patch("app.api.routes.gemini_openscad_generate_route.run_agent")
async def test_process_requirements_llm_error(mock_run_agent, client, mock_db_session):
    """Test error handling when agent fails."""
    mock_chat = MagicMock(spec=Chat)
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = MOCK_USER_ID
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    # Mock agent error
    mock_run_agent.side_effect = Exception("LLM API Error")

    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)
    assert response.status_code == 500
    assert "LLM API Error" in response.json()["detail"]

@pytest.mark.asyncio
@patch("app.api.routes.gemini_openscad_generate_route.run_agent")
async def test_process_requirements_chat_response(mock_run_agent, client, mock_db_session):
    """Test conversational (non-code) response."""
    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = MOCK_CHAT_ID
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = MOCK_USER_ID
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    # Mock a conversational response (no code)
    mock_response = OpenSCADResponse(
        openscad_code="",
        parameters=[],
        model_type="chat",
        message="OpenSCAD is a 3D CAD modeler."
    )
    mock_run_agent.return_value = mock_response

    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "OpenSCAD is a 3D CAD modeler."

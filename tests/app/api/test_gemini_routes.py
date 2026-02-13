import json
from httpx import ASGITransport, AsyncClient
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Adjust imports to match your project structure
from app.main import app
from app.db.session import get_db
from app.core.deps import get_current_user_id_async
from app.models.chat import Chat
from app.models.session import Session as UserSession

# --- CONFIGURATION ---
ENDPOINT_URL = "/api/v1/gemini/process_requirements"

MOCK_CHAT_ID = "chat-123"
MOCK_USER_ID = "user-456"
VALID_PAYLOAD = {
    "chat_id": MOCK_CHAT_ID,
    "content": "Build a drone frame",
    "role": "user"
}
VALID_GEMINI_RESPONSE = json.dumps({
    "model_type": "drone",
    "primary_function": "Aerial surveillance",
    "detailed_geometric_instructions": "Draw a circle...",
    "standard_components": [],
    "estimated_dimensions": {"length": 10.0, "width": 10.0, "height": 5.0}
})

@pytest.fixture
def mock_db_session():
    """Creates a fake AsyncSession that mocks all DB methods."""
    session = AsyncMock()

    mock_result = MagicMock()
    session.execute.return_value = mock_result

    session.add = MagicMock()
    session.commit = AsyncMock()

    # Simulate db.refresh() populating ID and timestamps
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

    # Use ASGITransport to talk directly to the app without a real network call
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as ac:
        yield ac

    app.dependency_overrides.clear()

# --- TESTS ---
@pytest.mark.asyncio
@patch("app.api.routes.gemini_routes.get_chat_history")
@patch("app.api.routes.gemini_routes.client")
async def test_process_requirements_success(
    mock_client_instance,
    mock_get_history,
    client,
    mock_db_session
):
    # 1. Setup Mock DB (Chat exists)
    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = MOCK_CHAT_ID
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = MOCK_USER_ID
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    # 2. Setup Mock Helpers
    mock_get_history.return_value = [{"role": "user", "parts": ["hi"]}]

    # 3. Setup Gemini Client Mock
    mock_response = MagicMock()
    mock_response.text = VALID_GEMINI_RESPONSE

    mock_client_instance.models.generate_content.return_value = mock_response

    # 4. Execute
    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)

    # 5. Assertions
    assert response.status_code == 200, f"Error: {response.text}"
    data = response.json()
    assert data["role"] == "assistant"

    # Verify DB interactions
    assert mock_db_session.add.call_count == 2
    assert mock_db_session.commit.call_count == 2
    assert mock_db_session.refresh.call_count == 1

@pytest.mark.asyncio
async def test_process_requirements_chat_not_found(client, mock_db_session):
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = None
    # Added missing await
    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)
    assert response.status_code == 404
    assert response.json()["detail"] == "Chat not found"

@pytest.mark.asyncio
async def test_process_requirements_forbidden(client, mock_db_session):
    mock_chat = MagicMock(spec=Chat)
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = "other-user"
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)
    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied"

@pytest.mark.asyncio
@patch("app.api.routes.gemini_routes.get_chat_history")
@patch("app.api.routes.gemini_routes.client")
async def test_process_requirements_gemini_error(
    mock_client_instance,
    mock_get_history,
    client,
    mock_db_session
):
    # DB Setup
    mock_chat = MagicMock(spec=Chat)
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = MOCK_USER_ID
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    # Provide a dummy return for get_chat_history to avoid iteration over MagicMock
    mock_get_history.return_value = []

    # Mock Error
    mock_client_instance.models.generate_content.side_effect = Exception("Google API Error")

    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)

    assert response.status_code == 500
    assert "Google API Error" in response.json()["detail"]

@pytest.mark.asyncio
@patch("app.api.routes.gemini_routes.get_chat_history")
@patch("app.api.routes.gemini_routes.client")
async def test_process_requirements_invalid_json_response(
    mock_client_instance,
    mock_get_history,
    client,
    mock_db_session
):
    # DB Setup
    mock_chat = MagicMock(spec=Chat)
    mock_chat.session = MagicMock(spec=UserSession)
    mock_chat.session.user_id = MOCK_USER_ID
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_chat

    # Provide a dummy return for get_chat_history
    mock_get_history.return_value = []

    # Mock Invalid JSON
    mock_response = MagicMock()
    mock_response.text = "This is not JSON"
    mock_client_instance.models.generate_content.return_value = mock_response

    response = await client.post(ENDPOINT_URL, json=VALID_PAYLOAD)

    assert response.status_code == 500
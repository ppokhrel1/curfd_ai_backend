"""Tests for the /init combined endpoint."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.session import get_db
from app.core.deps import get_current_user_id
from app.models.session import Session as SessionModel
from app.models.chat import Chat as ChatModel

ENDPOINT_URL = "/api/v1/init"
MOCK_USER_ID = "user-456"


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    return session


@pytest.fixture
async def client(mock_db_session):
    async def override_get_db():
        yield mock_db_session

    def override_get_user():
        return MOCK_USER_ID

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_id] = override_get_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


def _make_session(id: str, user_id: str = MOCK_USER_ID) -> MagicMock:
    s = MagicMock(spec=SessionModel)
    s.id = id
    s.user_id = user_id
    s.name = f"Session {id}"
    s.status = "active"
    s.last_active_at = datetime.now(timezone.utc)
    s.created_at = datetime.now(timezone.utc)
    s.updated_at = datetime.now(timezone.utc)
    s.metadata_json = None
    return s


def _make_chat(id: str, session_id: str, title: str) -> MagicMock:
    c = MagicMock(spec=ChatModel)
    c.id = id
    c.session_id = session_id
    c.title = title
    c.created_at = datetime.now(timezone.utc)
    c.updated_at = datetime.now(timezone.utc)
    return c


@pytest.mark.asyncio
async def test_init_returns_sessions_and_chats(client, mock_db_session):
    """Test that /init returns all sessions and chats in one response."""
    sessions = [_make_session("s1"), _make_session("s2")]
    chats = [
        _make_chat("c1", "s1", "Chat One"),
        _make_chat("c2", "s1", "Chat Two"),
        _make_chat("c3", "s2", "Chat Three"),
    ]

    # First execute returns sessions, second returns chats
    mock_result_sessions = MagicMock()
    mock_result_sessions.scalars.return_value.all.return_value = sessions

    mock_result_chats = MagicMock()
    mock_result_chats.scalars.return_value.all.return_value = chats

    mock_db_session.execute.side_effect = [mock_result_sessions, mock_result_chats]

    response = await client.get(ENDPOINT_URL)
    assert response.status_code == 200

    data = response.json()
    assert len(data["sessions"]) == 2
    assert len(data["chats"]) == 3
    assert data["sessions"][0]["id"] == "s1"
    assert data["chats"][0]["title"] == "Chat One"


@pytest.mark.asyncio
async def test_init_no_sessions(client, mock_db_session):
    """Test /init with no sessions returns empty lists."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db_session.execute.return_value = mock_result

    response = await client.get(ENDPOINT_URL)
    assert response.status_code == 200

    data = response.json()
    assert data["sessions"] == []
    assert data["chats"] == []
    # Should only call execute once (sessions query), skip chats query
    assert mock_db_session.execute.call_count == 1


@pytest.mark.asyncio
async def test_init_sessions_with_no_chats(client, mock_db_session):
    """Test /init with sessions but no chats."""
    sessions = [_make_session("s1")]

    mock_result_sessions = MagicMock()
    mock_result_sessions.scalars.return_value.all.return_value = sessions

    mock_result_chats = MagicMock()
    mock_result_chats.scalars.return_value.all.return_value = []

    mock_db_session.execute.side_effect = [mock_result_sessions, mock_result_chats]

    response = await client.get(ENDPOINT_URL)
    assert response.status_code == 200

    data = response.json()
    assert len(data["sessions"]) == 1
    assert data["chats"] == []

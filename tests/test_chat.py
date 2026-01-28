"""
Tests for Chat Session Endpoints

Covers Requirements 2.1-2.5
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_chat_session(client: AsyncClient, auth_headers):
    """Test creating a chat session (Req 2.1)."""
    response = await client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "Test Chat"}
    )
    
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["title"] == "Test Chat"
    assert data["message_count"] == 0


@pytest.mark.asyncio
async def test_list_user_sessions(client: AsyncClient, auth_headers):
    """Test listing user sessions (Req 2.4)."""
    # Create a session first
    await client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "Session 1"}
    )
    
    response = await client.get(
        "/api/v1/chat/sessions",
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_send_message_to_session(client: AsyncClient, auth_headers):
    """Test sending a message (Req 2.2)."""
    # Create session
    create_response = await client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "Message Test"}
    )
    session_id = create_response.json()["id"]
    
    # Send message
    response = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers,
        json={"content": "Hello, create a robot arm"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "user_message" in data
    assert "assistant_message" in data
    assert data["user_message"]["content"] == "Hello, create a robot arm"


@pytest.mark.asyncio
async def test_get_chat_history(client: AsyncClient, auth_headers):
    """Test getting chat history in order (Req 2.3)."""
    # Create session
    create_response = await client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "History Test"}
    )
    session_id = create_response.json()["id"]
    
    # Send messages
    await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers,
        json={"content": "First message"}
    )
    await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers,
        json={"content": "Second message"}
    )
    
    # Get history
    response = await client.get(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers
    )
    
    assert response.status_code == 200
    messages = response.json()
    assert len(messages) >= 4  # 2 user + 2 assistant
    # Verify chronological order
    for i in range(len(messages) - 1):
        assert messages[i]["created_at"] <= messages[i + 1]["created_at"]


@pytest.mark.asyncio
async def test_delete_chat_session(client: AsyncClient, auth_headers):
    """Test deleting a session (Req 2.5)."""
    # Create session
    create_response = await client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "Delete Test"}
    )
    session_id = create_response.json()["id"]
    
    # Delete session
    response = await client.delete(
        f"/api/v1/chat/sessions/{session_id}",
        headers=auth_headers
    )
    
    assert response.status_code == 204
    
    # Verify deleted
    get_response = await client.get(
        f"/api/v1/chat/sessions/{session_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 404

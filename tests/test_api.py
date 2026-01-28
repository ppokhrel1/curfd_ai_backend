"""
Tests for Model Generation Endpoints

Covers Requirements 3.1-3.5, 4.1-4.2
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_trigger_model_generation(client: AsyncClient, auth_headers):
    """Test triggering model generation (Req 3.1)."""
    # Create chat session first
    session_response = await client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "Generation Test"}
    )
    session_id = session_response.json()["id"]
    
    # Trigger generation
    response = await client.post(
        "/api/v1/models/generate",
        headers=auth_headers,
        json={
            "session_id": session_id,
            "prompt": "Create a 6-DOF robotic arm"
        }
    )
    
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_get_generation_status(client: AsyncClient, auth_headers):
    """Test getting generation job status (Req 3.5)."""
    # Create session and trigger generation
    session_response = await client.post(
        "/api/v1/chat/sessions",
        headers=auth_headers,
        json={"title": "Status Test"}
    )
    session_id = session_response.json()["id"]
    
    gen_response = await client.post(
        "/api/v1/models/generate",
        headers=auth_headers,
        json={
            "session_id": session_id,
            "prompt": "Create a car"
        }
    )
    job_id = gen_response.json()["job_id"]
    
    # Get status
    response = await client.get(
        f"/api/v1/models/{job_id}/status",
        headers=auth_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "progress" in data


@pytest.mark.asyncio
async def test_list_user_models(client: AsyncClient, auth_headers):
    """Test listing user models (Req 3.4)."""
    response = await client.get(
        "/api/v1/models",
        headers=auth_headers
    )
    
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    """Test health check endpoint."""
    response = await client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    """Test root endpoint."""
    response = await client.get("/")
    
    assert response.status_code == 200
    data = response.json()
    assert "Welcome" in data["message"]

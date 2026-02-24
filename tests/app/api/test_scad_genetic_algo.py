import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from fastapi import FastAPI
import httpx
from httpx import ASGITransport

from app.api.routes.scad_genetic_algo import router 
from app.core.deps import get_current_user_id_async
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.schemas.scad_job import ScadJob

# --- Setup App & Overrides ---
app = FastAPI()
app.include_router(router)

# Dummy payload for creation
VALID_PAYLOAD = {
    "chat_id": "chat-123",
    "openscad_code": "cube([10, 10, 10]);",
    "parameters": [{"name": "x", "min_val": 1.0, "max_val": 10.0}],
    "generations": 10,
    "population_size": 20
}

# Helper to generate fully valid mock db objects for ResponseValidation
def create_mock_job(status="Processing", worker_task_id="worker-abc"):
    return ScadJob(
        id="job-123",
        chat_id="chat-123",
        status=status,
        worker_task_id=worker_task_id,
        openscad_code="cube([10, 10, 10]);",
        parameters=[],
        generations=10,
        population_size=20,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc), # Fixed: Added missing created_at
        updated_at=datetime.now(timezone.utc)
    )

@pytest.fixture
def mock_db_session():
    """Creates a mock AsyncSession that properly handles sync vs async methods."""
    session = AsyncMock()
    
    # Fixed: Simulate DB auto-generating timestamps on add() so FastAPI validation passes
    def mock_add_side_effect(instance):
        instance.created_at = datetime.now(timezone.utc)
        instance.updated_at = datetime.now(timezone.utc)

    session.add = MagicMock(side_effect=mock_add_side_effect)
    
    mock_result = MagicMock()
    session.execute.return_value = mock_result
    return session

@pytest.fixture
def test_client(mock_db_session):
    """Provides an Async Test Client with mocked dependencies."""
    async def override_get_db():
        yield mock_db_session

    async def override_get_user():
        return "user-123"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_id_async] = override_get_user
    
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")

@pytest.fixture
def mock_httpx_client():
    """Mocks the httpx.AsyncClient context manager used in the router."""
    with patch("app.api.routes.scad_genetic_algo.httpx.AsyncClient") as mock_client_class:
        mock_instance = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_instance
        yield mock_instance

# --- Tests for POST /start ---

@pytest.mark.asyncio
async def test_start_job_success(test_client, mock_db_session, mock_httpx_client):
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = ChatModel(id="chat-123")
    
    # Mock Worker Response with a dummy Request attached for raise_for_status()
    mock_httpx_client.post.return_value = httpx.Response(
        status_code=200, 
        json={"task_id": "worker-task-abc"},
        request=httpx.Request("POST", "http://dummy")
    )

    response = await test_client.post("/optimization/start", json=VALID_PAYLOAD)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Processing"
    assert data["worker_task_id"] == "worker-task-abc"
    mock_db_session.add.assert_called_once()
    assert mock_db_session.commit.call_count == 2 

@pytest.mark.asyncio
async def test_start_job_unauthorized(test_client, mock_db_session):
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = None

    response = await test_client.post("/optimization/start", json=VALID_PAYLOAD)

    assert response.status_code == 403
    assert "Unauthorized" in response.json()["detail"]

@pytest.mark.asyncio
async def test_start_job_worker_unreachable(test_client, mock_db_session, mock_httpx_client):
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = ChatModel(id="chat-123")
    
    # Force httpx to raise a RequestError
    mock_httpx_client.post.side_effect = httpx.RequestError("Connection failed")

    response = await test_client.post("/optimization/start", json=VALID_PAYLOAD)

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]
    added_job = mock_db_session.add.call_args[0][0]
    assert added_job.status == "Failed"


# --- Tests for GET /status/{job_id} ---

@pytest.mark.asyncio
async def test_check_status_already_completed(test_client, mock_db_session, mock_httpx_client):
    # Use helper to generate a fully valid response model
    mock_job = create_mock_job(status="Completed")
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_job

    response = await test_client.get("/optimization/status/job-123")

    assert response.status_code == 200
    assert response.json()["status"] == "Completed"
    mock_httpx_client.get.assert_not_called()

@pytest.mark.asyncio
async def test_check_status_polling_completed(test_client, mock_db_session, mock_httpx_client):
    mock_job = create_mock_job(status="Processing")
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_job

    worker_resp_json = {
        "status": "Completed",
        "result": {
            "optimized_parameters": {"x": 5.0},
            "fitness_score": 0.99,
            "result_url": "https://b2.backblaze.com/file.stl"
        }
    }
    mock_httpx_client.get.return_value = httpx.Response(
        status_code=200, 
        json=worker_resp_json,
        request=httpx.Request("GET", "http://dummy")
    )

    response = await test_client.get("/optimization/status/job-123")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Completed"
    assert data["fitness_score"] == 0.99
    assert data["result_url"] == "https://b2.backblaze.com/file.stl"
    mock_db_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_check_status_worker_failed(test_client, mock_db_session, mock_httpx_client):
    mock_job = create_mock_job(status="Processing")
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_job

    mock_httpx_client.get.return_value = httpx.Response(
        status_code=500, 
        json={"detail": "Compilation failed"},
        request=httpx.Request("GET", "http://dummy")
    )

    response = await test_client.get("/optimization/status/job-123")

    assert response.status_code == 200 
    data = response.json()
    assert data["status"] == "Failed"
    assert "Compilation failed" in data["error"]

@pytest.mark.asyncio
async def test_check_status_missing_worker_id(test_client, mock_db_session):
    mock_job = create_mock_job(status="Processing", worker_task_id=None)
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = mock_job

    response = await test_client.get("/optimization/status/job-123")

    assert response.status_code == 500
    assert "No worker task ID" in response.json()["detail"]
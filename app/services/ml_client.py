"""
ML Service Client with Circuit Breaker Pattern

Implements:
- HTTP communication with ML Service (Req 6.1)
- Request timeouts (Req 6.3)
- Circuit breaker pattern (Req 6.3)
- 503 Service Unavailable responses (Req 6.2)
"""

import asyncio
import time
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

import httpx

from app.core.config import settings
from app.core.exceptions import (
    MLServiceUnavailable,
    MLServiceTimeout,
    MLGenerationFailed,
)


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascading failures when ML service is down.
    """

    failure_threshold: int = 5
    recovery_timeout: int = 60
    half_open_max_calls: int = 3

    failure_count: int = field(default=0, init=False)
    success_count: int = field(default=0, init=False)
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    last_failure_time: Optional[float] = field(default=None, init=False)

    def record_success(self) -> None:
        """Record a successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_max_calls:
                self._close()
        else:
            self.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self._open()

    def can_execute(self) -> bool:
        """Check if a call can be executed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self._half_open()
                return True
            return False

        # HALF_OPEN
        return True

    def _open(self) -> None:
        self.state = CircuitState.OPEN
        self.success_count = 0

    def _close(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0

    def _half_open(self) -> None:
        self.state = CircuitState.HALF_OPEN
        self.success_count = 0

    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return True
        return time.time() - self.last_failure_time >= self.recovery_timeout


class MLClient:
    """
    HTTP client for communicating with ML Service.

    Features:
    - Async HTTP calls with configurable timeout
    - Circuit breaker for fault tolerance
    - Proper error handling and retry info
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        api_key: Optional[str] = None,
    ):
        # Ensure base_url does NOT include /runsync
        self.base_url = (base_url or settings.ML_SERVICE_URL).rstrip("/")
        if self.base_url.endswith("/runsync"):
            self.base_url = self.base_url.replace("/runsync", "")

        # Timeout in seconds
        self.timeout = float(timeout or settings.ML_SERVICE_TIMEOUT or 180)
        self.api_key = api_key or settings.RUNPOD_API_KEY
        self.circuit_breaker = CircuitBreaker()

    async def _make_request(
        self, method: str, endpoint: str, unwrap_output: bool = True, **kwargs
    ) -> Any:
        """Make HTTP request with circuit breaker protection."""
        if not self.circuit_breaker.can_execute():
            raise MLServiceUnavailable(
                "ML service circuit breaker is open. Service is unavailable.",
                retry_after=self.circuit_breaker.recovery_timeout,
            )

        # Prepare headers
        headers = kwargs.pop("headers", {})
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["Content-Type"] = "application/json"

        # Wrap POST payload ONLY for RunPod endpoints
        if endpoint in ["/runsync", "/run"] and "json" in kwargs:
            kwargs["json"] = {"input": kwargs["json"]}

        # Safe URL joining
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

        # Debug logs
        print(f"ML_CLIENT: Requesting {method} {url}")
        if "json" in kwargs:
            print(f"ML_CLIENT: Payload -> {kwargs['json']}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                self.circuit_breaker.record_success()

                result = response.json()

                # Enhanced debug logging
                print(f"ML_CLIENT: Raw response keys -> {list(result.keys())}")
                if "output" in result:
                    print(f"ML_CLIENT: 'output' keys -> {list(result['output'].keys()) if isinstance(result['output'], dict) else 'none'}")
                
                # RunPod specific response handling
                if isinstance(result, dict):
                    # Check for 'output' or 'data' key (various RunPod handler patterns)
                    output = result.get("output") or result.get("data")
                    
                    if unwrap_output and output is not None:
                        # Merge top-level metadata into output if output is a dict
                        # This preserves tokens, job IDs, etc. that might be outside 'output'
                        if isinstance(output, dict):
                            for key in result:
                                if key not in ["output", "data", "input", "executionTime", "delayTime"] and key not in output:
                                    output[key] = result[key]
                        
                        return output
                    # Check for 'status' indicating async job
                    if result.get("status") == "IN_QUEUE" or result.get("status") == "IN_PROGRESS":
                        print(f"ML_CLIENT: Job still processing -> {result}")
                        return result
                    # Check for error status
                    if result.get("status") == "FAILED":
                        error_msg = result.get("error", "Unknown error from RunPod")
                        print(f"ML_CLIENT: RunPod error -> {error_msg}")
                        raise MLGenerationFailed(f"RunPod error: {error_msg}")

                return result

        except httpx.TimeoutException:
            self.circuit_breaker.record_failure()
            raise MLServiceTimeout(
                f"ML service request timed out after {self.timeout}s", retry_after=30
            )
        except httpx.ConnectError:
            self.circuit_breaker.record_failure()
            raise MLServiceUnavailable("Cannot connect to ML service", retry_after=30)
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                self.circuit_breaker.record_failure()
                raise MLServiceUnavailable(
                    f"ML service returned error: {e.response.status_code}",
                    retry_after=30,
                )
            # Don't count 4xx as circuit breaker failures
            raise MLGenerationFailed(
                f"ML service error: {e.response.text}",
                error_details={"status_code": e.response.status_code},
            )

    async def health_check(self) -> Dict[str, Any]:
        """Check ML service health."""
        try:
            return await self._make_request("GET", "/health")
        except Exception as e:
            return {"status": "unavailable", "detail": str(e)}

    async def generate_model(
        self, prompt: str, session_id: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Call ML service to generate 3D model."""
        payload = {
            "action": "generate",
            "prompt": prompt,
            "session_id": session_id
        }
        if context:
            payload["context"] = context
        return await self._make_request("POST", "/runsync", json=payload)

    async def get_generation_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a model generation job."""
        return await self._make_request("GET", f"/status/{job_id}", unwrap_output=False)

    async def get_tokens(self, asset_id: str) -> Dict[str, Any]:
        """Get signed tokens for a specific asset ID."""
        payload = {
            "action": "get_tokens",
            "asset_id": asset_id
        }
        return await self._make_request("POST", "/runsync", json=payload)

    async def chat_completion(
        self, messages: list, session_id: Optional[str] = None, stream: bool = False
    ) -> Dict[str, Any]:
        """Get chat completion from RunPod Serverless using the 'generate' action."""
        last_message = messages[-1]["content"] if messages else ""
        payload = {
            "action": "generate",
            "prompt": last_message,
        }
        if session_id:
            payload["session_id"] = session_id
        
        # Initial request
        response = await self._make_request("POST", "/runsync", json=payload)
        
        # Poll if job is async (IN_QUEUE or IN_PROGRESS)
        if isinstance(response, dict) and response.get("status") in ["IN_QUEUE", "IN_PROGRESS"]:
            job_id = response.get("id")
            if job_id:
                print(f"ML_CLIENT: Job {job_id} is async. Polling for completion...")
                return await self._wait_for_completion(job_id)
                
        return response

    async def _wait_for_completion(self, job_id: str, max_retries: int = None, delay: int = 2) -> Dict[str, Any]:
        """Poll job status until complete or failed."""
        import asyncio
        
        # Calculate retries based on configured timeout
        if max_retries is None:
            max_retries = int(self.timeout / delay) + 30 # Add buffer

        for _ in range(max_retries):
            await asyncio.sleep(delay)
            status_response = await self.get_generation_status(job_id)
            
            # Debug log
            print(f"ML_CLIENT: Polling {job_id} -> {status_response.get('status')}")
            
            status = status_response.get("status")
            if status == "COMPLETED":
                # Merge top-level metadata into output
                output = status_response.get("output", {})
                if isinstance(output, dict):
                    for key in status_response:
                        if key not in ["output", "input"] and key not in output:
                            output[key] = status_response[key]
                return output
            elif status == "FAILED":
                raise MLGenerationFailed(f"Job failed: {status_response.get('error')}")
            
        raise MLServiceTimeout("Job timed out while polling")

# Global instance
ml_client = MLClient()

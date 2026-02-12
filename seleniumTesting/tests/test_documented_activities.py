from __future__ import annotations

import json
import time
from uuid import uuid4

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait


def _set_input(driver: WebDriver, element_id: str, value: str) -> None:
    el = driver.find_element(By.ID, element_id)
    el.clear()
    el.send_keys(value)


def _parse_json(text: str) -> dict | list:
    if not text.strip():
        return {}
    return json.loads(text)


def _api_base_from_harness(driver: WebDriver) -> str:
    return driver.find_element(By.ID, "api-base-url").get_attribute("value").rstrip("/")


def _http_to_ws(api_base_url: str) -> str:
    if api_base_url.startswith("https://"):
        return "wss://" + api_base_url[len("https://") :]
    if api_base_url.startswith("http://"):
        return "ws://" + api_base_url[len("http://") :]
    return api_base_url


def _browser_fetch(
    driver: WebDriver,
    *,
    method: str,
    url: str,
    body: dict | None = None,
    token: str | None = None,
    timeout_seconds: int = 20,
) -> dict:
    script = """
const done = arguments[0];
const method = arguments[1];
const url = arguments[2];
const body = arguments[3];
const token = arguments[4];
const timeoutMs = arguments[5];

const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), timeoutMs);

const headers = {"Content-Type": "application/json"};
if (token) headers["Authorization"] = `Bearer ${token}`;

fetch(url, {
  method,
  headers,
  body: body ? JSON.stringify(body) : undefined,
  signal: controller.signal
}).then(async (resp) => {
  clearTimeout(timer);
  const raw = await resp.text();
  let jsonBody = null;
  try { jsonBody = raw ? JSON.parse(raw) : null; } catch (_) {}
  done({ok: resp.ok, status: resp.status, raw, json: jsonBody});
}).catch((err) => {
  clearTimeout(timer);
  done({ok: false, status: 0, error: String(err)});
});
"""
    driver.set_script_timeout(timeout_seconds)
    return driver.execute_async_script(
        script, method.upper(), url, body, token, timeout_seconds * 1000
    )


def _browser_ws_runpod_request(
    driver: WebDriver,
    *,
    ws_url: str,
    payload: dict,
    timeout_seconds: int = 20,
) -> dict:
    script = """
const done = arguments[0];
const wsUrl = arguments[1];
const payload = arguments[2];
const timeoutMs = arguments[3];

let finished = false;
const ws = new WebSocket(wsUrl);
const timer = setTimeout(() => {
  if (finished) return;
  finished = true;
  try { ws.close(); } catch (_) {}
  done({ok: false, timeout: true});
}, timeoutMs);

ws.onopen = () => {
  ws.send(JSON.stringify({type: "runpod.request", payload}));
};

ws.onmessage = (event) => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  let data = null;
  try { data = JSON.parse(event.data); } catch (_) { data = {raw: event.data}; }
  try { ws.close(); } catch (_) {}
  done({ok: true, data});
};

ws.onerror = (event) => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  done({ok: false, error: "websocket error", eventType: event.type});
};

ws.onclose = () => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  done({ok: false, closed: true});
};
"""
    driver.set_script_timeout(timeout_seconds)
    return driver.execute_async_script(script, ws_url, payload, timeout_seconds * 1000)


def _run_harness_login(
    driver: WebDriver, harness_url: str, api_base_url: str, email: str, password: str
) -> dict:
    driver.get(harness_url)
    _set_input(driver, "api-base-url", api_base_url)
    _set_input(driver, "email", email)
    _set_input(driver, "password", password)
    driver.find_element(By.ID, "login-btn").click()
    WebDriverWait(driver, 20).until(
        lambda d: d.find_element(By.ID, "login-status").text.strip() != ""
    )
    status = driver.find_element(By.ID, "login-status").text.strip()
    payload = _parse_json(driver.find_element(By.ID, "login-response").text)
    return {"status": int(status), "payload": payload}


def test_documented_backend_activities_via_selenium(
    driver: WebDriver,
    harness_url: str,
    api_base_url: str,
    test_email: str,
    test_password: str,
    enable_runpod_tests: bool,
) -> None:
    # Health
    health = _browser_fetch(driver, method="GET", url=f"{api_base_url}/health")
    assert health["status"] == 200, health
    assert (health.get("json") or {}).get("status") == "ok"

    # Auth login
    login = _run_harness_login(driver, harness_url, api_base_url, test_email, test_password)
    assert login["status"] == 200, login
    token = login["payload"].get("access_token")
    assert token, login

    # Auth me
    me = _browser_fetch(driver, method="GET", url=f"{api_base_url}/auth/me", token=token)
    assert me["status"] == 200, me
    current_user_id = (me.get("json") or {}).get("user_id")
    assert current_user_id

    # Auth register (documented activity) with random email
    reg_email = f"selenium_{uuid4().hex[:10]}@example.com"
    register = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/auth/register",
        body={"email": reg_email, "password": "TempPass123@", "display_name": "Selenium"},
    )
    assert register["status"] in {201, 409}, register

    # Sessions
    session_name = f"Selenium Session {int(time.time())}"
    session_create = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/sessions",
        body={"name": session_name, "status": "active", "metadata_json": {"source": "selenium"}},
        token=token,
    )
    assert session_create["status"] in {200, 201}, session_create
    session = session_create["json"]
    assert session and session.get("id"), session_create
    session_id = session["id"]

    sessions_list = _browser_fetch(driver, method="GET", url=f"{api_base_url}/sessions", token=token)
    assert sessions_list["status"] == 200, sessions_list
    assert any(item.get("id") == session_id for item in (sessions_list.get("json") or []))

    session_get = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/sessions/{session_id}", token=token
    )
    assert session_get["status"] == 200, session_get

    session_patch = _browser_fetch(
        driver,
        method="PATCH",
        url=f"{api_base_url}/sessions/{session_id}",
        body={"name": f"{session_name} Updated", "status": "inactive"},
        token=token,
    )
    assert session_patch["status"] == 200, session_patch
    assert (session_patch.get("json") or {}).get("name") == f"{session_name} Updated"

    # Chats
    chat_title = f"Selenium Chat {uuid4().hex[:8]}"
    chat_create = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/chats",
        body={"session_id": session_id, "title": chat_title},
        token=token,
    )
    assert chat_create["status"] in {200, 201}, chat_create
    chat = chat_create["json"]
    assert chat and chat.get("id"), chat_create
    chat_id = chat["id"]

    chats_list = _browser_fetch(
        driver,
        method="GET",
        url=f"{api_base_url}/chats?session_id={session_id}",
        token=token,
    )
    assert chats_list["status"] == 200, chats_list
    assert any(item.get("id") == chat_id for item in (chats_list.get("json") or []))

    chat_get = _browser_fetch(driver, method="GET", url=f"{api_base_url}/chats/{chat_id}", token=token)
    assert chat_get["status"] == 200, chat_get

    chat_patch = _browser_fetch(
        driver,
        method="PATCH",
        url=f"{api_base_url}/chats/{chat_id}",
        body={"title": f"{chat_title} Updated"},
        token=token,
    )
    assert chat_patch["status"] == 200, chat_patch

    # Messages
    msg_create = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/messages",
        body={"chat_id": chat_id, "role": "user", "content": "Hello", "tokens": 5},
        token=token,
    )
    assert msg_create["status"] in {200, 201}, msg_create
    message = msg_create["json"]
    assert message and message.get("id"), msg_create
    message_id = message["id"]

    msgs_list = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/messages?chat_id={chat_id}", token=token
    )
    assert msgs_list["status"] == 200, msgs_list
    assert any(item.get("id") == message_id for item in (msgs_list.get("json") or []))

    msg_get = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/messages/{message_id}", token=token
    )
    assert msg_get["status"] == 200, msg_get

    # Jobs
    job_create = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/jobs",
        body={"session_id": session_id, "prompt": "a red chair", "output_format": "glb"},
        token=token,
    )
    assert job_create["status"] in {200, 201}, job_create
    job = job_create["json"]
    assert job and job.get("id"), job_create
    job_id = job["id"]

    jobs_list = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/jobs?session_id={session_id}", token=token
    )
    assert jobs_list["status"] == 200, jobs_list
    assert any(item.get("id") == job_id for item in (jobs_list.get("json") or []))

    job_get = _browser_fetch(driver, method="GET", url=f"{api_base_url}/jobs/{job_id}", token=token)
    assert job_get["status"] == 200, job_get

    job_patch = _browser_fetch(
        driver,
        method="PATCH",
        url=f"{api_base_url}/jobs/{job_id}",
        body={"status": "running"},
        token=token,
    )
    assert job_patch["status"] == 200, job_patch

    job_start = _browser_fetch(
        driver, method="POST", url=f"{api_base_url}/jobs/{job_id}/start", token=token
    )
    assert job_start["status"] == 200, job_start

    job_complete = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/jobs/{job_id}/complete?success=true",
        token=token,
    )
    assert job_complete["status"] == 200, job_complete

    # Assets
    asset_create = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/assets",
        body={"job_id": job_id, "asset_type": "glb", "uri": "s3://bucket/file.glb"},
        token=token,
    )
    assert asset_create["status"] in {200, 201}, asset_create
    asset = asset_create["json"]
    assert asset and asset.get("id"), asset_create
    asset_id = asset["id"]

    assets_list = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/assets?job_id={job_id}", token=token
    )
    assert assets_list["status"] == 200, assets_list
    assert any(item.get("id") == asset_id for item in (assets_list.get("json") or []))

    asset_get = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/assets/{asset_id}", token=token
    )
    assert asset_get["status"] == 200, asset_get

    # Asset Meta
    asset_meta_create = _browser_fetch(
        driver,
        method="POST",
        url=f"{api_base_url}/asset-meta",
        body={
            "asset_id": asset_id,
            "part_name": "propeller",
            "component_of": asset_id,
            "position_json": {"x": 0, "y": 0, "z": 0},
            "image_paths_json": [{"face_direction": [0, 0, 0, 1], "image_src": ""}],
            "material_json": {"type": "plastic", "thickness": "10mm"},
            "is_composite_of": [asset_id],
            "used_for_json": ["build air turbulence", "engine coupling"],
        },
        token=token,
    )
    assert asset_meta_create["status"] in {200, 201}, asset_meta_create
    meta = asset_meta_create["json"]
    assert meta and meta.get("id"), asset_meta_create
    meta_id = meta["id"]

    asset_meta_list = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/asset-meta?asset_id={asset_id}", token=token
    )
    assert asset_meta_list["status"] == 200, asset_meta_list
    assert any(item.get("id") == meta_id for item in (asset_meta_list.get("json") or []))

    asset_meta_get = _browser_fetch(
        driver, method="GET", url=f"{api_base_url}/asset-meta/{meta_id}", token=token
    )
    assert asset_meta_get["status"] == 200, asset_meta_get

    asset_meta_patch = _browser_fetch(
        driver,
        method="PATCH",
        url=f"{api_base_url}/asset-meta/{meta_id}",
        body={"part_name": "propeller v2", "material_json": {"type": "carbon", "thickness": "8mm"}},
        token=token,
    )
    assert asset_meta_patch["status"] == 200, asset_meta_patch

    # Chat stream + runpod activities (optional)
    if enable_runpod_tests:
        runpod_health = _browser_fetch(
            driver,
            method="POST",
            url=f"{api_base_url}/chats/{chat_id}/runpod",
            body={"action": "health", "sync": True},
            token=token,
            timeout_seconds=60,
        )
        assert runpod_health["status"] in {200, 202, 500, 502}, runpod_health

        runpod_process = _browser_fetch(
            driver,
            method="POST",
            url=f"{api_base_url}/chats/{chat_id}/runpod",
            body={"action": "process_requirements", "content": "Make a table for flying"},
            token=token,
            timeout_seconds=60,
        )
        assert runpod_process["status"] in {200, 202, 500, 502}, runpod_process

        runpod_generate_scad = _browser_fetch(
            driver,
            method="POST",
            url=f"{api_base_url}/chats/{chat_id}/runpod",
            body={
                "action": "generate_scad",
                "content": "Generate SCAD",
                "requirements_json": {"constraints": {"environment": "outdoor", "size": "medium"}},
            },
            token=token,
            timeout_seconds=60,
        )
        assert runpod_generate_scad["status"] in {200, 202, 500, 502}, runpod_generate_scad

        ws_base = _http_to_ws(_api_base_from_harness(driver))
        ws_url = f"{ws_base}/chat-socket/{chat_id}?token={token}"
        ws_result = _browser_ws_runpod_request(
            driver,
            ws_url=ws_url,
            payload={"action": "health", "sync": True},
            timeout_seconds=60,
        )
        assert ws_result.get("ok") is True, ws_result

    # Delete cleanup + remaining documented activities
    msg_delete = _browser_fetch(
        driver, method="DELETE", url=f"{api_base_url}/messages/{message_id}", token=token
    )
    assert msg_delete["status"] in {200, 204}, msg_delete

    meta_delete = _browser_fetch(
        driver, method="DELETE", url=f"{api_base_url}/asset-meta/{meta_id}", token=token
    )
    assert meta_delete["status"] in {200, 204}, meta_delete

    asset_delete = _browser_fetch(
        driver, method="DELETE", url=f"{api_base_url}/assets/{asset_id}", token=token
    )
    assert asset_delete["status"] in {200, 204}, asset_delete

    chat_delete = _browser_fetch(
        driver, method="DELETE", url=f"{api_base_url}/chats/{chat_id}", token=token
    )
    assert chat_delete["status"] in {200, 204}, chat_delete

    session_delete = _browser_fetch(
        driver, method="DELETE", url=f"{api_base_url}/sessions/{session_id}", token=token
    )
    assert session_delete["status"] in {200, 204}, session_delete

    logout = _browser_fetch(driver, method="GET", url=f"{api_base_url}/auth/logout", token=token)
    assert logout["status"] == 200, logout

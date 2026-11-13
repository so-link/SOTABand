"""API 只读/分支路由覆盖：所有调用计入覆盖台账。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _allowed(response, allowed: set[int], label: str):
    assert response.status in allowed, f"{label} → HTTP {response.status}: {response.text[:500]}"
    return response


def test_health_and_platform_reads(api, admin_api, fixtures):
    _allowed(api.get("/api/health"), {200}, "health")
    _allowed(api.get("/api/apis/list"), {200}, "apis/list")
    _allowed(api.get("/api/migrate/info"), {200}, "migrate/info")
    _allowed(admin_api.get("/api/llm/config"), {200}, "llm/config")
    _allowed(api.get("/api/task/list", params={"limit": 10}), {200}, "task/list")
    _allowed(api.get("/api/task/recent-agents", params={"limit": 10}), {200}, "task/recent-agents")
    _allowed(api.get("/api/task-jobs/list"), {200}, "task-jobs/list")
    _allowed(api.get("/api/task-jobs/not-exists-e2e"), {404}, "task-jobs/{id}")
    _allowed(api.post("/api/task-jobs/not-exists-e2e/cancel"), {404}, "task-jobs cancel")
    _allowed(api.delete("/api/task/999999999"), {404}, "task delete")
    _allowed(api.get("/api/model/not-exists-e2e"), {404}, "model get")

    agent_id = (fixtures.get("agents") or [])[0]["id"]
    _allowed(api.get("/api/agent/bus/status"), {200}, "agent bus status")
    _allowed(api.get(f"/api/agent/{agent_id}/fork-status"), {200, 400}, "agent fork-status")
    _allowed(api.get(f"/api/agent/{agent_id}/service"), {200, 400}, "agent service")
    _allowed(api.get(f"/api/agent/{agent_id}/api-doc"), {200, 400}, "agent api-doc")
    _allowed(api.get(f"/api/agent/{agent_id}/export"), {200}, "agent export")
    _allowed(
        api.post(f"/api/agent/{agent_id}/test-api", json_body={"api_name": "ping", "params": {}}),
        {200, 400, 404, 422},
        "agent test-api",
    )
    _allowed(api.delete(f"/api/agent/{agent_id}/chat-history"), {200}, "agent chat-history delete")


def test_data_reads(api, fixtures):
    dataset = (fixtures.get("datasets") or [])[0]
    dataset_id = dataset["id"]
    _allowed(api.get("/api/data/search/find", params={"q": dataset["name"]}), {200}, "data search")
    _allowed(api.get("/api/data/repository"), {200}, "data repository")
    _allowed(api.get(f"/api/data/{dataset_id}"), {200}, "data get")
    _allowed(api.get(f"/api/data/{dataset_id}/files"), {200}, "data files")
    _allowed(api.get(f"/api/data/{dataset_id}/archive"), {200}, "data archive")

    preview = api.get(f"/api/data/{dataset_id}/preview")
    _allowed(preview, {200}, "data preview")
    _allowed(api.get("/api/data/not-exists-e2e/preview"), {404}, "data preview 404")
    files = (preview.json() or {}).get("files") or []
    if files:
        path = files[0].get("path") or files[0].get("name")
        _allowed(
            api.get(f"/api/data/{dataset_id}/raw", params={"path": path}),
            {200},
            "data raw",
        )

    _allowed(api.get("/api/file"), {200}, "file list")


def test_tool_and_chat_reads(api, fixtures):
    tool_id = (fixtures.get("tools") or [])[0]["id"]
    _allowed(api.get("/api/tool/search/find", params={"q": "calc"}), {200}, "tool search")
    _allowed(api.get(f"/api/tool/{tool_id}/fork-status"), {200, 400}, "tool fork-status")
    _allowed(api.get(f"/api/tool/{tool_id}/check-deps"), {200, 400}, "tool check-deps")
    _allowed(api.get(f"/api/tool/{tool_id}/export"), {200}, "tool export")

    _allowed(api.get("/api/chat/platform-call/pending"), {200}, "platform pending")
    _allowed(api.get("/api/chat/platform-ops"), {200}, "platform ops")
    _allowed(
        api.post(
            "/api/chat/platform-call",
            json_body={"tool": "not-exists-e2e", "args": {}, "session_id": "e2e-read"},
        ),
        {200, 400, 404, 422},
        "platform call",
    )
    _allowed(
        api.post(
            "/api/chat/platform-call/cancel",
            json_body={"opId": "not-exists-e2e"},
        ),
        {200, 400, 404, 422},
        "platform cancel",
    )
    _allowed(
        api.post(
            "/api/chat/platform-call/confirm",
            json_body={"opId": "not-exists-e2e", "token": "x", "fingerprint": "y"},
        ),
        {200, 400, 404, 422},
        "platform confirm",
    )
    _allowed(api.delete("/api/chat/sessions/e2e-not-exists"), {200}, "chat session delete")


def test_auth_reads_and_prefs(api, admin_api, fixtures):
    _allowed(api.get("/api/auth/prefs"), {200}, "auth prefs get")
    _allowed(api.put("/api/auth/prefs", json_body={"e2e_probe": "1"}), {200}, "auth prefs put")
    _allowed(
        api.put("/api/auth/me", json_body={"displayName": "E2E 用户"}),
        {200},
        "auth me put",
    )
    _allowed(admin_api.get("/api/auth/superuser"), {200}, "auth superuser")
    system = admin_api.post("/api/auth/act-as-system")
    _allowed(system, {200}, "auth act-as-system")
    assert (system.json() or {}).get("token")

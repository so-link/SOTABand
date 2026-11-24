"""Web 服务式 Agent 的路径参数支持测试。

覆盖两块：

- 宿主路由（``core.agent.web_server``）：精确匹配优先、``{name}`` 模板匹配、
  路径参数注入并优先于 body/query、``result.data.status`` 透传为 HTTP 状态码；
- 界面测试代理（``app.api.routes.agent_routes._fill_path_params``）：路径替换、
  URL 编码、剔除已消费参数、缺参报错。
"""

from __future__ import annotations

import asyncio

import pytest


# ── 宿主路由：模板匹配 ──────────────────────────────────────────────


def test_match_api_path_exact():
    from core.agent.web_server import match_api_path

    assert match_api_path("/api/v1/models", "/api/v1/models") == {}
    assert match_api_path("/api/v1/models", "/api/v1/other") is None
    assert match_api_path("/api/v1/models", "/api/v1/models/extra") is None


def test_match_api_path_params():
    from core.agent.web_server import match_api_path

    assert match_api_path(
        "/api/v1/recognition/tasks/{task_id}",
        "/api/v1/recognition/tasks/task-0001",
    ) == {"task_id": "task-0001"}
    assert match_api_path(
        "/api/v1/recognition/tasks/{task_id}/stop",
        "/api/v1/recognition/tasks/task-0001/stop",
    ) == {"task_id": "task-0001"}
    # 路径参数做 URL 解码
    assert match_api_path("/tasks/{task_id}", "/tasks/a%2Fb") == {"task_id": "a/b"}


def test_match_api_path_rejects_mismatch():
    from core.agent.web_server import match_api_path

    assert match_api_path("/tasks/{task_id}", "/tasks/") is None
    assert match_api_path("/tasks/{task_id}", "/tasks/x/stop") is None
    assert match_api_path("/tasks/{task_id}/stop", "/tasks/x") is None


# ── 宿主路由：HTTP 集成 ─────────────────────────────────────────────


class _FakeSpec:
    def __init__(self, apis):
        self.apis = apis


class _RecordingAgent:
    agent_id = "fake-agent"

    def __init__(self):
        self.spec = _FakeSpec(
            [
                {"name": "list", "method": "GET", "path": "/api/v1/models"},
                {
                    "name": "get",
                    "method": "GET",
                    "path": "/api/v1/recognition/tasks/{task_id}",
                },
                {
                    "name": "stop",
                    "method": "POST",
                    "path": "/api/v1/recognition/tasks/{task_id}/stop",
                },
            ]
        )
        self.calls: list[dict] = []

    async def execute(self, ctx, **kwargs):
        params = dict(kwargs.get("params") or {})
        self.calls.append({"api_name": kwargs.get("api_name"), "params": params})
        if kwargs.get("api_name") == "stop":
            # 顺带验证 result.status → HTTP 状态码
            yield {
                "event": "result",
                "data": {"response": {"task_id": params.get("task_id")}, "status": 409},
            }
        else:
            yield {
                "event": "result",
                "data": {
                    "response": {
                        "api": kwargs.get("api_name"),
                        "params": params,
                    }
                },
            }


async def _with_host(fn):
    """启动一个临时 WebServiceHost，跑完 fn(base_url) 后清理。"""
    httpx = pytest.importorskip("httpx")
    from core.agent.web_server import WebServiceHost

    agent = _RecordingAgent()
    host = WebServiceHost(agent, port=0)
    port = await host.start()
    task = asyncio.ensure_future(host.serve_forever())
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await fn(client, f"http://127.0.0.1:{port}", agent)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await host.stop()


async def test_host_injects_path_param():
    async def scenario(client, base, agent):
        r = await client.get(f"{base}/api/v1/recognition/tasks/task-0001")
        assert r.status_code == 200, r.text
        assert r.json()["params"] == {"task_id": "task-0001"}

        # URL 编码的路径参数会被解码后交给 Agent
        r = await client.get(f"{base}/api/v1/recognition/tasks/task%2F1")
        assert r.status_code == 200, r.text
        assert r.json()["params"] == {"task_id": "task/1"}

    await _with_host(scenario)


async def test_path_param_wins_over_body_and_status_passthrough():
    async def scenario(client, base, agent):
        r = await client.post(
            f"{base}/api/v1/recognition/tasks/task-0001/stop",
            json={"task_id": "forged"},
        )
        # result.data.status=409 被透传为 HTTP 状态码
        assert r.status_code == 409, r.text
        # 路径值优先于 body 里的同名伪造值
        assert r.json()["task_id"] == "task-0001"

    await _with_host(scenario)


async def test_exact_path_and_query_unchanged():
    async def scenario(client, base, agent):
        r = await client.get(f"{base}/api/v1/models?foo=bar")
        assert r.status_code == 200, r.text
        assert r.json()["params"] == {"foo": "bar"}

    await _with_host(scenario)


async def test_unknown_path_still_404():
    async def scenario(client, base, agent):
        r = await client.get(f"{base}/api/v1/unknown")
        assert r.status_code == 404, r.text

    await _with_host(scenario)


# ── 界面测试代理：路径参数填充 ───────────────────────────────────────


def test_fill_path_params_replaces_and_encodes():
    from app.api.routes.agent_routes import _fill_path_params

    filled, rest = _fill_path_params(
        "/api/v1/recognition/tasks/{task_id}",
        {"task_id": "task 1/2", "keep": 1},
    )
    assert filled == "/api/v1/recognition/tasks/task%201%2F2"
    assert rest == {"keep": 1}


def test_fill_path_params_without_placeholder_is_noop():
    from app.api.routes.agent_routes import _fill_path_params

    filled, rest = _fill_path_params("/api/v1/models", {"a": 1})
    assert filled == "/api/v1/models"
    assert rest == {"a": 1}


def test_fill_path_params_missing_raises():
    from app.api.routes.agent_routes import _fill_path_params

    with pytest.raises(ValueError) as exc:
        _fill_path_params("/tasks/{task_id}/stop", {})
    assert "task_id" in str(exc.value)

    with pytest.raises(ValueError):
        _fill_path_params("/tasks/{task_id}", {"task_id": ""})

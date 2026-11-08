"""Agent Web 服务鉴权（P1-13）测试。

Web 服务式 Agent 的 HTTP 端口是本机**第二个入口**：若不鉴权，任何能访问
端口的人都能绕过引擎登录直接执行 Agent 流程；且 host 来自可编辑的 agent
spec，可被改成 0.0.0.0 对外暴露。因此：

- 强制监听 127.0.0.1（忽略 spec host）；
- 除 ``/_health`` 外必须带引擎共享令牌；
- 令牌由引擎 spawn 时注入（``SOTABAND_AGENT_HTTP_TOKEN``）。
"""

from __future__ import annotations

import asyncio

import pytest


class _FakeSpec:
    def __init__(self, apis):
        self.apis = apis


class _FakeAgent:
    agent_id = "fake-agent"

    def __init__(self):
        self.spec = _FakeSpec(
            [{"name": "greet", "method": "POST", "path": "/greet"}]
        )

    async def execute(self, ctx, **kwargs):
        yield {
            "event": "result",
            "data": {"response": {"ok": True, "api": kwargs.get("api_name")}},
        }


@pytest.mark.asyncio
async def test_web_service_requires_engine_token():
    hop = pytest.importorskip("httpx")
    from core.agent.web_server import WebServiceHost

    host = WebServiceHost(_FakeAgent(), port=0, token="secret-token")
    port = await host.start()
    task = asyncio.ensure_future(host.serve_forever())
    try:
        base = f"http://127.0.0.1:{port}"
        async with hop.AsyncClient(timeout=5) as client:
            # 无令牌 → 401
            r = await client.post(f"{base}/greet", json={"name": "x"})
            assert r.status_code == 401
            # 错令牌 → 401
            r = await client.post(
                f"{base}/greet", json={}, headers={"Authorization": "Bearer wrong"}
            )
            assert r.status_code == 401
            # 健康检查免令牌
            r = await client.get(f"{base}/_health")
            assert r.status_code == 200
            # 正确令牌 → 200
            r = await client.post(
                f"{base}/greet",
                json={"name": "x"},
                headers={"Authorization": "Bearer secret-token"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["api"] == "greet"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await host.stop()


def test_web_service_ignores_configured_host():
    """agent spec 里写 0.0.0.0 也不生效（防绕过引擎鉴权对外暴露）。"""
    from core.agent.web_server import WebServiceHost

    host = WebServiceHost(_FakeAgent(), host="0.0.0.0", port=0, token="t")
    assert host.host == "127.0.0.1"

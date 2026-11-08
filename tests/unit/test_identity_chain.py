"""身份链（P0-1）回归测试 —— 管理员评审意见 1。

覆盖四层故障点：

1. ``current_owner_id()`` 的优先级：ContextVar > ``SOTABAND_USER_ID`` > 机器身份；
2. **环境变量层不缓存**：同一进程内改了 env，下一次调用立刻生效（跨用户串号的根因）；
3. ``ToolExecutor`` 子进程环境：有 owner 注入、无 owner 显式删除（不继承脏值）；
4. 常驻 Worker 协议：RPC 载荷携带 ``owner_id``，Worker 脚本每次调用重设 env。
"""

from __future__ import annotations

import os

import pytest

from core.agent.tool_runtime import _WORKER_SCRIPT, ToolRuntime
from core.executor.tool_executor import ToolExecutor
from core.user_context import (
    current_owner_id,
    get_current_user_id,
    reset_cache,
    use_owner,
)


@pytest.fixture(autouse=True)
def _clean_identity(monkeypatch):
    """每个用例从干净身份开始，避免进程级缓存/环境互相污染。"""
    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)
    reset_cache()
    yield
    reset_cache()


def test_env_is_read_fresh_not_cached(monkeypatch):
    """P0-1a：环境变量层不缓存 —— 改 env 后立即生效。"""
    monkeypatch.setenv("SOTABAND_USER_ID", "user-a")
    assert current_owner_id() == "user-a"

    monkeypatch.setenv("SOTABAND_USER_ID", "user-b")
    assert current_owner_id() == "user-b", "env 变化后必须立即生效（不能缓存第一次的值）"


def test_context_var_beats_env(monkeypatch):
    """P0-1：显式上下文（ContextVar）优先级最高。"""
    monkeypatch.setenv("SOTABAND_USER_ID", "user-env")
    with use_owner("user-ctx"):
        assert current_owner_id() == "user-ctx"
    # 退出后恢复 env 值
    assert current_owner_id() == "user-env"


def test_machine_identity_is_fallback(monkeypatch):
    """没有上下文/环境变量时回退机器身份（未登录兜底）。"""
    machine = current_owner_id()
    assert machine, "机器身份不应为空"
    assert get_current_user_id() == machine, "兼容别名必须与 current_owner_id 同语义"


def test_tool_executor_child_env_injects_owner(monkeypatch):
    """P0-1b：工具子进程环境显式注入发起方 UUID。"""
    monkeypatch.setenv("SOTABAND_USER_ID", "stale-user")
    env = ToolExecutor._child_env("uuid-of-user")
    assert env["SOTABAND_USER_ID"] == "uuid-of-user"


def test_tool_executor_child_env_keeps_parent_owner_when_none(monkeypatch):
    """无显式 owner 时**保留父进程身份**（entrypoint 每任务已设置正确值）。

    历史回归：曾经在这里 pop 掉变量，导致 workflow / 生成的 Agent 等
    未显式传 owner 的路径退化到机器身份，比修复前更差。
    """
    monkeypatch.setenv("SOTABAND_USER_ID", "current-user")
    env = ToolExecutor._child_env(None)
    assert env["SOTABAND_USER_ID"] == "current-user"


def test_tool_executor_child_env_strips_agent_http_token(monkeypatch):
    """Agent Web 服务的引擎令牌不能泄漏给工具子进程。"""
    monkeypatch.setenv("SOTABAND_AGENT_HTTP_TOKEN", "secret")
    env = ToolExecutor._child_env("uuid")
    assert "SOTABAND_AGENT_HTTP_TOKEN" not in env


def test_worker_script_resets_identity_per_call():
    """P0-1c：常驻 Worker 每次 call 前按载荷重设 env 并清缓存。"""
    assert "SOTABAND_USER_ID" in _WORKER_SCRIPT
    assert "owner_id" in _WORKER_SCRIPT
    assert "reset_cache" in _WORKER_SCRIPT


async def test_runtime_call_payload_carries_owner(monkeypatch):
    """P0-1c：ToolRuntime.call 把 owner_id 放进 RPC 载荷。"""
    import json as _json

    runtime = ToolRuntime.__new__(ToolRuntime)  # 不启动真实进程
    captured: list[dict] = []

    class _FakeStdin:
        def write(self, data: bytes):
            captured.append(_json.loads(data.decode("utf-8")))

        async def drain(self):
            return None

    class _FakeProc:
        returncode = None
        stdin = _FakeStdin()

        class _Stdout:
            async def readline(self):
                return _json.dumps({"event": "result", "result": {"ok": True}}).encode()

        stdout = _Stdout()

    runtime._workers = {"t1": {"proc": _FakeProc(), "ready": True}}
    runtime._lock = __import__("asyncio").Lock()

    result = await runtime.call("t1", {"x": 1}, owner_id="uuid-owner")
    assert result == {"ok": True}
    assert captured and captured[0]["type"] == "call"
    assert captured[0]["owner_id"] == "uuid-owner"


async def test_base_agent_call_tool_passes_owner(monkeypatch):
    """P0-1：BaseAgent.call_tool 把 owner_id 透传给底层执行器。"""
    from core.agent.base import BaseAgent

    captured: dict = {}

    class _Agent(BaseAgent):
        async def execute(self, ctx, **kwargs):  # pragma: no cover - 抽象占位
            yield {}

    agent = _Agent.__new__(_Agent)
    agent.spec = type("S", (), {"id": "a", "raw_md": "", "flow": [], "apis": [], "config": {}})()
    agent._tool_runtime = None
    agent.config = {}

    async def _fake_execute(tool_id, params, timeout=None, code=None, owner_id=None):
        captured["owner_id"] = owner_id
        return {"status": "success"}

    monkeypatch.setattr(ToolExecutor, "execute", staticmethod(_fake_execute))
    monkeypatch.setattr(
        "core.resource.param_guard.check_params", lambda tool_id, params: None
    )
    out = await agent.call_tool("t1", {}, owner_id="uuid-owner")
    assert out == {"status": "success"}
    assert captured["owner_id"] == "uuid-owner"


def test_system_api_uses_current_owner(monkeypatch):
    """P0-1d：系统 API（api_data）与 HTTP 路由共用同一身份解析。"""
    import core.api.implementations.api_data as api_data

    source = open("core/api/implementations/api_data.py", encoding="utf-8").read()
    assert "current_owner_id" in source
    assert "get_current_user_id" not in source, "系统 API 不应再直调旧别名"
    assert hasattr(api_data, "ApiDataList")

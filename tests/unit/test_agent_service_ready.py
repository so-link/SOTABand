# -*- coding: utf-8 -*-
"""web-service Agent 就绪探测（service_ready）的回归测试。

背景：Agent 的 ``on_start`` 里常要 import torch / 加载 RKNN 模型，``service_ready``
往往 3~15s 才上报。旧实现只等「2s + 1s」，超时就判空 → ``_services`` 永远为空
→ ``/api/agent/{id}/service`` 返回 ``running: false``，界面一直显示"未运行"，
但进程其实已经起来了。该修复此前在 ``f7ee390`` 上做过，未合入 main，本次重新移植。

这里用假队列 + 假进程，不真的起子进程。
"""

from __future__ import annotations

import asyncio

import pytest

from core.agent.factory import AgentFactory


class _FakeProc:
    """最小子进程替身：只提供 returncode 判定存活。"""

    def __init__(self, returncode: int | None = None):
        self.returncode = returncode


def _wire(factory: AgentFactory, agent_id: str = "svc-agent") -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    factory._outputs[agent_id] = q
    factory._processes[agent_id] = _FakeProc()
    return q


@pytest.mark.asyncio
async def test_slow_ready_is_captured():
    """就绪事件晚于旧超时（>3s 的一半）也必须在限时轮询内被捕获。"""
    factory = AgentFactory()
    q = _wire(factory)

    async def _feed():
        await asyncio.sleep(0.4)
        q.put_nowait({
            "event": "service_ready",
            "data": {"host": "0.0.0.0", "port": 8123, "apis": [{"name": "models"}]},
        })

    feeder = asyncio.create_task(_feed())
    svc = await factory._wait_service_ready("svc-agent", timeout=3.0)
    await feeder

    assert svc is not None
    assert svc["port"] == 8123
    assert svc["apis"] == [{"name": "models"}]
    assert factory.get_service("svc-agent") == svc


@pytest.mark.asyncio
async def test_host_is_forced_to_loopback():
    """子进程自报的 host 不可信：必须收敛到 127.0.0.1，防 /test-api 变成 SSRF。"""
    factory = AgentFactory()
    q = _wire(factory)
    q.put_nowait({
        "event": "service_ready",
        "data": {"host": "evil.example.com", "port": 9000, "apis": []},
    })

    svc = await factory._wait_service_ready("svc-agent", timeout=2.0)
    assert svc is not None
    assert svc["host"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_non_ready_events_are_requeued_in_order():
    """探测期间读到的其它事件必须原序回队，否则 execute_stream 会丢输出。"""
    factory = AgentFactory()
    q = _wire(factory)
    q.put_nowait({"event": "progress", "data": {"step": 1}})
    q.put_nowait({"event": "progress", "data": {"step": 2}})
    q.put_nowait({"event": "service_ready", "data": {"port": 9100}})

    svc = await factory._wait_service_ready("svc-agent", timeout=2.0)
    assert svc is not None

    rest = [q.get_nowait() for _ in range(q.qsize())]
    assert [e["data"]["step"] for e in rest] == [1, 2]


@pytest.mark.asyncio
async def test_timeout_returns_none_without_hanging():
    factory = AgentFactory()
    _wire(factory)

    loop = asyncio.get_running_loop()
    started = loop.time()
    svc = await factory._wait_service_ready("svc-agent", timeout=0.3)
    elapsed = loop.time() - started

    assert svc is None
    assert elapsed < 2.0, "超时后必须立刻返回，不能空等"


@pytest.mark.asyncio
async def test_process_exit_short_circuits():
    """进程已退出时不必等满超时。"""
    factory = AgentFactory()
    _wire(factory)
    factory._processes["svc-agent"].returncode = 1

    loop = asyncio.get_running_loop()
    started = loop.time()
    svc = await factory._wait_service_ready("svc-agent", timeout=5.0)
    elapsed = loop.time() - started

    assert svc is None
    assert elapsed < 2.5


@pytest.mark.asyncio
async def test_error_event_is_recorded():
    """加载失败时子进程先发 error：要留给 start() 回报原因。"""
    factory = AgentFactory()
    q = _wire(factory)
    q.put_nowait({"event": "error", "data": {"message": "spec 解析失败"}})

    svc = await factory._wait_service_ready("svc-agent", timeout=1.2)
    assert svc is None
    assert factory._startup_errors.get("svc-agent") == "spec 解析失败"


@pytest.mark.asyncio
async def test_is_process_alive():
    factory = AgentFactory()
    _wire(factory)
    assert factory.is_process_alive("svc-agent") is True

    factory._processes["svc-agent"].returncode = 0
    assert factory.is_process_alive("svc-agent") is False
    assert factory.is_process_alive("unknown") is False


# ── 角色判定：必须与 entrypoint 同源（读 MD 的 role，而不是猜代码关键字）──

def test_role_from_spec_reads_frontmatter(tmp_path):
    from core.agent.factory import _role_from_spec

    (tmp_path / "spec.md").write_text("---\nid: x\nrole: web-service\n---\n", encoding="utf-8")
    assert _role_from_spec(tmp_path, "x") == "web-service"


def test_role_from_spec_normalizes_underscore(tmp_path):
    from core.agent.factory import _role_from_spec

    (tmp_path / "spec.md").write_text("role: web_service\n", encoding="utf-8")
    assert _role_from_spec(tmp_path, "x") == "web-service"


def test_role_from_spec_finds_sibling_definitions(tmp_path):
    """多用户部署的典型布局：role 在 <root>/agents/definitions/<id>.md，
    实现目录旁边没有 spec.md —— 必须能顺着 impl_dir 找到同根定义。"""
    from core.agent.factory import _role_from_spec

    impl = tmp_path / "agents" / "implementations" / "svc"
    impl.mkdir(parents=True)
    defs = tmp_path / "agents" / "definitions"
    defs.mkdir(parents=True)
    (defs / "svc.md").write_text("---\nrole: web-service\n---\n", encoding="utf-8")

    assert _role_from_spec(impl, "svc") == "web-service"


def test_role_from_code_ignores_comparison(tmp_path):
    """interactive_agent 的真实形态：构造里 role=AgentRole.INTERACTIVE，
    文件里还夹着 `if role == "web-service":` 的比较语句 —— 不能被后者误判。"""
    from core.agent.factory import _role_from_code

    f = tmp_path / "agent.py"
    f.write_text(
        "from core.agent.base import BaseAgent, AgentRole, AgentSpec\n"
        "class A(BaseAgent):\n"
        "    def __init__(self):\n"
        "        self.spec = AgentSpec(id='a', name='a', role=AgentRole.INTERACTIVE)\n"
        "    def run(self, role):\n"
        "        if role == 'web-service':\n"
        "            return 1\n",
        encoding="utf-8",
    )
    assert _role_from_code(f) == "interactive"


def test_is_web_service_false_for_interactive_agent_like_code(tmp_path):
    from core.agent.factory import AgentFactory

    impl = tmp_path / "agents" / "implementations" / "interactive_agent"
    impl.mkdir(parents=True)
    (impl / "agent.py").write_text(
        "class A:\n"
        "    role = AgentRole.INTERACTIVE\n"
        "    def f(self, role):\n"
        "        return role == 'web-service'\n",
        encoding="utf-8",
    )
    assert AgentFactory()._is_web_service("interactive_agent", str(impl)) is False


def test_role_from_spec_returns_none_without_definition(tmp_path):
    from core.agent.factory import _role_from_spec

    assert _role_from_spec(tmp_path, "definitely-not-exists-agent") is None


def test_is_web_service_true_when_role_only_in_md(tmp_path):
    """用户自建 Agent 的典型形态：代码里没有任何 web-service 字面量。"""
    from core.agent.factory import AgentFactory

    (tmp_path / "agent.py").write_text("class A:\n    pass\n", encoding="utf-8")
    (tmp_path / "spec.md").write_text("---\nrole: web-service\n---\n", encoding="utf-8")

    assert AgentFactory()._is_web_service("x", str(tmp_path)) is True


def test_is_web_service_false_for_task_role(tmp_path):
    """MD 明确写 task 时以 MD 为准，不能被代码关键字覆盖。"""
    from core.agent.factory import AgentFactory

    (tmp_path / "agent.py").write_text("# 这里提到 web-service 只是注释\n", encoding="utf-8")
    (tmp_path / "spec.md").write_text("---\nrole: task\n---\n", encoding="utf-8")

    assert AgentFactory()._is_web_service("x", str(tmp_path)) is False


@pytest.mark.asyncio
async def test_cancel_service_probe():
    factory = AgentFactory()

    async def _never() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(_never())
    factory._service_probe_tasks["svc-agent"] = task

    factory._cancel_service_probe("svc-agent")
    assert "svc-agent" not in factory._service_probe_tasks

    with pytest.raises(asyncio.CancelledError):
        await task

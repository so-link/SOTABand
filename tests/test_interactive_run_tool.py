"""InteractiveAgentBase.run_tool 必须经平台控制网关（批次1-8，评审 H-2）。

有总线（子进程模式）时直连 call_tool 会绕过网关的身份注入、风险分级与
chat_ops 审计；这里用假总线锁定路由契约：

- ok 回执 → 解包 result 返回
- pending 回执（写操作等确认）→ 本轮不执行，返回 failed
- error 回执 → 透传 error 文案
- 总线异常 → 兜底 failed，不抛
- 无总线 → 退回直连 call_tool（进程内调试豁免，声明一次）
"""

from __future__ import annotations

import pytest

from core.agent.base import AgentContext, AgentSpec
from core.agent.interactive_base import InteractiveAgentBase

pytestmark = pytest.mark.unit


def _agent(bus=None, direct_result=None, direct_exc=None):
    agent = InteractiveAgentBase(AgentSpec(id="t-agent", name="测试"))
    if bus is not None:
        agent._bus = bus

    async def fake_call_tool(tool_id, params=None):
        if direct_exc:
            raise direct_exc
        return direct_result

    agent.call_tool = fake_call_tool
    return agent


class _Bus:
    def __init__(self, receipt=None, exc=None):
        self.receipt = receipt
        self.exc = exc
        self.calls = []

    async def platform_call(self, tool, args=None, *, session_id="default",
                            user_id="default", timeout=300):
        self.calls.append({"tool": tool, "args": args,
                           "session_id": session_id, "user_id": user_id})
        if self.exc:
            raise self.exc
        return self.receipt


CTX = AgentContext(agent_id="t-agent", session_id="s1", user_id="u7")


@pytest.mark.asyncio
async def test_bus_path_routes_through_gateway():
    bus = _Bus(receipt={"status": "ok", "result": {"status": "success", "data": {"square": 25}}})
    agent = _agent(bus=bus)
    out = await agent.run_tool("calc-square", {"n": 5}, CTX)
    assert out == {"status": "success", "data": {"square": 25}}
    (call,) = bus.calls
    assert call["tool"] == "run_tool"
    assert call["args"] == {"tool_id": "calc-square", "params": {"n": 5},
                            "confirmed": True}
    # 归属必须来自本轮 ctx，而不是帧内自报的缺省值
    assert call["session_id"] == "s1" and call["user_id"] == "u7"


@pytest.mark.asyncio
async def test_pending_receipt_does_not_execute():
    bus = _Bus(receipt={"status": "pending", "op_id": "op-1"})
    agent = _agent(bus=bus)
    out = await agent.run_tool("t", {}, CTX)
    assert out["status"] == "failed" and "确认" in out["message"]


@pytest.mark.asyncio
async def test_error_receipt_passthrough():
    bus = _Bus(receipt={"status": "error", "error": "风险等级不足"})
    agent = _agent(bus=bus)
    out = await agent.run_tool("t", {}, CTX)
    assert out == {"status": "failed", "message": "风险等级不足"}


@pytest.mark.asyncio
async def test_bus_exception_becomes_failed():
    bus = _Bus(exc=RuntimeError("通道断开"))
    agent = _agent(bus=bus)
    out = await agent.run_tool("t", {}, CTX)
    assert out["status"] == "failed" and "通道断开" in out["message"]


@pytest.mark.asyncio
async def test_no_bus_falls_back_to_direct():
    agent = _agent(direct_result={"status": "success"})
    out = await agent.run_tool("t", {}, CTX)
    assert out == {"status": "success"}


@pytest.mark.asyncio
async def test_no_bus_direct_exception_becomes_failed():
    agent = _agent(direct_exc=RuntimeError("boom"))
    out = await agent.run_tool("t", {}, CTX)
    assert out["status"] == "failed" and "boom" in out["message"]

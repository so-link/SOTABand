"""交互 Agent 派发回归：匹配到 Agent 但未运行时，必须自动启动并继续处理原提问。

对应线上问题：新对话输入「广州明天天气怎么样？」→ 匹配到天气 Agent →
自动启动成功 → 原始提问被丢弃，用户必须再问一遍才能得到结果。
根因是 _dispatch_to_agent 收到「未运行」后只发起 start_agent 就 return。

这里用假 bus 模拟「第一次 execute 返回未运行、启动后返回结果」，断言：
  1. 原始提问（request）最终被送达 Agent，而不是只启动就结束
  2. 用户不需要再发一轮「继续」
"""

import asyncio
import importlib.util
import types
from pathlib import Path

import pytest

AGENT_PY = (Path(__file__).resolve().parents[2]
            / "resources/agents/implementations/interactive_agent/agent.py")

USER_REQUEST = "广州明天天气怎么样？"


class FakeBus:
    """模拟主进程 RPC：首次 execute 报未运行，启动后正常返回。"""

    def __init__(self, start_confirm: dict | None = None):
        self.started = False
        self.confirm = start_confirm
        self.executed_payloads: list[dict] = []

    async def call_agent(self, agent_id, action, params, timeout=300):
        if action != "execute":
            return {"error": "unsupported"}
        self.executed_payloads.append(params)
        if not self.started:
            return {"error": "Agent 未运行"}
        return {"ok": True, "result": {"text": "广州明天多云，26~33℃，有阵雨。"}}

    async def platform_call(self, name, args, session_id="", user_id=""):
        if name != "start_agent":
            return {"error": "unsupported"}
        if self.confirm:
            return {"status": "pending", "confirm": self.confirm}
        self.started = True
        return {"status": "ok"}


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("interactive_agent_under_test", AGENT_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_agent(module, bus):
    agent = module.InteractiveAgent.__new__(module.InteractiveAgent)
    agent._bus = bus
    agent._sessions = {}
    agent._pending_agents = {}
    agent._pending_calls = {}
    agent._pending_modes = {}
    agent._pending_confirm = {}

    async def _no_summary(*args, **kwargs):
        return

    agent._summarize = _no_summary
    # 小结依赖 LLM，这里不测，直接让 chat_stream 空产出
    class _LLM:
        async def chat_stream(self, *a, **k):
            return
            yield  # pragma: no cover

    agent.llm = _LLM()
    return agent


def _collect(agent, entry):
    async def run():
        return [ev async for ev in
                agent._dispatch_to_agent(entry, USER_REQUEST, "s-test", "u-test")]

    return asyncio.run(run())


def test_original_request_is_processed_after_auto_start():
    module = _load_agent_module()
    bus = FakeBus()
    agent = _make_agent(module, bus)

    events = _collect(agent, {"id": "weather-agent", "name": "天气Agent", "role": "interactive"})

    # 原始提问必须真正送达 Agent（不止是启动一下就结束）
    assert any(p.get("content") == USER_REQUEST for p in bus.executed_payloads), \
        f"原始提问未被提交给 Agent，实际提交内容: {bus.executed_payloads}"

    # 必须拿到结果卡片，而不是「请回复继续」
    cards = [e for e in events if e.get("event") == "card"]
    assert any(c["data"].get("type") == "result-summary" and "广州市" not in ""
               for c in cards), f"没有结果卡片，事件: {[e.get('event') for e in events]}"
    texts = "".join(str(e.get("data", {})) for e in events)
    assert "继续" not in texts, "启动成功后仍要求用户回复「继续」"


def test_no_result_when_agent_start_requires_confirmation():
    """启动需用户确认时，保留原请求以便确认后重提，且不静默丢弃。"""
    module = _load_agent_module()
    bus = FakeBus(start_confirm={"summary": "启动 Agent 天气Agent"})
    agent = _make_agent(module, bus)

    events = _collect(agent, {"id": "weather-agent", "name": "天气Agent", "role": "interactive"})

    cards = [e for e in events if e.get("event") == "card"]
    confirm = next((c["data"] for c in cards
                    if c["data"].get("type") == "platform-confirm"), None)
    assert confirm is not None
    # 前端在确认成功后要靠卡片里的 request 自动续跑
    assert confirm["data"].get("request") == USER_REQUEST, "确认卡未携带原请求，确认后无法自动续跑"
    pending = agent._pending_agents.get("s-test") or {}
    assert pending.get("request") == USER_REQUEST, "确认启动场景下原始提问未被保留"


def test_resumed_request_is_not_duplicated():
    """前端续跑时会重发原话：合并逻辑不能把它拼成「问题，问题」。"""
    module = _load_agent_module()
    bus = FakeBus()
    bus.started = True
    agent = _make_agent(module, bus)
    agent._pending_agents["s-test"] = {
        "agent": {"id": "weather-agent", "name": "天气Agent", "role": "interactive"},
        "request": USER_REQUEST,
    }

    async def run():
        ctx = types.SimpleNamespace(session_id="s-test", user_id="u-test")
        return [ev async for ev in agent.execute(ctx, content=USER_REQUEST)]

    events = asyncio.run(run())
    assert bus.executed_payloads, f"续跑请求未送达 Agent，事件: {[e.get('event') for e in events]}"
    assert bus.executed_payloads[0].get("content") == USER_REQUEST

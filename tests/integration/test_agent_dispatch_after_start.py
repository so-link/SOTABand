"""交互 Agent 派发回归：匹配到 Agent 但未运行时，启动后必须继续处理原提问。

对应线上问题：新对话输入「广州明天天气怎么样？」→ 匹配到天气 Agent →
启动 Agent → 原始提问被丢弃，用户必须再问一遍才能得到结果。

## 检查项（两条都必须满足）

1. **必须从新会话开始**：每次用例都在全新的 session 里跑（前端「新建对话」
   会生成新的 sessionId），旧会话的消息不参与本轮上下文，本轮消息也不能
   落到旧会话里。`_make_agent` 每例新建实例、`SESSION` 每例新值，即对应
   这一条；浏览器脚本（tests/e2e/test_chat_new_session_agent_start.py）
   还会断言对话区旧消息为 0。
2. **自动确认 / 手工确认分别测试**：
   - 手工确认：Agent 未运行时，后端只下发确认卡并**提示用户启动**，
     不能声称已启动、不能让用户重新描述需求；用户确认后，前端补发原话，
     由 `_pending_agents` 把原请求重投给刚启动的 Agent。
   - 自动确认：网关直接放行，后端自动启动并把「启动」这一步作为
     `start_agent` 步骤下发，用户能在时间线里看到，然后自动继续处理
     原请求，不需要用户再说一句话。
"""

import asyncio
import importlib.util
import types
from pathlib import Path

AGENT_PY = (Path(__file__).resolve().parents[2]
            / "resources/agents/implementations/interactive_agent/agent.py")

USER_REQUEST = "广州明天天气怎么样？"
AGENT_ENTRY = {"id": "weather-agent", "name": "天气Agent", "role": "interactive"}
RESULT_TEXT = "广州明天多云，26~33℃，有阵雨。"


class FakeBus:
    """模拟主进程 RPC。

    start_confirm 非空 = 手工确认策略（网关返回 pending + 确认卡）；
    为空 = 自动确认策略（网关直接执行并返回 ok）。
    """

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
        return {"ok": True, "result": {"text": RESULT_TEXT}}

    async def platform_call(self, name, args, session_id="", user_id=""):
        if name != "start_agent":
            return {"error": "unsupported"}
        if self.confirm:
            # 手工确认：网关只登记 pending，执行发生在用户点「确认执行」之后
            return {"status": "pending", "confirm": dict(self.confirm)}
        self.started = True
        return {"status": "ok"}


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("interactive_agent_under_test", AGENT_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_agent(module, bus):
    """每例都新建实例 + 独立 session：用例必须从「新会话」开始。"""
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


def _collect(agent, session="s-test"):
    async def run():
        return [ev async for ev in
                agent._dispatch_to_agent(AGENT_ENTRY, USER_REQUEST, session, "u-test")]

    return asyncio.run(run())


def _texts(events):
    """把事件里的可见文案拼起来（不含屏幕外元数据）。"""
    out = []
    for e in events:
        d = e.get("data") or {}
        if e.get("event") in ("content", "step"):
            out.append(str(d.get("text", "")))
        elif e.get("event") == "card":
            out.append(str(d.get("summary", "")))
            out.append(str((d.get("data") or {}).get("request", "")))
    return out


# ────────────────────────── 自动确认 ──────────────────────────

def test_auto_confirm_starts_agent_and_continues_original_request():
    module = _load_agent_module()
    bus = FakeBus()
    agent = _make_agent(module, bus)

    events = _collect(agent)

    # 原提问必须真正送达 Agent（不止是启动一下就结束）
    assert any(p.get("content") == USER_REQUEST for p in bus.executed_payloads), \
        f"原始提问未被提交给 Agent，实际提交内容: {bus.executed_payloads}"

    # 结果卡：给用户看到真实结果，而不是「请回复继续」
    result_cards = [e["data"] for e in events
                    if e.get("event") == "card"
                    and e["data"].get("type") == "result-summary"]
    assert result_cards and RESULT_TEXT in str(result_cards[0].get("summary", "")), \
        f"没有拿到 Agent 结果，事件: {[e.get('event') for e in events]}"
    assert "继续" not in "".join(_texts(events)), "自动启动后仍要求用户回复「继续」"

    # 用户能看到「启动」这一步：start_agent 步骤有 running → done 的完整轨迹
    start_steps = [e["data"] for e in events
                   if e.get("event") == "step" and e["data"].get("id") == "start_agent"]
    statuses = [s.get("status") for s in start_steps]
    assert "running" in statuses, f"启动步骤没有展示为进行中: {start_steps}"
    assert statuses[-1] == "done" and "已就绪" in str(start_steps[-1].get("text", "")), \
        f"启动步骤没有收尾为「已就绪」: {start_steps}"
    # 自动确认路径不下发确认卡（否则等于要求用户点按钮）
    assert not any(e.get("event") == "card"
                   and e["data"].get("type") == "platform-confirm" for e in events)


# ────────────────────────── 手工确认 ──────────────────────────

def test_manual_confirm_prompts_user_to_start_agent():
    """手工确认：只提示用户启动，不声称已启动，也不丢原请求。"""
    module = _load_agent_module()
    bus = FakeBus(start_confirm={"tool": "start_agent", "summary": "启动 Agent 天气Agent"})
    agent = _make_agent(module, bus)

    events = _collect(agent)

    # 提示用户启动 + 下发确认卡，并说明确认后会接着处理原提问
    confirm = next((e["data"] for e in events
                    if e.get("event") == "card"
                    and e["data"].get("type") == "platform-confirm"), None)
    assert confirm is not None, "手工确认下没有下发启动确认卡"
    prompt = "".join(str(e.get("data", {}).get("text", "")) for e in events
                     if e.get("event") == "content")
    assert "未运行" in prompt and "确认执行" in prompt, f"没有提示用户启动 Agent: {prompt}"
    assert "不需要重新描述需求" in prompt or "接着处理" in prompt, f"没说明确认后会继续原请求: {prompt}"
    # 未确认前不能声称已启动；Agent 侧只收到一次探测调用（返回未运行），
    # 不会在确认前真正执行任务
    assert "已启动" not in prompt and "已就绪" not in prompt, f"未确认就声称已启动: {prompt}"
    result_cards = [e for e in events if e.get("event") == "card"
                    and e["data"].get("type") == "result-summary"]
    assert not result_cards, "未确认前就产出了执行结果"
    # 原请求随卡片下发（前端确认成功后据此自动续跑）
    assert confirm["data"].get("request") == USER_REQUEST
    assert (agent._pending_agents.get("s-test") or {}).get("request") == USER_REQUEST


def test_manual_confirm_resumes_original_request_after_confirm():
    """手工确认后：前端补发原话 → 后端把待办请求重投给刚启动的 Agent。"""
    module = _load_agent_module()
    bus = FakeBus(start_confirm={"tool": "start_agent", "summary": "启动 Agent 天气Agent"})
    agent = _make_agent(module, bus)
    _collect(agent)                      # 第一轮：下发确认卡
    bus.started = True                   # 用户点了「确认执行」，启动完成

    events = _collect(agent)             # 第二轮：前端补发的原话（新会话内续跑）

    assert bus.executed_payloads, "续跑后原请求未送达 Agent"
    # 首轮那次是「探测运行状态」的调用（返回未运行），续跑又提交一次；
    # 两次内容都必须是原话原文，不能拼接成「问题，问题」。
    assert all(p.get("content") == USER_REQUEST for p in bus.executed_payloads), \
        f"原请求重投不正确（不能拼接成「问题，问题」）: {bus.executed_payloads}"
    assert any(e.get("event") == "card"
               and e["data"].get("type") == "result-summary" for e in events), \
        f"确认后没有拿到 Agent 结果，事件: {[e.get('event') for e in events]}"
    # 「有结果还留着待办」会让用户的下一条消息又被重投一次原提问：
    # 续跑成功后必须清掉待办。
    assert "s-test" not in agent._pending_agents, "续跑已成功但待办请求未清理"


# ────────────────────────── 新会话检查项 ──────────────────────────

def test_case_uses_fresh_session_and_does_not_leak_to_previous():
    """检查项「必须从新会话开始」：待办请求只作用于本轮 session。"""
    module = _load_agent_module()
    bus = FakeBus(start_confirm={"tool": "start_agent", "summary": "启动 Agent 天气Agent"})
    agent = _make_agent(module, bus)
    _collect(agent, session="s-new")

    assert set(agent._pending_agents) == {"s-new"}, \
        f"待办请求落到了其它会话: {list(agent._pending_agents)}"
    # 另一个新会话发问时不会消费掉 s-new 的待办
    bus2 = FakeBus()
    other = _make_agent(module, bus2)
    _collect(other, session="s-other")
    assert set(other._pending_agents) == set()

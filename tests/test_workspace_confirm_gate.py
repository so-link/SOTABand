"""交互 Agent 工作空间工具执行确认门与审计接入的回归测试。

覆盖审计修复：
1. 确认门旁路 → 预匹配直执行路径也必须先落 workspace-exec pending + 确认卡
2. _pending_confirm 无 TTL → 过期作废
3. workspace 执行生命周期审计 pending/confirmed/executed 落 chat_ops
4. 确认轮 history 协议残缺 → 占位 tool 消息配对
5. function name 合法性 → 清洗 + name→tool_id 反查映射
"""

import asyncio
import re
import time
import types

import pytest

from tests.conftest import run_coro

pytestmark = pytest.mark.unit


agent_mod = pytest.importorskip(
    "resources.agents.implementations.interactive_agent.agent")
InteractiveAgent = agent_mod.InteractiveAgent


def _bare_agent(bus=None):
    """绕过 __init__（不拉起 LLM），只装配确认门所需的最小状态。"""
    agent = object.__new__(InteractiveAgent)
    agent._sessions = {}
    agent._pending_calls = {}
    agent._pending_confirm = {}
    agent._pending_modes = {}
    agent._pending_agents = {}
    agent._bus = bus
    return agent


# ── 5. function name 合法性 ──────────────────────────────────

def test_sanitize_function_name():
    f = InteractiveAgent._sanitize_function_name
    assert f("tool.llm_client") == "tool_llm_client"
    assert f("a" * 100) == "a" * 64  # 截断到 64
    assert f("") == "tool"
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", f(x))
               for x in ("x.y", "中文id", "", "a-b_C.9"))


def test_workspace_tool_schemas_name_map_and_uniqueness(monkeypatch):
    """含非法字符的 id 清洗后作 name，真实 id 走 name_map 反查；重名加后缀。"""
    fake_tools = [
        {"id": "tool.a", "name": "A", "status": "active",
         "description": "d", "param_meta": []},
        {"id": "tool_a", "name": "A2", "status": "active",
         "description": "d", "param_meta": []},  # 清洗后与 tool.a 同名
        {"id": "中文工具", "name": "中文", "status": "active",
         "description": "d", "param_meta": [{"name": "q", "type": "string"}]},
    ]

    class FakeReg:
        def _read(self):
            return fake_tools

    import core.resource.registry.tool_registry as tr
    monkeypatch.setattr(tr, "ToolRegistry", FakeReg)

    agent = _bare_agent()
    name_map = {}
    schemas = agent._workspace_tool_schemas(None, name_map)
    names = [s["function"]["name"] for s in schemas]
    assert len(names) == 3
    for n in names:
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", n)
    # 重名加后缀保证唯一
    assert len(set(names)) == 3
    # name → 真实工具信息（id 可反查）
    id_of = {info["id"] for info in name_map.values()}
    assert id_of == {"tool.a", "tool_a", "中文工具"}
    for n, info in name_map.items():
        assert n in names


# ── 6. schema 合法性（供应商按 JSON Schema uniqueItems 校验）──────────────

def _fake_registry(monkeypatch, tools):
    import core.resource.registry.tool_registry as tr

    class _FakeReg:
        def _read(self):
            return tools

    monkeypatch.setattr(tr, "ToolRegistry", _FakeReg)


def test_workspace_tool_schema_required_is_unique(monkeypatch):
    """多模式工具同名参数重复 → required 必须去重，否则整轮 tools 请求 400。"""
    _fake_registry(monkeypatch, [{
        "id": "test-multi-mode", "name": "多模式", "status": "active", "description": "d",
        "param_meta": [
            {"name": "x", "type": "string", "required": True},
            {"name": "x", "type": "string", "required": True},
            {"name": "y", "type": "string", "required": True},
            {"name": "file_path", "type": "string", "required": True},
        ],
    }])

    schemas = _bare_agent()._workspace_tool_schemas(["test-multi-mode"])
    params = schemas[0]["function"]["parameters"]

    assert params["required"] == ["x", "y", "file_path"]
    assert len(set(params["required"])) == len(params["required"])
    assert set(params["required"]) <= set(params["properties"])


def test_workspace_tool_schema_skips_empty_param_name(monkeypatch):
    _fake_registry(monkeypatch, [{
        "id": "t", "name": "t", "status": "active", "description": "d",
        "param_meta": [
            {"name": "  ", "type": "string", "required": True},
            {"name": "a", "type": "string", "required": True},
        ],
    }])

    params = _bare_agent()._workspace_tool_schemas(["t"])[0]["function"]["parameters"]
    assert list(params["properties"]) == ["a"]
    assert params["required"] == ["a"]


def test_workspace_tool_schema_isolates_broken_tool(monkeypatch):
    """一个工具参数脏数据只跳过它自己，其它工具照常可用。"""
    _fake_registry(monkeypatch, [
        {"id": "broken", "name": "b", "status": "active", "description": "d",
         "param_meta": "not-a-list"},                      # 迭代出 str → p.get 抛错
        {"id": "good", "name": "g", "status": "active", "description": "d",
         "param_meta": [{"name": "q", "type": "string", "required": True}]},
    ])

    schemas = _bare_agent()._workspace_tool_schemas(["broken", "good"])
    assert [s["function"]["name"] for s in schemas] == ["good"]


def test_attachment_local_path_resolves_file_id(monkeypatch):
    """只发图片/文件时，把 file_id 解析成本地绝对路径当作参数值。"""
    from core import user_files

    monkeypatch.setattr(user_files, "resolve", lambda fid: f"/tmp/{fid}.jpg")
    agent = _bare_agent()

    assert agent._attachment_local_path([{"fileId": "abc"}]) == "/tmp/abc.jpg"
    assert agent._attachment_local_path([types.SimpleNamespace(fileId="xyz")]) == "/tmp/xyz.jpg"
    assert agent._attachment_local_path([]) == ""
    assert agent._attachment_local_path([{"fileName": "no-id.jpg"}]) == ""


def test_empty_workspace_injects_no_tools(monkeypatch):
    """空工作空间 = 不注入任何工具（新用户首句话不再被全部内置工具拖累）。

    None 仍表示"未提供"→ 全部，保持内部直调与既有单测的语义。
    """
    _fake_registry(monkeypatch, [{
        "id": "t", "name": "t", "status": "active", "description": "d",
        "param_meta": [{"name": "a", "type": "string", "required": True}],
    }])

    agent = _bare_agent()
    assert agent._workspace_tool_schemas([]) == []
    assert len(agent._workspace_tool_schemas(None)) == 1


# ── 2. 确认 TTL ─────────────────────────────────────────────

def test_exec_pending_ttl_expiry(monkeypatch):
    """过期确认作废并落 expired 审计（对齐网关 15 分钟 TTL）。"""
    agent = _bare_agent()
    audited = []

    async def fake_audit(**kw):
        audited.append(kw)
    monkeypatch.setattr(agent, "_audit_workspace_op", fake_audit)
    assert agent_mod._CONFIRM_TTL_SECONDS == 900

    async def run():
        entry = await agent._create_workspace_exec_pending(
            "s1", "0", {"id": "t1", "name": "T"}, {"a": 1}, None)
        # 未过期：不清理
        await agent._purge_expired_confirm("s1", "0")
        assert "s1" in agent._pending_confirm
        # 人为过期
        entry["expires_at"] = time.time() - 1
        await agent._purge_expired_confirm("s1", "0")
        assert "s1" not in agent._pending_confirm

    run_coro(run())
    statuses = [a["status"] for a in audited]
    assert statuses[0] == "pending"
    assert "expired" in statuses


def test_no_legacy_instant_confirm():
    """② 分支执行确认必须只认显式回复，不再模糊匹配。"""
    agent = _bare_agent()
    assert agent._resolve_exec_confirmation("确认") == "confirm"
    assert agent._resolve_exec_confirmation("确认执行") == "confirm"
    assert agent._resolve_exec_confirmation("我选择第1个工具：天气查询") == "confirm"
    assert agent._resolve_exec_confirmation("取消") == "cancel"
    assert agent._resolve_exec_confirmation("cancel") == "cancel"
    # 模糊内容不得触发执行（旧漏洞：任意消息模糊命中候选名后拿旧参数执行）
    assert agent._resolve_exec_confirmation("今天天气真好") is None
    assert agent._resolve_exec_confirmation("帮我查一下天气查询的结果吧") is None
    assert agent._resolve_exec_confirmation("1") is None


# ── 3. 审计接入（audit.record uid 参数 + factory 帧处理） ────

def test_audit_record_accepts_uid(tmp_path, monkeypatch):
    """record 支持 uid：落指定用户的库，不依赖当前请求身份。"""
    from core.chat_control import audit
    from core import user_paths
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", tmp_path)
    audit._pool.reset()
    try:
        audit.record(op_id="op_x", session_id="s", owner="1001", tool="t",
                     args={"a": 1}, risk="write", status="pending", uid="1001")
        conn = audit._pool.get("1001")
        row = conn.execute(
            "SELECT status, owner FROM chat_ops WHERE op_id='op_x'").fetchone()
        assert row is not None and row["status"] == "pending"
        assert (tmp_path / "1001" / "platform.db").exists()
    finally:
        audit._pool.reset()  # 丢弃 tmp 库连接，避免污染后续测试


def test_factory_workspace_audit_frame(monkeypatch):
    """子进程 workspace_audit 帧 → 主进程 audit.record（uid=owner）。"""
    from core.agent.factory import AgentFactory
    from core.chat_control import audit as audit_mod

    calls = []
    monkeypatch.setattr(audit_mod, "record", lambda **kw: calls.append(kw))

    factory = AgentFactory()
    factory._handle_workspace_audit({
        "type": "workspace_audit", "op_id": "wop_1", "session_id": "s1",
        "owner": "42", "tool": "天气查询", "args": {"city": "广州"},
        "risk": "write", "status": "executed", "result": {"status": "success"},
        "error": "", "duration_ms": 12,
    })
    assert len(calls) == 1
    assert calls[0]["uid"] == "42"
    assert calls[0]["owner"] == "42"
    assert calls[0]["status"] == "executed"
    assert calls[0]["duration_ms"] == 12


def test_workspace_audit_frame_type_registered():
    from core.agent.protocol import BUS_FRAME_TYPES, FRAME_WORKSPACE_AUDIT
    assert FRAME_WORKSPACE_AUDIT == "workspace_audit"
    assert FRAME_WORKSPACE_AUDIT in BUS_FRAME_TYPES


def test_audit_helper_uses_bus_when_present():
    """子进程侧：有总线时经 bus.workspace_audit 交主进程，不本地落库。"""
    sent = []

    class FakeBus:
        async def workspace_audit(self, **kw):
            sent.append(kw)
            return True

    agent = _bare_agent(bus=FakeBus())

    async def run():
        await agent._audit_workspace_op(
            op_id="wop_9", session_key="s", owner="7", tool="t",
            args={}, status="confirmed")

    run_coro(run())
    assert sent and sent[0]["op_id"] == "wop_9" and sent[0]["status"] == "confirmed"


# ── 4. 确认轮 history 配对（占位 tool 消息） ──────────────────

def test_confirm_history_pairing_placeholder_logic():
    """占位消息补全逻辑：assistant tool_calls 与其后 tool 消息一一对应。

    验证 tool_call 循环中「工作空间工具确认返回」路径的协议不变量：
    对未处理的 tool_calls（含当前条）逐条 append role=tool 占位。
    """
    history = []
    tool_calls = [
        {"id": "c1", "name": "plat", "arguments": {}},
        {"id": "c2", "name": "ws_fn", "arguments": {}},
        {"id": "c3", "name": "ws_fn2", "arguments": {}},
    ]
    history.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": tc["id"], "type": "function",
                        "function": {"name": tc["name"], "arguments": "{}"}}
                       for tc in tool_calls],
    })
    # 模拟：先处理 c1（平台工具，回填结果），c2 为工作空间工具 → 从 i=1 起补占位
    history.append({"role": "tool", "tool_call_id": "c1", "content": "{}"})
    i = 1
    for j, tc2 in enumerate(tool_calls[i:], start=i):
        history.append({"role": "tool",
                        "tool_call_id": tc2.get("id") or f"call_{j}",
                        "content": "(等待用户确认)"})
    # 协议校验：每个 tool_call id 都有配对的 tool 消息
    call_ids = [tc["id"] for tc in history[0]["tool_calls"]]
    tool_ids = [m["tool_call_id"] for m in history if m["role"] == "tool"]
    assert call_ids == tool_ids
    assert any("(等待用户确认)" == m["content"] for m in history[1:])


# ── 1. 统一确认门（预匹配路径不再直执行） ─────────────────────

def test_direct_match_requires_confirmation(monkeypatch):
    """预匹配唯一候选 + 参数齐全 → 不直接执行，先发确认卡并登记 pending。"""
    agent = _bare_agent()
    audited = []

    async def fake_audit(**kw):
        audited.append(kw)
    monkeypatch.setattr(agent, "_audit_workspace_op", fake_audit)

    async def fake_purge(*a, **k):
        return None
    monkeypatch.setattr(agent, "_purge_expired_confirm", fake_purge)

    async def fake_match_agent(q):
        return None
    monkeypatch.setattr(agent, "_match_agent", fake_match_agent)

    tool = {"id": "t-weather", "name": "天气查询", "status": "active"}

    async def fake_pre_match(q, ws):
        return [tool], None, []
    monkeypatch.setattr(agent, "_pre_match_tool", fake_pre_match)

    monkeypatch.setattr(agent, "_get_tool_modes", lambda tid: [])

    async def fake_check(tid, params, mode=None):
        return []
    monkeypatch.setattr(agent, "_check_missing_params", fake_check)

    async def fake_extract(q, tid, paths, mode=None):
        return {"city": "广州"}
    monkeypatch.setattr(agent, "_extract_params", fake_extract)

    executed = []

    async def fake_exec(tool_id, params, ctx, session_key, confirmed=False):
        executed.append({"id": tool_id, "confirmed": confirmed})
        return {"status": "success", "data": {}, "output_format": "file"}
    monkeypatch.setattr(agent, "_exec_via_gateway", fake_exec)

    ctx = types.SimpleNamespace(session_id="s1", user_id="0")

    # —— 第一轮：命中工具但未确认 → 只发确认卡，不执行 ——
    async def run1():
        events = []
        async for ev in agent._execute_impl(ctx, "查一下广州天气", [], []):
            events.append(ev)
        return events

    events = run_coro(run1())
    assert executed == []
    cards = [e for e in events if e.get("event") == "card"
             and e["data"].get("type") == "tool-confirm"
             and e["data"]["data"].get("kind") == "workspace-exec"]
    assert cards, "预匹配直执行路径必须先过确认门"
    entry = agent._pending_confirm["s1"]
    assert entry["kind"] == "workspace-exec"
    assert entry["op_id"]
    assert entry["arguments"] == {"city": "广州"}
    assert entry["expires_at"] > time.time()

    # —— 第二轮：模糊回复不触发执行 ——
    async def run_vague():
        events = []
        async for ev in agent._execute_impl(ctx, "今天天气不错", [], []):
            events.append(ev)
        return events

    run_coro(run_vague())
    assert executed == []
    assert "s1" in agent._pending_confirm  # 保持等待

    # —— 第三轮：显式「确认」→ 真正执行 ——
    async def run2():
        events = []
        async for ev in agent._execute_impl(ctx, "确认", [], []):
            events.append(ev)
        return events

    run_coro(run2())
    assert executed and executed[-1]["id"] == "t-weather"
    # 确认门放行后经网关执行（confirmed=True 让网关跳过二次确认）
    assert executed[-1]["confirmed"] is True
    assert "s1" not in agent._pending_confirm

    statuses = [a["status"] for a in audited]
    assert statuses[0] == "pending"
    assert "confirmed" in statuses
    assert "executed" in statuses


def test_exec_cancel_flow(monkeypatch):
    """确认门下回复「取消」→ 不执行、pending 清除、审计 cancelled。"""
    agent = _bare_agent()
    audited = []

    async def fake_audit(**kw):
        audited.append(kw)
    monkeypatch.setattr(agent, "_audit_workspace_op", fake_audit)

    async def fake_purge(*a, **k):
        return None
    monkeypatch.setattr(agent, "_purge_expired_confirm", fake_purge)

    executed = []

    async def fake_exec(*a, **k):
        executed.append(a)
        return {"status": "success"}
    monkeypatch.setattr(agent, "_execute_tool", fake_exec)

    ctx = types.SimpleNamespace(session_id="s9", user_id="0")

    async def run():
        entry = await agent._create_workspace_exec_pending(
            "s9", "0", {"id": "t1", "name": "T"}, {}, None)
        events = []
        async for ev in agent._execute_impl(ctx, "取消", [], []):
            events.append(ev)
        return events

    events = run_coro(run())
    assert executed == []
    assert "s9" not in agent._pending_confirm
    assert any(e.get("event") == "content" and "取消" in e["data"]["text"]
               for e in events)
    assert any(a["status"] == "cancelled" for a in audited)

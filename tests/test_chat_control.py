"""对话内控制系统（core/chat_control）单元测试。

覆盖不依赖 LLM 的纯逻辑：确认协议状态机与不变量、注册表严格校验、
路径白名单、网关白名单与风险分级。DB 与确认存储全部隔离到临时位置，
不污染 storage/platform.db。
"""

import time

import pytest

from tests.conftest import run_coro

pytestmark = pytest.mark.unit


from core.chat_control import audit, confirm, gateway
from core.chat_control import registry as reg
from core.chat_control.registry import RISK_READ, RISK_WRITE, StrictArgs
from core.chat_control.registry import platform_tool
from core.chat_control.guards import PathNotAllowed, resolve_allowed_path
from pydantic import Field


# ── 审计表隔离到临时库 ──

@pytest.fixture(autouse=True)
def isolate_audit_db(tmp_path, monkeypatch):
    from core import user_paths

    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", tmp_path / "users")
    monkeypatch.setattr(user_paths, "DEFAULT_UID", "test")
    audit._pool.reset()
    yield
    audit._pool.reset()


# ── 确认协议 ──

def _new_op(tool="demo_tool", args=None, owner="alice", session="s1"):
    return confirm.create(
        tool=tool, args=args or {"x": 1}, risk="write",
        summary="测试操作", args_view=args or {"x": 1},
        owner=owner, session_id=session,
    )


def test_fingerprint_ignores_key_order():
    a = confirm.fingerprint("t", {"a": 1, "b": 2})
    b = confirm.fingerprint("t", {"b": 2, "a": 1})
    assert a == b
    assert confirm.fingerprint("t", {"a": 1, "b": 3}) != a


def test_confirm_owner_check():
    op = _new_op(owner="alice")
    with pytest.raises(confirm.ConfirmError):
        confirm.mark_confirmed(op.op_id, owner="bob", token=op.token)


def test_confirm_token_check():
    op = _new_op()
    with pytest.raises(confirm.ConfirmError):
        confirm.mark_confirmed(op.op_id, owner="alice", token="wrong-token")


def test_confirm_fingerprint_mismatch_rejected():
    """确认后参数被替换（TOCTOU）必须拒绝执行。"""
    op = _new_op()
    with pytest.raises(confirm.ConfirmError):
        confirm.mark_confirmed(op.op_id, owner="alice", token=op.token,
                               fingerprint_="deadbeef")


def test_confirm_expired():
    op = _new_op()
    op.expires_at = time.time() - 1
    with pytest.raises(confirm.ConfirmError):
        confirm.mark_confirmed(op.op_id, owner="alice", token=op.token)


def test_confirm_is_single_use():
    op = _new_op()
    confirm.mark_confirmed(op.op_id, owner="alice", token=op.token)
    with pytest.raises(confirm.ConfirmError):
        confirm.mark_confirmed(op.op_id, owner="alice", token=op.token)


def test_new_pending_supersedes_old():
    """一轮对话最多 1 个 pending：新请求顶替旧请求。"""
    old = _new_op(session="same")
    new = _new_op(session="same")
    assert old.status == "superseded"
    assert new.status == "pending"
    with pytest.raises(confirm.ConfirmError):
        confirm.mark_confirmed(old.op_id, owner="alice", token=old.token)


def test_cancel_blocks_confirm():
    op = _new_op()
    ok, msg = confirm.cancel(op.op_id, "alice")
    assert ok and msg == ""
    with pytest.raises(confirm.ConfirmError):
        confirm.mark_confirmed(op.op_id, owner="alice", token=op.token)


def test_pending_for_returns_public_view():
    op = _new_op(session="vis")
    view = confirm.pending_for("vis")
    assert view and view["op_id"] == op.op_id
    assert "token" in view and "fingerprint" in view and "expires_at" in view
    assert confirm.pending_for("other-session") is None


def test_finish_caches_result_for_idempotency():
    op = _new_op()
    confirm.finish(op.op_id, "executed", result={"dataset_id": "d1"})
    cached = confirm.cached_result(op.op_id)
    assert cached and cached["status"] == "executed"


# ── 注册表：严格校验 ──

class DemoArgs(StrictArgs):
    name: str = Field(description="名称")
    count: int = Field(default=1, ge=1, le=10)


def test_validate_rejects_extra_keys():
    tool = reg.PlatformTool(
        name="x", description="", args_model=DemoArgs, handler=None, risk=RISK_WRITE,
    )
    model, err = reg.validate_args(tool, {"name": "a", "surprise": 1})
    assert model is None and "surprise" in err


def test_validate_rejects_wrong_type():
    tool = reg.PlatformTool(
        name="x", description="", args_model=DemoArgs, handler=None, risk=RISK_WRITE,
    )
    model, err = reg.validate_args(tool, {"name": "a", "count": "many"})
    assert model is None and err


def test_validate_enforces_bounds():
    tool = reg.PlatformTool(
        name="x", description="", args_model=DemoArgs, handler=None, risk=RISK_WRITE,
    )
    assert reg.validate_args(tool, {"name": "a", "count": 99})[0] is None
    model, _ = reg.validate_args(tool, {"name": "a"})
    assert model.count == 1


def test_schemas_shape_is_openai_function_format():
    schemas = reg.schemas()
    assert schemas, "注册表不应为空"
    for s in schemas:
        fn = s["function"]
        assert s["type"] == "function"
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        assert "title" not in fn["parameters"]


def test_unknown_tool_rejected_by_gateway():
    """LLM 幻觉出的工具名必须被白名单挡下（不执行、落审计）。"""
    import asyncio
    r = run_coro(gateway.platform_call(
        "totally_made_up_tool", {}, owner="alice", session_id="u"))
    assert r["status"] == "error" and "未知平台工具" in r["error"]
    assert any(o["status"] == "rejected_unknown_tool" for o in audit.recent(owner="alice"))


def test_run_tool_confirmed_flag_bypasses_second_confirm():
    """工作空间工具经 Agent 统一确认门后带 confirmed=True：网关直接执行。

    模型可见的 schema 里不能有 confirmed（否则等于把二次确认开关
    交给 LLM），只有 Agent 子进程内部回传才会出现该字段。
    """
    calls = []

    async def _fake_run_tool(args, ctx):
        calls.append((args.tool_id, args.params))
        return {"status": "success", "output_format": "text", "message": "ok", "data": {}}

    # monkeypatch 无法替换「模块内直接引用」的名称，这里直接改注册表登记的
    # handler（网关就是通过 tool.handler 调用的），用例结束复原。
    tool = reg.get("run_tool")
    original_handler = tool.handler
    tool.handler = _fake_run_tool
    try:
        # 未带 confirmed → 仍然只发确认卡
        r1 = run_coro(gateway.platform_call(
            "run_tool", {"tool_id": "any-tool", "params": {"a": 1}},
            owner="alice", session_id="u-rt"))
        assert r1["status"] == "pending" and calls == []

        # 带 confirmed=True → 直接执行（handler 收到的是剥离该内部字段后的参数）
        r2 = run_coro(gateway.platform_call(
            "run_tool", {"tool_id": "any-tool", "params": {"a": 1}, "confirmed": True},
            owner="alice", session_id="u-rt"))
        assert r2["status"] == "ok", r2
        assert calls and calls[0][0] == "any-tool"
    finally:
        tool.handler = original_handler

    # LLM 可见 schema 里不能暴露 confirmed
    for s in reg.schemas():
        if s["function"]["name"] == "run_tool":
            assert "confirmed" not in (s["function"]["parameters"].get("properties") or {})


def test_read_tool_never_creates_pending():
    """只读工具直接执行；写工具只生成 pending，绝不本轮执行。"""
    calls = []

    @platform_tool("_t_read_demo", "", DemoArgs, RISK_READ)
    async def _read(a, ctx):
        calls.append(a.name)
        return {"ok": True}

    @platform_tool("_t_write_demo", "", DemoArgs, RISK_WRITE)
    async def _write(a, ctx):
        calls.append(a.name)
        return {"ok": True}

    import asyncio
    r1 = run_coro(gateway.platform_call(
        "_t_read_demo", {"name": "r"}, owner="alice", session_id="u2"))
    assert r1["status"] == "ok" and calls == ["r"]

    r2 = run_coro(gateway.platform_call(
        "_t_write_demo", {"name": "w"}, owner="alice", session_id="u2"))
    assert r2["status"] == "pending"
    assert r2["confirm"]["risk"] == "write"
    assert calls == ["r"], "写工具处理器在执行前绝不能被调用"

    reg._REGISTRY.pop("_t_read_demo", None)
    reg._REGISTRY.pop("_t_write_demo", None)


# ── 路径白名单 ──

def test_path_outside_allowed_roots_rejected():
    with pytest.raises(PathNotAllowed):
        resolve_allowed_path("/etc/passwd")
    with pytest.raises(PathNotAllowed):
        resolve_allowed_path("/root/.ssh/id_rsa")


def test_symlink_escape_rejected(tmp_path):
    """realpath 前缀匹配必须挡住符号链接逃逸。

    从允许根（storage/）内建一个指向 /etc 的符号链接，
    realpath 解析后落到根外，必须拒绝。
    """
    import os
    from core.resource import paths

    link = paths.PROJECT_ROOT / "storage" / "_pytest_escape_link"
    if link.is_symlink() or link.exists():
        os.unlink(link)
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("/etc")
        with pytest.raises(PathNotAllowed):
            resolve_allowed_path(str(link / "passwd"))
    finally:
        if link.is_symlink():
            os.unlink(link)


def test_allowed_root_path_resolves():
    from core.resource import paths
    p = resolve_allowed_path(str(paths.PROJECT_ROOT))
    assert p.exists()


# ── 速率限制 ──

def test_rate_limit_blocks_excess_calls():
    owner = "rate-test"
    gateway._calls.pop(owner, None)
    for _ in range(30):
        assert gateway.rate_check(owner, "list_datasets") == ""
    blocked = gateway.rate_check(owner, "list_datasets")
    assert "过于频繁" in blocked


def test_gen_tools_get_stricter_limit():
    owner = "gen-rate-test"
    gateway._calls.pop(owner, None)
    gateway._gens.pop(owner, None)
    for _ in range(5):
        assert gateway.rate_check(owner, "create_tool") == ""
    assert "生成类" in gateway.rate_check(owner, "create_tool")


# ── 写路径审计强制（评审 H-4：审计挂了写操作照常执行且无痕）──

def test_pending_card_requires_audit_receipt(monkeypatch):
    """审计不可用时写请求不受理：不发确认卡、不留 pending。"""
    async def _never(args, ctx):
        raise AssertionError("handler 不应被执行")

    tool = reg.get("run_tool")
    original_handler = tool.handler
    tool.handler = _never
    try:
        def boom(uid=None):
            raise RuntimeError("db locked")
        monkeypatch.setattr(audit, "_db", boom)
        r = run_coro(gateway.platform_call(
            "run_tool", {"tool_id": "any-tool", "params": {"a": 1}},
            owner="alice", session_id="s-strict-card"))
        assert r["status"] == "error" and "审计" in r["error"]
        assert confirm.pending_for("s-strict-card") is None
    finally:
        tool.handler = original_handler


def test_confirm_execute_requires_audit_receipt(monkeypatch):
    """确认执行前拿不到审计回执 → 不执行；操作收尾 failed，重试不可绕过。"""
    executed = []

    async def _fake_run_tool(args, ctx):
        executed.append(args.tool_id)
        return {"status": "success", "message": "ok", "data": {}}

    tool = reg.get("run_tool")
    original_handler = tool.handler
    tool.handler = _fake_run_tool
    try:
        r1 = run_coro(gateway.platform_call(
            "run_tool", {"tool_id": "any-tool", "params": {"a": 1}},
            owner="alice", session_id="s-strict-exec"))
        assert r1["status"] == "pending"
        card = r1["confirm"]

        def boom(uid=None):
            raise RuntimeError("disk full")
        monkeypatch.setattr(audit, "_db", boom)
        r2 = run_coro(gateway.confirm_op(
            card["op_id"], owner="alice", token=card["token"],
            fingerprint=card["fingerprint"]))
        assert r2["status"] == "error" and "未执行" in r2["error"]
        assert executed == []

        # 重试：幂等缓存返回首次失败，绝不绕过审计再次执行
        r3 = run_coro(gateway.confirm_op(
            card["op_id"], owner="alice", token=card["token"],
            fingerprint=card["fingerprint"]))
        assert r3["status"] == "failed"
        assert executed == []
    finally:
        tool.handler = original_handler


def test_read_path_audit_stays_lenient(monkeypatch):
    """只读路径：审计失败仍正常返回结果（对话可用性优先）。"""
    import core.chat_control.registry as _reg

    class _Empty(StrictArgs):
        pass

    @platform_tool(name="strict_demo_read", description="d", args_model=_Empty, risk=RISK_READ)
    async def _demo(args, ctx):
        return {"items": []}

    try:
        def boom(uid=None):
            raise RuntimeError("db locked")
        monkeypatch.setattr(audit, "_db", boom)
        r = run_coro(gateway.platform_call(
            "strict_demo_read", {}, owner="alice", session_id="s-strict-read"))
        assert r["status"] == "ok"
    finally:
        _reg._REGISTRY.pop("strict_demo_read", None)

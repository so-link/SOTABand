"""Agent 通信总线越权/串号修复回归

覆盖两个审计发现：
1. /api/agent/bus/call、/bus/publish 此前不走 _visible_agent 闸门，
   任何登录用户可按 id RPC 他人正在运行的私有 Agent。
2. 被调侧 RPC 轮曾把帧内自报的 from（agent 名）当 user_id 塞进
   AgentContext，且不覆写 SOTABAND_USER_ID——共享进程里 RPC 触发的
   工具沿用上一个 execute 轮残留身份落盘（串号）。
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import user_paths
from core import auth as auth_mod


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """用户目录根、用户表、令牌密钥指向临时位置；总线路由表用后清理。"""
    from core.agent.factory import agent_factory
    from core.chat_control import audit
    from core.cron import store as cron_store
    from core.observability import chat_store, task_history
    from core import user_prefs, user_files

    root = tmp_path / "SB-USER"
    (root / "0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(user_paths, "USER_HOME_ROOT", root)
    monkeypatch.setattr(auth_mod, "USERS_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_mod, "USERS_FILE", tmp_path / "auth" / "users.json")
    monkeypatch.setattr(auth_mod, "SECRET_FILE", tmp_path / "auth" / "secret.key")
    monkeypatch.delenv("SOTABAND_AUTH_SECRET", raising=False)
    monkeypatch.delenv("SOTABAND_USER_ID", raising=False)
    monkeypatch.setenv("SOTABAND_ROOT_USERNAME", "root")
    monkeypatch.setenv("SOTABAND_ROOT_PASSWORD", "sup3r-secret-pw")

    pools = [
        user_files._pool, chat_store._pool, task_history._pool,
        audit._pool, cron_store._pool, user_prefs._pool,
    ]
    for p in pools:
        p.reset()
    saved_subs = dict(agent_factory._subscribers)
    agent_factory._subscribers.clear()
    yield
    agent_factory._subscribers.clear()
    agent_factory._subscribers.update(saved_subs)
    for p in pools:
        p.reset()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def tokens():
    from core.auth import authenticate, create_user, issue_token

    root = authenticate("root", "sup3r-secret-pw")
    create_user("alice", "alice-password")
    alice = authenticate("alice", "alice-password")
    return {"root": issue_token(root), "alice": issue_token(alice)}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_bob():
    from core.auth import authenticate, create_user, issue_token

    create_user("bob", "bob-password")
    return issue_token(authenticate("bob", "bob-password"))


def _register_agent(client, token, agent_id):
    return client.post(
        "/api/agent/register", headers=_bearer(token),
        json={
            "specMd": f"---\nid: {agent_id}\nname: {agent_id}\nrole: task\n---\n\n# {agent_id}\n",
            "agentId": agent_id, "agentName": agent_id, "role": "task",
        },
    )


# ── /bus/call 可见性闸门 ──

def test_bus_call_rejects_invisible_agent(client, tokens, monkeypatch):
    """他人私有 Agent：bus/call 一律 404（与详情/启停/删除同一闸门）。"""
    import app.api.routes.agent_routes as ar

    bob = _make_bob()
    assert _register_agent(client, tokens["alice"], "agent-alice").status_code == 200

    called: dict = {}

    async def fake_call_agent(from_agent, to, method, params=None, timeout=60,
                              caller_uid=None):
        called["to"] = to
        return {"ok": True, "result": "should-not-reach"}

    monkeypatch.setattr(ar.agent_factory, "call_agent", fake_call_agent)

    r = client.post("/api/agent/bus/call", headers=_bearer(bob),
                    json={"to": "agent-alice", "method": "execute", "from": "main"})
    assert r.status_code == 404, r.text
    assert "to" not in called  # 闸门在路由之前，RPC 未发出


def test_bus_call_injects_server_side_caller_uid(client, tokens, monkeypatch):
    """执行身份由服务端按登录用户注入；请求体自报的 from 不参与身份。"""
    import app.api.routes.agent_routes as ar

    assert _register_agent(client, tokens["alice"], "agent-alice-uid").status_code == 200

    captured: dict = {}

    async def fake_call_agent(from_agent, to, method, params=None, timeout=60,
                              caller_uid=None):
        captured["from"] = from_agent
        captured["caller_uid"] = caller_uid
        return {"ok": True, "result": {}}

    monkeypatch.setattr(ar.agent_factory, "call_agent", fake_call_agent)

    # 客户端谎称 from 是超管身份"0"——uid 仍必须是登录用户 alice
    r = client.post("/api/agent/bus/call", headers=_bearer(tokens["alice"]),
                    json={"to": "agent-alice-uid", "method": "execute", "from": "0"})
    assert r.status_code == 200, r.text
    assert captured["caller_uid"] == "1000"


# ── /bus/publish 订阅方按可见性过滤 ──

def test_bus_publish_filters_invisible_subscribers(client, tokens, monkeypatch):
    """发布只投递给调用者可见的订阅方，他人私有 Agent 静默过滤。"""
    from core.agent.factory import agent_factory

    bob = _make_bob()
    assert _register_agent(client, tokens["alice"], "agent-pub-alice").status_code == 200
    assert _register_agent(client, bob, "agent-pub-bob").status_code == 200

    agent_factory._subscribers["news"] = {"agent-pub-alice", "agent-pub-bob"}
    sent: list = []

    async def fake_deliver(agent_id, frame):
        sent.append(agent_id)
        return True

    monkeypatch.setattr(agent_factory, "_deliver", fake_deliver)

    r = client.post("/api/agent/bus/publish", headers=_bearer(tokens["alice"]),
                    json={"channel": "news", "message": {"x": 1}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delivered"] == ["agent-pub-alice"]
    # bob 的私有 Agent 既不在 delivered 也不在 failed——不泄露其存在
    assert "agent-pub-bob" not in body["delivered"] + body["failed"]

    sent.clear()
    r = client.post("/api/agent/bus/publish", headers=_bearer(bob),
                    json={"channel": "news", "message": {"x": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] == ["agent-pub-bob"]


# ── 工厂层：caller_uid / user_id 透传 ──

class _StubStdin:
    def __init__(self, sink):
        self.sink = sink

    def write(self, data):
        self.sink.append(json.loads(data.decode("utf-8")))

    async def drain(self):
        pass


class _FakeProc:
    returncode = None

    def __init__(self):
        self.frames: list = []
        self.stdin = _StubStdin(self.frames)


@pytest.mark.asyncio
async def test_factory_call_agent_embeds_trusted_uid():
    """主进程发起的 RPC 把 caller_uid 注入转发帧的 user_id 字段。"""
    from core.agent.factory import AgentFactory

    f = AgentFactory()
    proc = _FakeProc()
    f._processes["target"] = proc

    async def responder():
        while not proc.frames:
            await asyncio.sleep(0.01)
        await f._route_frame("target", {
            "v": 1, "dir": "o", "type": "agent_reply",
            "req": proc.frames[0]["req"], "ok": True, "result": {}})

    result, _ = await asyncio.gather(
        f.call_agent("main", "target", "m", {}, caller_uid="1000"), responder())
    assert result["ok"] is True
    assert proc.frames[0]["type"] == "agent_call"
    assert proc.frames[0]["user_id"] == "1000"


@pytest.mark.asyncio
async def test_proxy_call_forwards_user_id_and_tolerates_legacy_frames():
    """Agent→Agent 转发保留 user_id；旧帧缺字段不崩（回退 None）。"""
    from core.agent.factory import AgentFactory

    f = AgentFactory()
    proc = _FakeProc()
    f._processes["b"] = proc

    async def drive(frame_in, expect_uid):
        task = asyncio.ensure_future(f._proxy_call("a", frame_in))
        while not proc.frames:
            await asyncio.sleep(0.01)
        fwd = proc.frames.pop(0)
        assert fwd["type"] == "agent_call"
        assert fwd.get("user_id") == expect_uid
        await f._route_frame("b", {
            "v": 1, "dir": "o", "type": "agent_reply",
            "req": fwd["req"], "ok": True, "result": {}})
        await asyncio.wait_for(task, timeout=5)

    # 新帧：子进程侧带受信 user_id
    await drive({"type": "agent_call", "req": "a#1", "to": "b",
                 "method": "m", "params": {}, "user_id": "1001"}, "1001")
    # 旧帧：无 user_id 字段，转发不崩、被调侧收到 None 自行回退
    await drive({"type": "agent_call", "req": "a#2", "to": "b",
                 "method": "m", "params": {}}, None)


# ── 子进程侧：帧 user_id → RPC 处理身份 ──

@pytest.mark.asyncio
async def test_bus_deliver_passes_trusted_uid_to_handler():
    """deliver 把帧内 user_id 作为第 4 参传给 RPC 处理回调（旧帧为 None）。"""
    from core.agent.bus import AgentBusClient

    class _Writer:
        def write(self, data):
            pass

        async def drain(self):
            pass

    c = AgentBusClient("x")
    c.bind(_Writer())
    got: list = []

    async def handler(frm, method, params, uid):
        got.append(uid)
        return {"ok": True, "result": {}}

    c.add_handler("__call__", handler)
    await c.deliver({"v": 1, "dir": "i", "type": "agent_call", "req": "1",
                     "from": "f", "method": "m", "params": {}, "user_id": "42"})
    await c.deliver({"v": 1, "dir": "i", "type": "agent_call", "req": "2",
                     "from": "f", "method": "m", "params": {}})
    assert got == ["42", None]


@pytest.mark.asyncio
async def test_subprocess_call_agent_carries_env_uid(monkeypatch):
    """子进程发起 RPC 时携带进程环境 uid（主进程注入），不是自报值。"""
    from core.agent.bus import AgentBusClient

    monkeypatch.setenv("SOTABAND_USER_ID", "1001")

    written: list = []

    class _Writer:
        def write(self, data):
            written.append(json.loads(data.decode("utf-8")))

        async def drain(self):
            pass

    c = AgentBusClient("caller")
    c.bind(_Writer())
    task = asyncio.ensure_future(c.call_agent("t", "m", {}, timeout=5))
    while not written:
        await asyncio.sleep(0.01)
    assert written[0]["user_id"] == "1001"
    c.resolve({"req": written[0]["req"], "ok": True, "result": {}})
    assert (await asyncio.wait_for(task, timeout=5))["ok"] is True

    monkeypatch.delenv("SOTABAND_USER_ID")
    written.clear()
    task = asyncio.ensure_future(c.call_agent("t", "m", {}, timeout=5))
    while not written:
        await asyncio.sleep(0.01)
    assert written[0]["user_id"] is None
    c.resolve({"req": written[0]["req"], "ok": True, "result": {}})
    await asyncio.wait_for(task, timeout=5)


# ── entrypoint 集成：RPC 轮身份覆写，杜绝残留 ──

_IDENT_AGENT = '''
import os
from core.agent.base import BaseAgent


class IdentAgent(BaseAgent):
    async def execute(self, ctx, **kwargs):
        yield {"event": "content", "data": {"text": "%s|%s" % (
            os.environ.get("SOTABAND_USER_ID"), ctx.user_id)}}
        yield {"event": "done", "data": {}}
'''


async def _read_until(stream, pred, timeout=30):
    while True:
        line = await asyncio.wait_for(stream.readline(), timeout=timeout)
        assert line, "子进程提前退出"
        obj = json.loads(line.decode("utf-8").strip())
        if pred(obj):
            return obj


@pytest.mark.asyncio
async def test_entrypoint_rpc_overrides_residual_identity(tmp_path):
    """execute 轮(1000) 之后到达的 RPC 必须切到调用者 uid，绝不用残留 1000，
    更不把帧内 from（名字不是 uid）当身份；旧帧缺 user_id 回退默认 "0"。"""
    impl = tmp_path / "ident-agent"
    impl.mkdir()
    (impl / "agent.py").write_text(_IDENT_AGENT, encoding="utf-8")

    import os
    env = {k: v for k, v in os.environ.items() if k != "SOTABAND_USER_ID"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "core.agent.entrypoint",
        "--agent-id", "ident-agent", "--impl-path", str(impl),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_ROOT), env=env,
    )
    try:
        # 第一轮 execute：alice（1000）——把 1000 留在进程环境里
        proc.stdin.write((json.dumps({
            "action": "execute", "context": {"user_id": "1000"},
            "params": {}}) + "\n").encode())
        await proc.stdin.drain()
        first = await _read_until(
            proc.stdout, lambda o: o.get("event") in ("content", "done"))
        assert first["event"] == "content"
        assert first["data"]["text"] == "1000|1000"
        await _read_until(proc.stdout, lambda o: o.get("event") == "done")

        # 新帧 RPC：调用者 uid=2000，from 是名字（不是 uid，绝不可信）
        proc.stdin.write((json.dumps({
            "v": 1, "dir": "i", "type": "agent_call", "req": "r1",
            "from": "alice-agent", "method": "execute", "params": {},
            "user_id": "2000"}) + "\n").encode())
        reply = await _read_until(proc.stdout, lambda o: o.get("type") == "agent_reply")
        assert reply["ok"] is True
        assert reply["result"]["text"] == "2000|2000", \
            f"RPC 轮身份错误（疑似沿用残留）: {reply['result']}"

        # 旧帧（缺 user_id）：不崩，回退默认 "0"，且不取 from
        proc.stdin.write((json.dumps({
            "v": 1, "dir": "i", "type": "agent_call", "req": "r2",
            "from": "alice-agent", "method": "execute", "params": {}}) + "\n").encode())
        reply2 = await _read_until(proc.stdout, lambda o: o.get("type") == "agent_reply")
        assert reply2["ok"] is True
        assert reply2["result"]["text"] == "0|0", reply2["result"]
    finally:
        proc.kill()
        await proc.wait()

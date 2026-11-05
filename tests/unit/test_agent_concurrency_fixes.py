"""并发安全与发起者身份回归测试（终审 Critical/High 修复）。

覆盖：

1. ``AgentFactory.execute_stream``：同一 agent 的子进程 stdin/stdout 是单通道
   协议，两个并发执行必须串行；被提前放弃的响应不得流到下一个请求
   （实测「B 的回复被 C 收到」）；
2. ``ToolRuntime.call``：同一 tool 的两个并发调用必须串行（Worker 是单通道
   RPC），且协议错误后旧 Worker 必须被停掉（否则残留结果会漏给下一次调用，
   有副作用的工具还会被回退到 ToolExecutor 再执行一遍）；
3. ``WebServiceHost`` 的发起者身份签名 ``sign_owner`` / ``verify_owner``：
   正确签名通过，篡改 owner / 错误令牌 / 空值一律拒绝；
4. chat 停止标志按 ``(owner, session_id)`` 复合键隔离：两个 owner 共用同一
   session 时，stop 只影响本人；他人任务不可停（路由回 404，不泄露存在性）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent.factory import AgentFactory
from core.agent.tool_runtime import ToolRuntime

# ══════════════════════════════════════════════════════════════════
# 0) AgentFactory：单通道串行 + 被放弃的响应不得串给下一个请求
# ══════════════════════════════════════════════════════════════════


class _FakeChildStdin:
    def __init__(self, proc: _FakeChildProcess):
        self._proc = proc

    def write(self, data: bytes) -> None:
        task = json.loads(data.decode("utf-8"))
        if task.get("action") == "stop":
            self._proc.returncode = 0
            return
        # 真实 entrypoint 是「读一条 → 执行完 → 再读下一条」：任务排队，
        # 不会并发执行。这里如实模拟，才能暴露并发抢读。
        self._proc.inputs.put_nowait(task)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        if self._proc.returncode is None:
            self._proc.returncode = 0
        self._proc.inputs.put_nowait(None)


class _FakeChildStdout:
    def __init__(self, proc: _FakeChildProcess):
        self._proc = proc

    async def readline(self) -> bytes:
        event = await self._proc.output.get()
        return (json.dumps(event) + "\n").encode("utf-8")


class _FakeChildProcess:
    """伪造 Agent 子进程：串行处理 stdin 任务，逐条把事件写到 stdout。"""

    def __init__(self):
        self.returncode = None
        self.inputs: asyncio.Queue = asyncio.Queue()
        self.output: asyncio.Queue = asyncio.Queue()
        self.stdin = _FakeChildStdin(self)
        self.stdout = _FakeChildStdout(self)
        self._consumer = None

    def _ensure_consumer(self) -> None:
        if self._consumer is None:
            self._consumer = asyncio.get_running_loop().create_task(self._consume())

    async def _consume(self) -> None:
        while True:
            task = await self.inputs.get()
            if task is None:
                return
            tag = task.get("params", {}).get("content", "")
            for index in range(2):
                await asyncio.sleep(0.01)
                self.output.put_nowait(
                    {"event": "content", "data": {"text": f"{tag}-{index}"}}
                )
            await asyncio.sleep(0.01)
            self.output.put_nowait({"event": "done", "data": {"messageId": tag}})

    async def aclose(self) -> None:
        if self._consumer is not None:
            self._consumer.cancel()
            try:
                await self._consumer
            except (asyncio.CancelledError, Exception):
                pass
            self._consumer = None

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def _texts(events: list[dict]) -> list[str]:
    out = []
    for event in events:
        if event.get("event") == "content":
            out.append(str(event.get("data", {}).get("text")))
        elif event.get("event") == "done":
            out.append("done:" + str(event.get("data", {}).get("messageId")))
        else:
            out.append("error:" + str(event.get("data", {}).get("message")))
    return out


async def _collect_stream(factory: AgentFactory, content: str) -> list[dict]:
    events = []
    async for event in factory.execute_stream(
        "agent-x", params={"content": content}, timeout=5
    ):
        events.append(event)
    return events


async def test_factory_execute_stream_serializes_concurrent_requests():
    """两个并发 execute_stream 必须串行：各自只收到自己的响应。"""
    factory = AgentFactory()
    proc = _FakeChildProcess()
    proc._ensure_consumer()
    factory._processes["agent-x"] = proc
    try:
        events_a, events_b = await asyncio.gather(
            _collect_stream(factory, "A"),
            _collect_stream(factory, "B"),
        )
        assert _texts(events_a) == ["A-0", "A-1", "done:A"]
        assert _texts(events_b) == ["B-0", "B-1", "done:B"], "B 读到了 A 的流（跨请求串号）"
        assert "agent-x" not in factory._desynced
    finally:
        await proc.aclose()


async def test_factory_execute_stream_drops_abandoned_output():
    """被提前放弃（用户点停止）的响应不得被下一个请求读走。"""
    factory = AgentFactory()
    proc = _FakeChildProcess()
    proc._ensure_consumer()
    factory._processes["agent-x"] = proc
    try:
        stream = factory.execute_stream("agent-x", params={"content": "A"}, timeout=5)
        first = await stream.__anext__()
        assert _texts([first]) == ["A-0"]
        await stream.aclose()  # 模拟「停止」/客户端断连

        events_b = await _collect_stream(factory, "B")
        assert _texts(events_b) == ["B-0", "B-1", "done:B"], "A 的残留事件被 B 收到了"
        assert "agent-x" not in factory._desynced
    finally:
        await proc.aclose()


# ══════════════════════════════════════════════════════════════════
# 1) 常驻 Worker：并发 call 串行 + 协议错误停旧 Worker
# ══════════════════════════════════════════════════════════════════


class _FakeStdin:
    def __init__(self, proc: _FakeWorkerProc):
        self._proc = proc

    def write(self, data: bytes) -> None:
        task = json.loads(data.decode("utf-8"))
        if task.get("type") == "shutdown":
            self._proc.shutdown_requested = True
            self._proc.returncode = 0
            return
        self._proc.calls.append(task)
        self._proc.outstanding += 1
        self._proc.max_outstanding = max(self._proc.max_outstanding, self._proc.outstanding)
        asyncio.get_running_loop().call_later(0.02, self._proc._complete, task)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        if self._proc.returncode is None:
            self._proc.returncode = 0


class _FakeStdout:
    def __init__(self, proc: _FakeWorkerProc):
        self._proc = proc

    async def readline(self) -> bytes:
        return json.dumps(await self._proc.results.get()).encode("utf-8")


class _FakeWorkerProc:
    """伪造常驻 Worker：统计「同时在途的 RPC 数」，结果按 FIFO 回包。"""

    def __init__(self):
        self.returncode = None
        self.calls: list[dict] = []
        self.outstanding = 0
        self.max_outstanding = 0
        self.shutdown_requested = False
        self.results: asyncio.Queue = asyncio.Queue()
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)

    def _complete(self, task: dict) -> None:
        self.outstanding -= 1
        self.results.put_nowait(
            {
                "event": "result",
                "call_id": task.get("call_id", ""),
                "result": {"status": "success", "echo": task["params"].get("x")},
            }
        )

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


async def test_tool_runtime_serializes_concurrent_calls():
    """同一 tool 的两次 call 必须串行，各自拿到自己的结果、不触发回退。"""
    runtime = ToolRuntime(Path("."))
    proc = _FakeWorkerProc()
    runtime._workers = {"demo-tool": {"proc": proc, "ready": True}}

    results = await asyncio.gather(
        runtime.call("demo-tool", {"x": 1}, timeout=5),
        runtime.call("demo-tool", {"x": 2}, timeout=5),
    )

    assert proc.max_outstanding == 1, "单通道 RPC 不允许两个调用同时在途（否则会互相抢读）"
    assert [call["params"]["x"] for call in proc.calls] == [1, 2]
    assert results == [
        {"status": "success", "echo": 1},
        {"status": "success", "echo": 2},
    ]
    assert all("_runtime_error" not in (result or {}) for result in results)
    # 正常调用不应误杀 Worker
    assert runtime._workers["demo-tool"]["ready"] is True


class _UnexpectedEventStdout:
    async def readline(self) -> bytes:
        return json.dumps({"event": "unexpected"}).encode("utf-8")


async def test_tool_runtime_stops_worker_after_protocol_error():
    """协议错误后旧 Worker 必须停掉，且下一次调用不会在旧进程上二次执行。"""
    runtime = ToolRuntime(Path("."))
    proc = _FakeWorkerProc()
    proc.stdout = _UnexpectedEventStdout()
    runtime._workers = {"demo-tool": {"proc": proc, "ready": True}}

    result = await runtime.call("demo-tool", {"x": 1}, timeout=5)

    assert result["_runtime_error"] is True
    assert "demo-tool" not in runtime._workers, "协议错误后旧 Worker 未 stop，残留结果会串给下一次调用"
    assert proc.returncode is not None
    # 旧进程已停：再次调用只会回退（None），不会在旧 Worker 上再执行一次
    assert await runtime.call("demo-tool", {"x": 2}, timeout=5) is None
    assert len(proc.calls) == 1


# ══════════════════════════════════════════════════════════════════
# 2) 发起者身份签名（Web 服务式 Agent）
# ══════════════════════════════════════════════════════════════════


def test_sign_owner_roundtrip():
    from core.agent.web_server import (
        OWNER_HEADER,
        OWNER_SIG_HEADER,
        owner_headers,
        sign_owner,
        verify_owner,
    )

    token = "engine-token"
    owner = "2f1c9d3e-4b5a-4c6d-8e7f-001122334455"
    signature = sign_owner(token, owner)

    assert len(signature) == 64
    assert verify_owner(token, owner, signature) is True
    assert owner_headers(token, owner) == {
        OWNER_HEADER: owner,
        OWNER_SIG_HEADER: signature,
    }
    # 空 owner / 空 token → 不构造头
    assert owner_headers(token, None) == {}
    assert owner_headers("", owner) == {}


def test_sign_owner_rejects_tampering():
    from core.agent.web_server import sign_owner, verify_owner

    token = "engine-token"
    signature = sign_owner(token, "user-a")

    # owner 被替换
    assert verify_owner(token, "user-b", signature) is False
    # 令牌不对
    assert verify_owner("other-token", "user-a", signature) is False
    # 签名被改动
    tampered = signature[:-1] + ("0" if signature[-1] != "0" else "1")
    assert verify_owner(token, "user-a", tampered) is False
    # 缺 token / owner / 签名
    assert verify_owner("", "user-a", signature) is False
    assert verify_owner(token, "", signature) is False
    assert verify_owner(token, "user-a", "") is False
    # 非法 owner 形态（控制字符）即使签名正确也拒绝（防止污染 env / 日志）
    weird = "user\nA"
    assert verify_owner(token, weird, sign_owner(token, weird)) is False


class _OwnerEchoSpec:
    apis = [{"name": "who", "method": "POST", "path": "/who"}]


class _OwnerEchoAgent:
    agent_id = "owner-echo-agent"
    spec = _OwnerEchoSpec()

    async def execute(self, ctx, **kwargs):
        from core.user_context import current_owner_id

        yield {
            "event": "result",
            "data": {
                "response": {
                    "ctx_user": ctx.user_id,
                    "owner": current_owner_id(),
                }
            },
        }


@pytest.mark.asyncio
async def test_web_service_uses_signed_owner_identity():
    """带签名的身份头必须以发起者身份执行；无头/坏签名退回机器身份。"""
    httpx = pytest.importorskip("httpx")

    from core.agent.web_server import (
        OWNER_HEADER,
        OWNER_SIG_HEADER,
        WebServiceHost,
        owner_headers,
    )

    host = WebServiceHost(_OwnerEchoAgent(), port=0, token="engine-token")
    port = await host.start()
    server_task = asyncio.ensure_future(host.serve_forever())
    try:
        base = f"http://127.0.0.1:{port}"
        auth = {"Authorization": "Bearer engine-token"}
        async with httpx.AsyncClient(timeout=5) as client:
            # 合法签名 → ctx 与上下文身份都是发起者
            signed = await client.post(
                f"{base}/who", json={}, headers={**auth, **owner_headers("engine-token", "user-uuid")}
            )
            assert signed.status_code == 200, signed.text
            assert signed.json()["ctx_user"] == "user-uuid"
            assert signed.json()["owner"] == "user-uuid"

            # 无身份头 → 机器身份（ctx 仍是哨兵 "http"）
            anonymous = await client.post(f"{base}/who", json={}, headers=auth)
            assert anonymous.status_code == 200
            assert anonymous.json()["ctx_user"] == "http"
            assert anonymous.json()["owner"] != "user-uuid"

            # 坏签名 → 不采信，退回机器身份
            tampered = await client.post(
                f"{base}/who",
                json={},
                headers={
                    **auth,
                    OWNER_HEADER: "user-uuid",
                    OWNER_SIG_HEADER: "0" * 64,
                },
            )
            assert tampered.status_code == 200
            assert tampered.json()["ctx_user"] == "http"
            assert tampered.json()["owner"] != "user-uuid"
    finally:
        server_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await server_task
        await host.stop()


# ══════════════════════════════════════════════════════════════════
# 3) chat 停止：按 (owner, session_id) 隔离
# ══════════════════════════════════════════════════════════════════


def _reset_chat_state() -> None:
    from app.api.routes import chat_routes as cr

    cr._chat_stop_flags.clear()
    cr._chat_owners.clear()


def test_chat_stop_only_affects_own_session():
    from app.api.routes import chat_routes as cr

    _reset_chat_state()
    try:
        cr._set_chat_running("default", "user-a")
        cr._set_chat_running("default", "user-b")

        allowed, stopped = cr.request_chat_stop("default", "user-a")
        assert allowed is True
        assert stopped == ["user-a"]
        assert cr.is_chat_running("default", "user-a") is False
        assert cr.is_chat_running("default", "user-b") is True, "不能停掉别人在同 session 上的会话"
        assert cr._chat_owner("default", "user-b") == "user-b"

        # 模拟 A 的生成器收尾（清掉自己的归属记录）
        cr._set_chat_stopped("default", "user-a")

        # 第三个用户既不是本人也无特权 → 拒绝（路由回 404，不泄露存在性）
        allowed, stopped = cr.request_chat_stop("default", "user-c")
        assert (allowed, stopped) == (False, [])
        assert cr.is_chat_running("default", "user-b") is True

        # 租户 Owner/Admin 可兜底停止
        allowed, stopped = cr.request_chat_stop("default", "user-c", allow_others=True)
        assert allowed is True
        assert stopped == ["user-b"]
        assert cr.is_chat_running("default", "user-b") is False

        # 没有在跑的记录 → 幂等成功
        assert cr.request_chat_stop("no-such-session", "user-a") == (True, [])
    finally:
        _reset_chat_state()


def test_stop_chat_without_owner_stops_all_owners():
    """内部/特权路径：裸 session_id 也能命中复合键（旧实现查不到）。"""
    from app.api.routes import chat_routes as cr

    _reset_chat_state()
    try:
        cr._set_chat_running("default", "user-a")
        cr._set_chat_running("default", "user-b")

        assert sorted(cr.stop_chat("default")) == ["user-a", "user-b"]
        assert cr.is_chat_running("default", "user-a") is False
        assert cr.is_chat_running("default", "user-b") is False
    finally:
        _reset_chat_state()


@pytest.mark.asyncio
async def test_chat_stop_route_hides_other_users_session():
    """他人 session 的 stop 请求返回 404，且不会清掉对方的运行标志。"""
    from fastapi import HTTPException

    from app.api.routes import chat_routes as cr

    _reset_chat_state()
    try:
        cr._set_chat_running("default", "user-b")
        ctx = SimpleNamespace(user_id="user-a", tenant_role="member")

        with pytest.raises(HTTPException) as exc:
            await cr.chat_stop({"session_id": "default"}, ctx=ctx)
        assert exc.value.status_code == 404
        assert cr.is_chat_running("default", "user-b") is True
    finally:
        _reset_chat_state()

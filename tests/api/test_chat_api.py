"""API 层：对话链路的关键接口契约（真实 HTTP，8201 需已启动）。

覆盖用户实际会踩到的接口契约，而不是内部函数：

1. 鉴权边界：未带令牌 401，带令牌 200
2. 平台确认网关：写操作先 pending → 确认才执行 → 重复确认幂等
3. Agent 启停：start/stop 是写操作（必须确认），执行后状态生效
4. 会话隔离：新 session 的对话只落自己的消息，不污染其它会话
5. 对话 SSE：事件流以 done 收尾，且用户消息即时落库

未启动后端 / 无登录凭据时整批 skip（见 tests/api/conftest.py）。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.api

AGENT_ID = "city-info-query-agent"


def _request(base: str, path: str, token: str = "", method: str = "GET",
             body: dict | None = None, timeout: float = 60) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def _confirm(base: str, token: str, confirm: dict) -> tuple[int, dict]:
    return _request(base, "/api/chat/platform-call/confirm", token, "POST", {
        "opId": confirm["op_id"], "token": confirm["token"],
        "fingerprint": confirm["fingerprint"],
    })


# ────────────────────────── 鉴权 ──────────────────────────

def test_requires_login(backend_url):
    """多用户模式（配置了超管密码）下，未登录访问对话接口必须 401。

    未配置密码 = 单用户兼容模式，匿名按超管执行是既定行为，此时该断言不适用。
    """
    if not os.environ.get("SOTABAND_ROOT_PASSWORD"):
        pytest.skip("单用户模式（未配置 SOTABAND_ROOT_PASSWORD），匿名访问属预期行为")
    status, _ = _request(backend_url, "/api/chat/sessions")
    assert status == 401, "对话接口未鉴权，未登录也能读到会话列表"


def test_me_returns_user(backend_url, backend_token):
    status, body = _request(backend_url, "/api/auth/me", backend_token)
    assert status == 200
    assert body.get("authenticated") is True
    assert body.get("user", {}).get("id")


# ────────────────────────── 平台确认网关 ──────────────────────────

def test_write_op_needs_confirmation(backend_url, backend_token):
    """写操作只下发 pending，确认后才执行；确认内容与原请求一致。"""
    status, body = _request(backend_url, "/api/chat/platform-call", backend_token,
                            "POST", {"tool": "start_agent",
                                     "args": {"agent_id": AGENT_ID},
                                     "session_id": "api-confirm"})
    assert status == 200
    assert body.get("status") == "pending", f"写操作未要求确认: {body}"
    confirm = body["confirm"]
    assert confirm["tool"] == "start_agent"
    assert confirm["fingerprint"] and confirm["token"]

    status, done = _confirm(backend_url, backend_token, confirm)
    assert status == 200 and done.get("status") in ("ok", "executed"), done


def test_confirm_is_idempotent(backend_url, backend_token):
    """同一 op 重复确认返回首次结果，绝不重复执行。"""
    _, body = _request(backend_url, "/api/chat/platform-call", backend_token, "POST",
                       {"tool": "stop_agent", "args": {"agent_id": AGENT_ID},
                        "session_id": "api-idem"})
    confirm = body["confirm"]
    _, first = _confirm(backend_url, backend_token, confirm)
    _, second = _confirm(backend_url, backend_token, confirm)
    assert first.get("status") in ("ok", "executed"), first
    assert second.get("idempotent") is True, f"重复确认没有被幂等处理: {second}"


def test_unknown_tool_is_rejected(backend_url, backend_token):
    _, body = _request(backend_url, "/api/chat/platform-call", backend_token, "POST",
                       {"tool": "drop_database", "args": {}, "session_id": "api-x"})
    assert body.get("status") == "error"
    assert "未知平台工具" in str(body.get("error", ""))


# ────────────────────────── Agent 启停 ──────────────────────────

def test_agent_start_and_stop(backend_url, backend_token):
    """start/stop 经确认后生效，且不会把 Agent 弄成不可用状态。"""
    for tool in ("start_agent", "stop_agent"):
        _, body = _request(backend_url, "/api/chat/platform-call", backend_token,
                           "POST", {"tool": tool, "args": {"agent_id": AGENT_ID},
                                    "session_id": "api-lifecycle"})
        assert body.get("status") == "pending", f"{tool} 未要求确认: {body}"
        _, done = _confirm(backend_url, backend_token, body["confirm"])
        assert done.get("status") in ("ok", "executed"), f"{tool} 执行失败: {done}"
        result = done.get("result") or {}
        assert result.get("agent_id") == AGENT_ID
        assert result.get("status") in ("started", "already_running", "stopped")


# ────────────────────────── 会话隔离 ──────────────────────────

def test_new_session_messages_are_isolated(backend_url, backend_token):
    """「必须从新会话开始」：新 session 的消息不会出现在其它会话里。"""
    sid_a = f"api-isolation-a-{int(time.time())}"
    sid_b = f"api-isolation-b-{int(time.time())}"
    # 用 curl -sN 按 SSE 读：不依赖流内事件顺序；只关心「消息落库到自己的会话」
    curl = subprocess.run(  # noqa: S603
        ["curl", "-sN", "--max-time", "180",
         "-H", "Content-Type: application/json",
         "-H", f"Authorization: Bearer {backend_token}",
         "-d", json.dumps({"content": f"会话隔离探针 {sid_a}",
                           "sessionId": sid_a, "attachments": [],
                           "workspaceToolIds": []}),
         f"{backend_url}/api/chat/send"],
        capture_output=True, text=True, timeout=200)
    assert curl.returncode == 0, curl.stderr[:300]

    _, a = _request(backend_url, f"/api/chat/sessions/{sid_a}", backend_token)
    _, b = _request(backend_url, f"/api/chat/sessions/{sid_b}", backend_token)
    text_a = " ".join(str(m.get("content", "")) for m in a.get("messages", []))
    text_b = " ".join(str(m.get("content", "")) for m in b.get("messages", []))
    assert sid_a in text_a, f"会话 A 没收到自己的消息: {text_a}"
    assert sid_b not in text_a, f"会话 A 混入了 B 的消息: {text_a}"
    assert not text_b, f"没发过消息的会话 B 却有内容（串会话）: {text_b}"


# ────────────────────────── 对话 SSE ──────────────────────────

def test_chat_stream_ends_with_done(backend_url, backend_token):
    """对话接口是 SSE：事件流必须以 done 收尾，用户消息即时落库。"""
    sid = f"api-sse-{int(time.time())}"
    curl = subprocess.run(  # noqa: S603
        ["curl", "-sN", "--max-time", "180",
         "-H", "Content-Type: application/json",
         "-H", f"Authorization: Bearer {backend_token}",
         "-d", json.dumps({"content": "你好", "sessionId": sid,
                           "attachments": [], "workspaceToolIds": []}),
         f"{backend_url}/api/chat/send"],
        capture_output=True, text=True, timeout=200)
    assert curl.returncode == 0, curl.stderr[:300]
    events = [ln.split(":", 1)[1].strip() for ln in curl.stdout.splitlines()
              if ln.startswith("event:")]
    assert events, f"SSE 没有收到任何事件: {curl.stdout[:300]}"
    assert "done" in events, f"SSE 未以 done 收尾: {events}"
    assert events[-1] in ("done", "title"), f"done 之后不应再有业务事件: {events[-3:]}"

    _, saved = _request(backend_url, f"/api/chat/sessions/{sid}", backend_token)
    contents = [str(m.get("content", "")) for m in saved.get("messages", [])]
    assert any("你好" in c for c in contents), f"用户消息没有即时落库: {contents}"

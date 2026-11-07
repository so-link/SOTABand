"""API 层：对话平台确认网关的取消/待确认/审计，以及会话与 Agent 生命周期。

覆盖面（都是前端直接依赖、此前无 HTTP 层用例的接口）：

1. 取消待确认操作：取消成功 → 重复确认被拒 → pending 清空
2. 待确认查询：只按 session 查找，未确认时能拿到卡片数据
3. 平台操作审计流水：按 session 过滤、只含自己、字段结构
4. 停止对话：缺 session_id → 400；正常请求返回 ok
5. 删除会话：删除后会话列表里不再出现
6. Agent 状态/停止/对话历史：可见性校验（不存在的 Agent → 404）
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.api

AGENT_ID = "city-info-query-agent"


def _call(base: str, token: str, path: str, method: str = "GET",
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
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def _pending(base: str, token: str, session_id: str) -> dict | None:
    """经网关下发一张待确认卡（写操作），返回 confirm 数据。"""
    _, body = _call(base, token, "/api/chat/platform-call", "POST",
                    {"tool": "stop_agent", "args": {"agent_id": AGENT_ID},
                     "session_id": session_id})
    return body.get("confirm")


# ────────────────────── 取消 / 待确认 ──────────────────────

def test_cancel_pending_op_then_confirm_is_rejected(backend_url, backend_token):
    """取消后不能被执行：confirm 返回 error，pending 被清空。"""
    sid = f"api-cancel-{int(time.time())}"
    confirm = _pending(backend_url, backend_token, sid)
    assert confirm and confirm.get("op_id"), "网关没有下发待确认操作"

    status, body = _call(backend_url, backend_token, "/api/chat/platform-call/cancel",
                         "POST", {"opId": confirm["op_id"]})
    assert status == 200 and body.get("status") == "ok", body

    # 取消后再确认：应为 error（不是 ok/executed）
    status, body = _call(backend_url, backend_token,
                         "/api/chat/platform-call/confirm", "POST",
                         {"opId": confirm["op_id"], "token": confirm["token"],
                          "fingerprint": confirm["fingerprint"]})
    assert status == 200, body
    assert body.get("status") == "error", f"已取消的操作仍被执行: {body}"
    assert "cancelled" in str(body.get("error", "")) or "取消" in str(body.get("error", "")), body

    _, pending = _call(backend_url, backend_token,
                       f"/api/chat/platform-call/pending?session_id={sid}")
    assert pending.get("pending") is None, f"取消后仍残留待确认卡片: {pending}"


def test_pending_endpoint_returns_card_before_confirm(backend_url, backend_token):
    sid = f"api-pending-{int(time.time())}"
    confirm = _pending(backend_url, backend_token, sid)

    _, body = _call(backend_url, backend_token,
                    f"/api/chat/platform-call/pending?session_id={sid}")
    pending = body.get("pending") or {}
    assert pending.get("op_id") == confirm["op_id"], f"待确认卡片不匹配: {body}"
    # 前端确认卡渲染依赖这些字段
    for key in ("tool", "summary", "status", "expires_at"):
        assert key in pending, f"待确认卡片缺字段 {key}: {list(pending)}"
    assert pending["tool"] == "stop_agent"

    # 收尾：取消，避免留下悬挂的待确认操作
    _call(backend_url, backend_token, "/api/chat/platform-call/cancel", "POST",
          {"opId": confirm["op_id"]})


def test_cancel_unknown_op_returns_error(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/chat/platform-call/cancel",
                         "POST", {"opId": "op-not-exist-xyz"})
    assert status == 200
    assert body.get("status") == "error", f"取消不存在的操作应报错: {body}"


# ────────────────────── 审计流水 ──────────────────────

def test_platform_ops_audit_records_cancelled(backend_url, backend_token):
    """取消会进审计流水，且能按 session 过滤出来（前端历史回放依赖它）。"""
    sid = f"api-audit-{int(time.time())}"
    confirm = _pending(backend_url, backend_token, sid)
    _call(backend_url, backend_token, "/api/chat/platform-call/cancel", "POST",
          {"opId": confirm["op_id"]})

    _, body = _call(backend_url, backend_token,
                    f"/api/chat/platform-ops?session_id={sid}&limit=50")
    ops = body.get("ops") or []
    matched = [o for o in ops if o.get("op_id") == confirm["op_id"]]
    assert matched, f"审计流水里找不到本次操作: {ops}"
    op = matched[0]
    assert op.get("status") == "cancelled", op
    for key in ("id", "op_id", "session_id", "tool", "risk", "created_at"):
        assert key in op, f"审计记录缺字段 {key}: {list(op)}"
    assert all(o.get("session_id") == sid for o in ops), \
        f"按 session 过滤失效，混入了别的会话: {[o.get('session_id') for o in ops]}"


# ────────────────────── 停止 / 删除会话 ──────────────────────

def test_stop_requires_session_id(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, "/api/chat/stop", "POST", {})
    assert status == 400, f"缺 session_id 应 400: {status} {body}"


def _start_chat_session(base: str, token: str, sid: str, content: str) -> None:
    """发一条消息让会话落库：SSE 只读首包（用户消息在流开始前就已入库）。"""
    req = urllib.request.Request(
        f"{base}/api/chat/send", method="POST",
        data=json.dumps({"content": content, "sessionId": sid}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read(64)          # 读到第一个事件即可，随后主动断开
    except Exception:  # noqa: BLE001
        pass                       # 超时/断开都正常：落库发生在流开始之前


def test_stop_idle_session_is_safe(backend_url, backend_token):
    """对没有在跑的会话发停止：不报错，且明确告知没有可中断的对话。"""
    sid = f"api-stop-{int(time.time())}"
    status, body = _call(backend_url, backend_token, "/api/chat/stop", "POST",
                         {"session_id": sid})
    assert status == 200
    assert body.get("status") == "ok"
    # interrupt 的取值随实现变化（not_running/cancel/done…），这里只要求
    # 「不是中断失败」：不能出现 error 前缀
    interrupt = str(body.get("interrupt", ""))
    assert not interrupt.startswith("error"), f"停止空闲会话出错: {body}"


def test_delete_session(backend_url, backend_token):
    sid = f"api-del-{int(time.time())}"
    _start_chat_session(backend_url, backend_token, sid, "待删除会话")
    _, before = _call(backend_url, backend_token, "/api/chat/sessions?limit=100")
    assert sid in [s.get("session_id") for s in before.get("sessions", [])], \
        f"会话没有落库，无法验证删除: {sid}"

    status, body = _call(backend_url, backend_token, "/api/chat/sessions/" + sid, "DELETE")
    assert status == 200 and body.get("ok") is True, body

    _, after = _call(backend_url, backend_token, "/api/chat/sessions?limit=100")
    assert sid not in [s.get("session_id") for s in after.get("sessions", [])], \
        "删除后会话仍在列表里"


# ────────────────────── Agent 状态 / 历史 ──────────────────────

def test_agent_status_includes_running(backend_url, backend_token):
    status, body = _call(backend_url, backend_token, f"/api/agent/{AGENT_ID}/status")
    assert status == 200
    assert body.get("id") == AGENT_ID
    assert "running" in body, f"状态接口缺 running 字段: {list(body)}"


def test_agent_chat_history_shape(backend_url, backend_token):
    status, body = _call(backend_url, backend_token,
                         f"/api/agent/{AGENT_ID}/chat-history")
    assert status == 200
    assert body.get("session_id") == f"agent-chat-{AGENT_ID}", body
    assert isinstance(body.get("messages"), list)


def test_agent_chat_history_unknown_agent_404(backend_url, backend_token):
    status, _ = _call(backend_url, backend_token,
                      "/api/agent/not-exist-agent-xyz/chat-history")
    assert status == 404


def test_agent_stop_unknown_agent_404(backend_url, backend_token):
    status, _ = _call(backend_url, backend_token,
                      "/api/agent/not-exist-agent-xyz/stop", "POST")
    assert status == 404

"""待确认操作存储 — 写操作的二次确认协议（设计文档第 5 节）。

安全不变量：

1. **未确认绝不执行**：写/高危工具在网关侧只生成 pending 操作，处理器不会被调用。
2. **确认内容 = 执行内容**：fingerprint = sha256(tool + 规范化 args)。确认时
   必须回传一致的内容指纹，否则拒绝——防止"确认后参数被替换"（TOCTOU）。
3. **归属校验**：只有 owner 本人能确认自己的操作；token 随机下发防跨会话重放。
4. **幂等**：同 op_id 二次确认返回首次执行结果，绝不重复执行。
5. **单会话单待确认**：新的写请求把旧的 pending 置 superseded，用户不会面对
   多张互相冲突的卡片。

状态机：pending → confirmed → executed / failed
        pending → cancelled（用户取消）
        pending → expired（超时）
        pending → superseded（被新请求顶替）

存储：pending 操作放内存（进程内，与定时任务模块同单 worker 前提），
状态流水同时落 chat_ops（见 audit.py），保证事后可审计。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

CONFIRM_TTL_SECONDS = 900  # 15 分钟

# 终态（不再可变更）
TERMINAL = {"executed", "failed", "cancelled", "expired", "superseded"}


def fingerprint(tool: str, args: dict) -> str:
    """工具名 + 规范化 JSON 参数 → sha256。键序无关。"""
    payload = json.dumps(
        {"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class PendingOp:
    op_id: str
    tool: str
    args: dict
    risk: str
    summary: str
    args_view: dict
    owner: str
    session_id: str
    token: str
    fp: str
    created_at: float
    expires_at: float
    status: str = "pending"
    result: dict | None = None
    error: str = ""
    events: list = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        """下发给前端/模型的安全视图（不含 args 原值以外的敏感信息）。"""
        return {
            "op_id": self.op_id,
            "tool": self.tool,
            "risk": self.risk,
            "summary": self.summary,
            "args_view": self.args_view,
            "fingerprint": self.fp,
            "token": self.token,
            "status": self.status,
            "expires_at": self.expires_at,
            "ttl_seconds": max(0, int(self.expires_at - time.time())),
        }


_lock = threading.Lock()
_ops: dict[str, PendingOp] = {}
# 幂等结果缓存：op_id → 首次执行结果（终态后从 _ops 摘除，缓存保留一段时间）
_results: dict[str, tuple[float, dict]] = {}

RESULT_CACHE_TTL = 3600


def create(*, tool: str, args: dict, risk: str, summary: str, args_view: dict,
           owner: str, session_id: str) -> PendingOp:
    """登记一个待确认操作，并把同会话旧的 pending 置为 superseded。"""
    now = time.time()
    op = PendingOp(
        op_id=f"op_{int(now * 1000)}_{uuid.uuid4().hex[:6]}",
        tool=tool, args=args, risk=risk, summary=summary, args_view=args_view,
        owner=owner, session_id=session_id,
        token=secrets.token_urlsafe(24),
        fp=fingerprint(tool, args),
        created_at=now, expires_at=now + CONFIRM_TTL_SECONDS,
    )
    with _lock:
        _purge_locked(now)
        for other in _ops.values():
            if (other.session_id == session_id and other.status == "pending"):
                other.status = "superseded"
                other.events.append(("superseded", now))
        _ops[op.op_id] = op
    return op


def get(op_id: str) -> PendingOp | None:
    with _lock:
        return _ops.get(op_id)


def pending_for(session_id: str) -> dict | None:
    """当前会话待确认操作（供刷新后前端恢复卡片）。"""
    now = time.time()
    with _lock:
        for op in _ops.values():
            if op.session_id != session_id or op.status != "pending":
                continue
            if now > op.expires_at:
                op.status = "expired"
                continue
            return op.public()
    return None


def cancel(op_id: str, owner: str) -> tuple[bool, str]:
    with _lock:
        op = _ops.get(op_id)
        if not op:
            return False, "操作不存在或已过期"
        if op.owner != owner:
            return False, "无权操作该请求"
        if op.status != "pending":
            return False, f"操作已处于 {op.status} 状态"
        op.status = "cancelled"
        op.events.append(("cancelled", time.time()))
    return True, ""


class ConfirmError(Exception):
    """确认校验失败（附带面向用户的中文原因）。"""


def mark_confirmed(op_id: str, owner: str, token: str,
                   fingerprint_: str | None = None) -> PendingOp:
    """校验并锁定为 confirmed（执行前调用）。失败抛 ConfirmError。

    标记 confirmed 而非直接执行，是为了让"执行中"这一状态可审计：
    执行成功/失败由 finish() 收尾。
    """
    now = time.time()
    with _lock:
        op = _ops.get(op_id)
        if op is None:
            cached = _results.get(op_id)
            if cached:
                raise ConfirmError("该操作已执行完成（幂等），不会重复执行")
            raise ConfirmError("确认已失效（超时或被顶替），请重新发起")
        if op.status == "superseded":
            raise ConfirmError("该确认已被新的操作顶替，请重新发起")
        if op.status == "expired" or now > op.expires_at:
            op.status = "expired"
            raise ConfirmError("确认已超时（15 分钟），请重新发起")
        if op.status != "pending":
            raise ConfirmError(f"操作已处于 {op.status} 状态，不能重复确认")
        if op.owner != owner:
            raise ConfirmError("无权确认他人的操作")
        if not secrets.compare_digest(op.token, token or ""):
            raise ConfirmError("确认令牌不匹配")
        if fingerprint_ and fingerprint_ != op.fp:
            raise ConfirmError("确认内容与原请求不一致，已拒绝执行")
        op.status = "confirmed"
        op.events.append(("confirmed", now))
        return op


def finish(op_id: str, status: str, result: dict | None = None,
           error: str = "") -> None:
    """执行收尾：记录终态并缓存结果供幂等返回。"""
    now = time.time()
    with _lock:
        op = _ops.get(op_id)
        if op:
            op.status = status
            op.result = result
            op.error = error
            op.events.append((status, now))
            _results[op_id] = (now, {"status": status, "result": result, "error": error})
            _ops.pop(op_id, None)


def cached_result(op_id: str) -> dict | None:
    now = time.time()
    with _lock:
        hit = _results.get(op_id)
        if not hit:
            return None
        if now - hit[0] > RESULT_CACHE_TTL:
            _results.pop(op_id, None)
            return None
        return hit[1]


def _purge_locked(now: float) -> None:
    """清理超时 pending 与过期结果缓存（在锁内调用）。"""
    for oid, op in list(_ops.items()):
        if op.status == "pending" and now > op.expires_at:
            op.status = "expired"
            _ops.pop(oid, None)
        elif op.status in TERMINAL:
            _ops.pop(oid, None)
    for oid, (ts, _) in list(_results.items()):
        if now - ts > RESULT_CACHE_TTL:
            _results.pop(oid, None)

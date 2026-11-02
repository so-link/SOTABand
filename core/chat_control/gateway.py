"""平台控制网关 — 对话内所有平台操作的唯一入口。

职责（设计文档第 3/5/6 节）：

1. 熔断开关：`platform_control_enabled` 关闭时一切 platform 调用返回"能力已停用"。
2. 工具名白名单：LLM 幻觉出的工具名在这里查不到 → 拒绝，不执行。
3. 参数严格校验：多余键/错类型回错误文本给 LLM 自我修正。
4. 风险分级：read 直接执行；write/danger 只生成待确认操作，**绝不在本轮执行**。
5. 身份注入：以发起对话的用户身份执行，权限边界与手工操作一致。
6. 审计：每次调用（含只读）落 chat_ops；结果卡片只认这里的回执。

调用方有两个：HTTP 端点（P0-3）与子进程协议帧路由（P0-4），二者共用本函数，
保证"审计点唯一"。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from config.settings import settings
from core.auth.session import AuthUser, reset_current_user, set_current_user
from core.chat_control import audit, confirm, registry as reg
from core.chat_control.registry import RISK_DANGER, RISK_READ, RISK_WRITE, CallContext

# 网关全链路硬超时（生成类长任务沿用现状，此处给足余量）
GATEWAY_TIMEOUT = 300

# 熔断开关（settings 里未定义时按启用处理，保持默认能力可用）
def control_enabled() -> bool:
    chat_cfg = getattr(settings, "chat", None)
    return bool(getattr(chat_cfg, "platform_control_enabled", True))


def _cfg() -> Any:
    return getattr(settings, "chat", None)


# ── 速率限制（设计文档 §6.5）：每用户 30 次/分钟；生成类 5 次/10 分钟 ──
# 生成类工具烧 LLM token，成本与滥用风险都远高于普通调用，单独更严的窗口。
GEN_TOOLS = {"create_tool", "create_agent"}

_rate_lock = threading.Lock()
_calls: dict[str, deque] = defaultdict(deque)       # owner → 调用时间戳
_gens: dict[str, deque] = defaultdict(deque)         # owner → 生成类调用时间戳


def rate_check(owner: str, name: str) -> str:
    """返回 "" 表示放行；否则为面向 LLM 的拒绝原因。"""
    now = time.time()
    per_min = int(getattr(_cfg(), "rate_limit_per_minute", 30) or 30)
    gen_10min = int(getattr(_cfg(), "gen_limit_per_10min", 5) or 5)
    with _rate_lock:
        q = _calls[owner]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= per_min:
            return f"平台调用过于频繁（每分钟最多 {per_min} 次），请一分钟后再试。"
        if name in GEN_TOOLS:
            g = _gens[owner]
            while g and g[0] < now - 600:
                g.popleft()
            if len(g) >= gen_10min:
                return f"生成类操作过于频繁（每 10 分钟最多 {gen_10min} 次），请稍后再试。"
            g.append(now)
        q.append(now)
        return ""


async def platform_call(
    name: str,
    args: Any,
    *,
    owner: str,
    session_id: str = "default",
    emit: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """执行一次平台调用。

    返回结构对调用方（子进程 / HTTP）统一：
      {"status": "ok",        "tool":..., "result": {...}}           只读已执行
      {"status": "pending",   "confirm": {...卡片字段...}}            等待用户确认
      {"status": "error",     "tool":..., "error": "..."}            被拒绝/执行失败
    """
    if not control_enabled():
        return {"status": "error", "tool": name,
                "error": "平台控制能力已停用（settings.chat.platform_control_enabled=false）"}

    # ⓪ 速率限制（按 owner 计；确认/取消端点不占额度，只有真实调用计）
    limited = rate_check(owner, str(name))
    if limited:
        audit.record(op_id="", session_id=session_id, owner=owner, tool=str(name),
                     args={}, risk="unknown", status="rate_limited", error=limited)
        return {"status": "error", "tool": name, "error": limited}

    # ① 工具名白名单
    tool = reg.get(name)
    if tool is None or not tool.enabled:
        audit.record(op_id="", session_id=session_id, owner=owner, tool=str(name),
                     args={}, risk="unknown", status="rejected_unknown_tool",
                     error="工具不在注册表内")
        return {"status": "error", "tool": name,
                "error": f"未知平台工具: {name}。可用工具：{', '.join(reg.tool_names())}"}

    # ② 参数严格校验
    model, err = reg.validate_args(tool, args)
    if model is None:
        return {"status": "error", "tool": name, "error": err}
    clean = model.model_dump()

    # ③ 写/高危操作 → 只生成待确认，不执行
    if tool.risk != RISK_READ:
        op = confirm.create(
            tool=name, args=clean, risk=tool.risk,
            summary=reg.summary_for(tool, clean),
            args_view=reg.args_view_for(tool, clean),
            owner=owner, session_id=session_id,
        )
        audit.record(op_id=op.op_id, session_id=session_id, owner=owner, tool=name,
                     args=clean, risk=tool.risk, status="pending")
        return {"status": "pending", "tool": name, "confirm": op.public(),
                "hint": "已向用户发出确认卡片。请告知用户在卡片上确认；"
                        "在用户确认前该操作不会执行，也不要声称已执行。"}

    # ④ 只读直通执行
    return await execute_read(name, clean, tool, owner=owner, session_id=session_id,
                             emit=emit)


async def execute_read(name: str, clean: dict, tool, *, owner: str,
                       session_id: str,
                       emit: Callable[[dict], Awaitable[None]] | None = None) -> dict:
    token = set_current_user(AuthUser(id=owner, username="", role="user"))
    started = time.time()
    try:
        result = await tool.handler(tool.args_model.model_validate(clean),
                                   CallContext(owner=owner, session_id=session_id, emit=emit))
        ms = int((time.time() - started) * 1000)
        failed = isinstance(result, dict) and bool(result.get("error"))
        audit.record(op_id="", session_id=session_id, owner=owner, tool=name,
                     args=clean, risk=tool.risk,
                     status="failed" if failed else "executed",
                     result=result, error=str(result.get("error", "")) if failed else "",
                     duration_ms=ms)
        if failed:
            return {"status": "error", "tool": name, "error": result["error"]}
        return {"status": "ok", "tool": name, "result": result}
    except Exception as e:
        ms = int((time.time() - started) * 1000)
        audit.record(op_id="", session_id=session_id, owner=owner, tool=name,
                     args=clean, risk=tool.risk, status="failed",
                     error=f"{type(e).__name__}: {e}", duration_ms=ms)
        return {"status": "error", "tool": name, "error": f"{type(e).__name__}: {e}"}
    finally:
        reset_current_user(token)


async def confirm_op(op_id: str, *, owner: str, token: str,
                     fingerprint: str | None = None,
                     emit: Callable[[dict], Awaitable[None]] | None = None) -> dict:
    """用户点「确认」→ 校验 → 执行 → 结果。幂等：重复确认返回首次结果。"""
    cached = confirm.cached_result(op_id)
    if cached:
        return {"status": cached.get("status", "ok"), "op_id": op_id,
                "result": cached.get("result"), "idempotent": True}

    try:
        op = confirm.mark_confirmed(op_id, owner=owner, token=token,
                                   fingerprint_=fingerprint)
    except confirm.ConfirmError as e:
        audit.record(op_id=op_id, session_id="", owner=owner, tool="", args={},
                     risk="write", status="confirm_rejected", error=str(e))
        return {"status": "error", "op_id": op_id, "error": str(e)}

    audit.record(op_id=op_id, session_id=op.session_id, owner=owner, tool=op.tool,
                 args=op.args, risk=op.risk, status="confirmed")
    out = await execute_confirmed(op, emit=emit)
    confirm.finish(op_id, "executed" if out["status"] == "ok" else "failed",
                   result=out.get("result"), error=out.get("error", ""))
    return out


async def execute_confirmed(op, *, emit=None) -> dict:
    """执行已确认的操作（身份 = op.owner）。"""
    tool = reg.get(op.tool)
    if tool is None:
        return {"status": "error", "op_id": op.op_id, "tool": op.tool,
                "error": f"工具已不存在: {op.tool}"}
    token = set_current_user(AuthUser(id=op.owner, username="", role="user"))
    started = time.time()
    try:
        result = await tool.handler(tool.args_model.model_validate(op.args),
                                   CallContext(owner=op.owner,
                                               session_id=op.session_id, emit=emit))
        ms = int((time.time() - started) * 1000)
        failed = isinstance(result, dict) and bool(result.get("error"))
        status = "failed" if failed else "executed"
        audit.record(op_id=op.op_id, session_id=op.session_id, owner=op.owner,
                     tool=op.tool, args=op.args, risk=op.risk, status=status,
                     result=result,
                     error=str(result.get("error", "")) if failed else "",
                     duration_ms=ms)
        if failed:
            return {"status": "error", "op_id": op.op_id, "tool": op.tool,
                    "error": result["error"]}
        return {"status": "ok", "op_id": op.op_id, "tool": op.tool, "result": result}
    except Exception as e:
        ms = int((time.time() - started) * 1000)
        err = f"{type(e).__name__}: {e}"
        audit.record(op_id=op.op_id, session_id=op.session_id, owner=op.owner,
                     tool=op.tool, args=op.args, risk=op.risk, status="failed",
                     error=err, duration_ms=ms)
        return {"status": "error", "op_id": op.op_id, "tool": op.tool, "error": err}
    finally:
        reset_current_user(token)


def cancel_op(op_id: str, *, owner: str) -> dict:
    ok, msg = confirm.cancel(op_id, owner)
    op = confirm.get(op_id)
    audit.record(op_id=op_id, session_id=op.session_id if op else "", owner=owner,
                 tool=op.tool if op else "", args={}, risk=op.risk if op else "write",
                 status="cancelled" if ok else "cancel_rejected", error=msg)
    return {"status": "ok" if ok else "error", "op_id": op_id} if ok \
        else {"status": "error", "op_id": op_id, "error": msg}


def pending_op(session_id: str) -> dict | None:
    return confirm.pending_for(session_id)


def tool_schemas() -> list[dict]:
    """给 LLM 的能力清单（引擎静态注入，不受工具返回值影响）。"""
    if not control_enabled():
        return []
    return reg.schemas()


def init() -> None:
    """建审计表（启动时调用）。"""
    audit.init()

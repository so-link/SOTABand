"""对话路由 — SSE 流式响应

交互 Agent 以**独立进程**运行（经 AgentFactory 管理），本路由作为
引擎侧代理：接收前端 SSE 请求 → 转发给交互 Agent 子进程 → 流式回传事件。

设计要点（见 docs/agent-types-design.md）：
- 所有 Agent 运行时均为独立进程，交互 Agent 不再与引擎同进程。
- 对前端接口保持完全兼容（/api/chat/send、/api/chat/stop 契约不变）。
- 引擎启动时自动拉起「系统默认交互 Agent」，但它同样可被手动关闭 / 重启。
"""

import json
import asyncio
import threading
from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_auth_context
from app.api.schemas.chat_schemas import ChatRequest
from core.agent.factory import agent_factory
from core.auth.context import AuthContext
from core.user_context import resolve_owner_id

# ── 系统默认交互 Agent 的标识 ──
# 它是系统默认入口：引擎启动时自动拉起；用户自定义交互 Agent 则需手动启动。
DEFAULT_INTERACTIVE_AGENT_ID = "interactive-agent"
DEFAULT_INTERACTIVE_IMPL_PATH = "resources/agents/implementations/interactive_agent"

router = APIRouter()

# ── 对话停止标志 ──
# session_id 由前端生成、可被猜测；因此同时记录「这次对话属于谁」，
# 停止接口据此做归属校验（否则任何登录用户都能停别人的对话）。
_chat_stop_flags: dict[str, bool] = {}
_chat_owners: dict[str, str] = {}
_chat_stop_lock = threading.Lock()

#: 租户 Owner/Admin 可兜底停止本租户内任何会话
_CHAT_STOP_PRIVILEGED_ROLES = ("owner", "admin")


def _chat_key(session_id: str, owner_id: str | None) -> str:
    """停止标志/归属的复合键：同一安装下不同用户可能用相同 session_id。

    前端固定 ``sessionId``（默认 "default"）时，若只用 session_id 做键，
    A/B 会共享停止标志与归属记录（B 可停 A 的对话）。
    """
    return f"{owner_id or '-'}::{session_id}"


def _set_chat_running(session_id: str, owner_id: str | None = None):
    key = _chat_key(session_id, owner_id)
    with _chat_stop_lock:
        _chat_stop_flags[key] = True
        if owner_id:
            _chat_owners[key] = owner_id


def _set_chat_stopped(session_id: str, owner_id: str | None = None):
    key = _chat_key(session_id, owner_id)
    with _chat_stop_lock:
        _chat_stop_flags.pop(key, None)
        _chat_owners.pop(key, None)


def _chat_owner(session_id: str, owner_id: str | None) -> str | None:
    with _chat_stop_lock:
        return _chat_owners.get(_chat_key(session_id, owner_id))


def _chat_session_keys(session_id: str) -> list[str]:
    """该 session 对应的全部复合键（调用方必须已持有 ``_chat_stop_lock``）。"""
    suffix = f"::{session_id}"
    return [key for key in _chat_stop_flags if key.endswith(suffix)]


def _chat_session_owners(session_id: str) -> list[str]:
    """该 session 对应的全部 owner（调用方必须已持有 ``_chat_stop_lock``）。"""
    suffix = f"::{session_id}"
    owners: list[str] = []
    for key, owner in _chat_owners.items():
        if key.endswith(suffix) and owner and owner not in owners:
            owners.append(owner)
    return owners


def request_chat_stop(
    session_id: str,
    requester: str | None,
    *,
    allow_others: bool = False,
) -> tuple[bool, list[str]]:
    """按归属决定并设置停止标志（扫描全部 ``*::<session_id>`` 键）。

    Returns:
        ``(是否允许, 实际被停止的 owner 列表)``。

    规则（在 ``_chat_stop_lock`` 内一次性完成，避免「先查后停」的竞态）：

    - 默认只停**自己的**复合键 ``(requester, session_id)``；
    - 该 session 只属于别人时：``allow_others``（租户 Owner/Admin）才允许
      全部停止，否则返回 ``(False, [])`` —— 路由据此回 404，不泄露存在性；
    - 没有在跑的记录时返回 ``(True, [])``（停止是幂等操作）。

    session_id 由前端生成、可被猜测；裸 session_id 无法定位复合键，所以这里
    必须扫描，而不是 ``_chat_stop_flags.get(session_id)``（那样永不命中）。
    """
    with _chat_stop_lock:
        own_key = _chat_key(session_id, requester or "-")
        flag_keys = _chat_session_keys(session_id)
        owners = _chat_session_owners(session_id)
        if allow_others:
            for key in flag_keys:
                _chat_stop_flags[key] = False
            return True, owners
        if own_key in flag_keys:
            _chat_stop_flags[own_key] = False
            return True, [requester] if requester else []
        if owners:
            # 同 session 的会话属于别人：不确认"存在但不属于你"
            return False, []
        return True, []


def stop_chat(session_id: str, owner_id: str | None = None) -> list[str]:
    """外部/内部调用：请求停止指定 session 的对话，返回被停止的 owner 列表。

    ``owner_id`` 为空表示停止该 session 下**所有** owner（特权/内部路径）；
    否则只停该 owner 自己的复合键。
    """
    _, owners = request_chat_stop(
        session_id, owner_id, allow_others=owner_id is None
    )
    return owners


def is_chat_running(session_id: str, owner_id: str | None = None) -> bool:
    with _chat_stop_lock:
        return _chat_stop_flags.get(_chat_key(session_id, owner_id), False)


async def ensure_default_interactive_agent():
    """确保系统默认交互 Agent 进程已启动（引擎启动时调用，幂等）。"""
    if agent_factory.is_running(DEFAULT_INTERACTIVE_AGENT_ID):
        return {"status": "already_running"}
    return await agent_factory.start(
        DEFAULT_INTERACTIVE_AGENT_ID, DEFAULT_INTERACTIVE_IMPL_PATH
    )


@router.post("/send")
async def chat_send(
    request: ChatRequest,
    ctx: AuthContext = Depends(get_auth_context),
):
    """
    发送消息给交互 Agent，返回 SSE 流式响应。

    事件类型:
    - content: 文本增量 {"text": "..."}
    - card: 内联卡片 {"type": "...", "title": "...", "data": {...}}
    - done: 响应结束 {"messageId": "..."}
    - error: 错误 {"code": "...", "message": "..."}
    """

    session_id = request.session_id or "default"
    owner_id = resolve_owner_id(ctx)

    # 交互 Agent 未运行时，自动拉起（保证开箱可用；亦可被用户手动关闭）
    if not agent_factory.is_running(DEFAULT_INTERACTIVE_AGENT_ID):
        await ensure_default_interactive_agent()

    # 构建附件列表（兼容 camelCase 和 snake_case）
    attachments = [
        {
            "fileName": att.fileName,
            "filePath": att.filePath,
            "fileSize": att.fileSize,
            "format": att.format,
        }
        for att in (request.attachments or [])
    ]

    _set_chat_running(session_id, owner_id)

    async def event_generator():
        stream = agent_factory.execute_stream(
            DEFAULT_INTERACTIVE_AGENT_ID,
            params={
                "content": request.content,
                "attachments": attachments,
                "workspace_tool_ids": request.workspace_tool_ids,
            },
            context={"session_id": session_id, "user_id": owner_id},
            # 不设总超时：工具可能长时间执行（如模型训练），由用户通过
            # 停止按钮主动中断，而非被固定超时提前截断。
            timeout=None,
        )
        try:
            async for event in stream:
                # 每 yield 一次检查是否被停止（复合键：只会是自己这条会话）
                if not is_chat_running(session_id, owner_id):
                    yield {
                        "event": "stopped",
                        "data": json.dumps({"message": "对话已停止"}, ensure_ascii=False),
                    }
                    return
                yield {
                    "event": event.get("event", "content"),
                    "data": json.dumps(event.get("data", {}), ensure_ascii=False),
                }
        finally:
            # 显式关闭工厂侧的 async generator：否则它可能长期挂在某个 yield
            # 上不释放 execution_lock，也来不及把「未读到 done」标记为通道失步。
            try:
                try:
                    await stream.aclose()
                except Exception:
                    pass
            finally:
                _set_chat_stopped(session_id, owner_id)

    return EventSourceResponse(event_generator())


@router.post("/stop")
async def chat_stop(
    req: dict,
    ctx: AuthContext = Depends(get_auth_context),
):
    """停止指定 session 的对话（仅限本人；租户 Owner/Admin 可兜底）"""
    session_id = req.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")

    me = resolve_owner_id(ctx)
    role = getattr(ctx, "tenant_role", None) if ctx is not None else None
    allowed, stopped = request_chat_stop(
        session_id, me, allow_others=role in _CHAT_STOP_PRIVILEGED_ROLES
    )
    if not allowed:
        # 404 而不是 403：不确认"这个 session 存在但不属于你"
        raise HTTPException(status_code=404, detail="对话不存在")

    return {
        "status": "ok",
        "message": f"已请求停止对话 {session_id}",
        "stopped": stopped,
    }


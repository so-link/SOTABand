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
import time
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.schemas.chat_schemas import (
    ChatRequest,
    PlatformCallRequest,
    PlatformConfirmRequest,
)
from core.agent.factory import agent_factory

# ── 系统默认交互 Agent 的标识 ──
# 它是系统默认入口：引擎启动时自动拉起；用户自定义交互 Agent 则需手动启动。
DEFAULT_INTERACTIVE_AGENT_ID = "interactive-agent"
DEFAULT_INTERACTIVE_IMPL_PATH = "resources/agents/implementations/interactive_agent"

router = APIRouter()

# ── 对话停止标志 ──
_chat_stop_flags: dict[str, bool] = {}
_chat_stop_lock = threading.Lock()


def _set_chat_running(session_id: str):
    with _chat_stop_lock:
        _chat_stop_flags[session_id] = True


def _set_chat_stopped(session_id: str):
    with _chat_stop_lock:
        _chat_stop_flags.pop(session_id, None)


def stop_chat(session_id: str):
    """外部调用：请求停止指定 session 的对话"""
    with _chat_stop_lock:
        if session_id in _chat_stop_flags:
            _chat_stop_flags[session_id] = False


def is_chat_running(session_id: str) -> bool:
    with _chat_stop_lock:
        return _chat_stop_flags.get(session_id, False)


async def ensure_default_interactive_agent():
    """确保系统默认交互 Agent 进程已启动（引擎启动时调用，幂等）。"""
    if agent_factory.is_running(DEFAULT_INTERACTIVE_AGENT_ID):
        return {"status": "already_running"}
    return await agent_factory.start(
        DEFAULT_INTERACTIVE_AGENT_ID, DEFAULT_INTERACTIVE_IMPL_PATH
    )


async def _summarize_title(session_id: str) -> None:
    """首轮对话结束后用 LLM 总结会话标题。

    会话标题不再直接截取用户第一句话，而是交给 LLM 概括主题。只在首轮
    （用户消息 + Agent 回复两条）结束时执行一次；失败或无内容时保留
    chat_store 里首条消息截断的兜底标题，不影响对话本身。
    """
    from core.observability import chat_store
    try:
        if chat_store.message_count(session_id) != 2:
            return
        msgs = chat_store.get_messages(session_id, limit=4)
        convo = "\n".join(
            f"{'用户' if m['role'] == 'user' else '助手'}: {str(m['content'])[:300]}"
            for m in msgs if m.get("content")
        )
        if not convo:
            return
        from core.llm.client import create_llm_client
        title = await create_llm_client().chat([
            {"role": "system",
             "content": "你是会话标题生成器。根据对话内容生成一个不超过 20 个字的中文标题，"
                        "概括用户想做的事。只输出标题本身，不要引号、标点或解释。"},
            {"role": "user", "content": convo},
        ])
        title = (title or "").strip().strip('"“”\'').splitlines()
        title = title[0].strip() if title else ""
        if title:
            chat_store.set_title(session_id, title[:30])
    except Exception:
        # 标题总结是锦上添花，失败不能影响对话主流程
        pass


@router.post("/send")
async def chat_send(request: ChatRequest):
    """
    发送消息给交互 Agent，返回 SSE 流式响应。

    事件类型:
    - content: 文本增量 {"text": "..."}
    - card: 内联卡片 {"type": "...", "title": "...", "data": {...}}
    - done: 响应结束 {"messageId": "..."}
    - error: 错误 {"code": "...", "message": "..."}
    """

    session_id = request.session_id or "default"

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

    _set_chat_running(session_id)

    from core.observability import task_history, chat_store
    # 对话不按每条用户输入记一条任务历史，而是整个会话聚合成一条
    # （input 为会话主题 = 该会话首条输入），每轮结束累计轮数与耗时。
    task_id = task_history.start_or_touch(
        DEFAULT_INTERACTIVE_AGENT_ID, request.content, session_id=session_id,
        agent_name="交互 Agent")
    task_finished = False
    round_started = time.time()

    def _round(status: str, error: str = "") -> None:
        task_history.accumulate(
            task_id, int((time.time() - round_started) * 1000), status, error)

    # 对话持久化：用户消息即时落库；Agent 回复在流结束时聚合落库
    chat_store.append_message(session_id, DEFAULT_INTERACTIVE_AGENT_ID,
                              "user", request.content)
    reply_parts: list[str] = []
    reply_cards: list[dict] = []

    def _persist_reply() -> None:
        text = "".join(reply_parts)
        if text or reply_cards:
            chat_store.append_message(session_id, DEFAULT_INTERACTIVE_AGENT_ID,
                                      "agent", text, reply_cards)

    async def event_generator():
        nonlocal task_finished
        try:
            async for event in agent_factory.execute_stream(
                DEFAULT_INTERACTIVE_AGENT_ID,
                params={
                    "content": request.content,
                    "attachments": attachments,
                    "workspace_tool_ids": request.workspace_tool_ids,
                },
                context={"session_id": session_id, "user_id": request.user_id or "default"},
                # 不设总超时：工具可能长时间执行（如模型训练），由用户通过
                # 停止按钮主动中断，而非被固定超时提前截断。
                timeout=None,
            ):
                # 每 yield 一次检查是否被停止
                if not is_chat_running(session_id):
                    _round("failed", "用户停止")
                    task_finished = True
                    _persist_reply()
                    yield {
                        "event": "stopped",
                        "data": json.dumps({"message": "对话已停止"}, ensure_ascii=False),
                    }
                    return
                ev_type = event.get("event", "content")
                data = event.get("data", {}) or {}
                if ev_type == "content":
                    reply_parts.append(str(data.get("text", "")))
                elif ev_type == "card":
                    reply_cards.append(data)
                elif ev_type == "done" and not task_finished:
                    _round("success")
                    task_finished = True
                    _persist_reply()
                    # 首轮结束后异步总结会话标题（不阻塞流结束）
                    asyncio.create_task(_summarize_title(session_id))
                elif ev_type == "error" and not task_finished:
                    msg = str(data.get("message", ""))[:200]
                    _round("failed", msg)
                    task_finished = True
                    _persist_reply()
                yield {
                    "event": ev_type,
                    "data": json.dumps(data, ensure_ascii=False),
                }
        finally:
            if not task_finished:
                # 流未正常收到 done/error（客户端断开或异常中断）
                _round("failed", "流中断")
                _persist_reply()
            _set_chat_stopped(session_id)

    return EventSourceResponse(event_generator())


@router.get("/sessions")
async def list_sessions(limit: int = 50):
    """会话列表（按最近更新倒序）。"""
    from core.observability import chat_store
    return {"sessions": chat_store.list_sessions(limit=limit)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, limit: int = 200):
    """会话消息（时间正序）。"""
    from core.observability import chat_store
    return {"messages": chat_store.get_messages(session_id, limit=limit)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其全部消息（前端「清空对话」也走这里）。"""
    from core.observability import chat_store
    chat_store.delete_session(session_id)
    return {"ok": True}


@router.post("/stop")
async def chat_stop(req: dict):
    """停止指定 session 的对话"""
    session_id = req.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    stop_chat(session_id)
    return {"status": "ok", "message": f"已请求停止对话 {session_id}"}


# ────────────────── 平台控制网关端点（docs/chat-platform-control-design.md） ──────────────────
# 对话内的所有平台操作都收敛到这里：身份取当前登录用户，风险分级/参数校验/
# 确认协议/审计全在 core.chat_control 网关内完成。POST 天然受 auth 中间件保护
# （未登录 401），无需在此重复鉴权。

def _platform_owner(user_id: str | None = None) -> str:
    """操作身份 = 当前登录用户；无令牌的老客户端退回会话 user_id。"""
    from core.auth.session import current_identity
    return current_identity() or (user_id or "").strip() or "default"


@router.post("/platform-call")
async def platform_call_endpoint(request: PlatformCallRequest):
    """执行一次平台工具调用（只读直通；写操作返回待确认卡片数据）。"""
    from core.chat_control import gateway
    owner = _platform_owner(request.user_id)
    result = await gateway.platform_call(
        request.tool, request.args, owner=owner, session_id=request.session_id
    )
    return result


@router.post("/platform-call/confirm")
async def platform_confirm_endpoint(request: PlatformConfirmRequest):
    """确认执行待确认操作。校验 owner/token/指纹/超时，幂等返回首次结果。"""
    from core.chat_control import gateway
    owner = _platform_owner()
    return await gateway.confirm_op(
        request.op_id, owner=owner, token=request.token,
        fingerprint=request.fingerprint or None,
    )


@router.post("/platform-call/cancel")
async def platform_cancel_endpoint(request: PlatformConfirmRequest):
    """取消待确认操作。"""
    from core.chat_control import gateway
    return gateway.cancel_op(request.op_id, owner=_platform_owner())


@router.get("/platform-call/pending")
async def platform_pending_endpoint(session_id: str = "default"):
    """查询会话当前的待确认操作（前端刷新后恢复卡片）。"""
    from core.chat_control import gateway
    pending = gateway.pending_op(session_id)
    return {"pending": pending}


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
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.schemas.chat_schemas import ChatRequest
from core.agent.factory import agent_factory
from core.agent.implementations import (
    DEFAULT_INTERACTIVE_AGENT_ID, DEFAULT_INTERACTIVE_IMPL_PATH,
)

# ── 系统默认交互 Agent 的标识 ──
# 它是系统默认入口：引擎启动时自动拉起；用户自定义交互 Agent 则需手动启动。

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
    startup_error = None
    if not agent_factory.is_running(DEFAULT_INTERACTIVE_AGENT_ID):
        result = await ensure_default_interactive_agent()
        if result.get("status") not in {"started", "already_running"}:
            startup_error = result.get("error") or "Agent 启动失败"

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

    async def event_generator():
        try:
            if startup_error:
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "code": "agent_start_failed",
                        "message": f"交互 Agent 启动失败：{startup_error}",
                    }, ensure_ascii=False),
                }
                yield {"event": "done", "data": json.dumps({"messageId": "startup-failed"})}
                return
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
            _set_chat_stopped(session_id)

    return EventSourceResponse(event_generator())


@router.post("/stop")
async def chat_stop(req: dict):
    """停止指定 session 的对话"""
    session_id = req.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    stop_chat(session_id)
    return {"status": "ok", "message": f"已请求停止对话 {session_id}"}

"""对话路由 — SSE 流式响应"""

import json
import asyncio
import threading
from fastapi import APIRouter, Request, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.schemas.chat_schemas import ChatRequest
from core.agent.base import AgentContext
import importlib.util, sys
from pathlib import Path
# 交互Agent 已移到 resources/agents/implementations/
_spec = importlib.util.spec_from_file_location(
    "interactive_agent",
    Path(__file__).resolve().parent.parent.parent.parent / "resources" / "agents" / "implementations" / "interactive_agent" / "agent.py"
)
_ia_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ia_mod)
interactive_agent = _ia_mod.interactive_agent

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
    ctx = AgentContext(
        agent_id="interactive-agent",
        session_id=session_id,
        user_id=request.user_id or "default",
    )

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
            async for event in interactive_agent.execute(
                ctx,
                content=request.content,
                attachments=attachments,
                workspace_tool_ids=request.workspace_tool_ids,
            ):
                # 每 yield 一次检查是否被停止
                if not is_chat_running(session_id):
                    yield {
                        "event": "stopped",
                        "data": json.dumps({"message": "对话已停止"}, ensure_ascii=False),
                    }
                    return
                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"], ensure_ascii=False),
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

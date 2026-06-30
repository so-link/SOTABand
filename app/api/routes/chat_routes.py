"""对话路由 — SSE 流式响应"""

import json
import asyncio
from fastapi import APIRouter, Request
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

    ctx = AgentContext(
        agent_id="interactive-agent",
        session_id=request.session_id or "default",
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

    async def event_generator():
        async for event in interactive_agent.execute(
            ctx,
            content=request.content,
            attachments=attachments,
        ):
            yield {
                "event": event["event"],
                "data": json.dumps(event["data"], ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())

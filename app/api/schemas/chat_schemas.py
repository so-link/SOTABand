"""对话相关的 Pydantic 模型"""

from typing import Any, Optional
from pydantic import BaseModel, Field


class FileAttachment(BaseModel):
    """文件附件"""

    id: str
    fileName: str = Field(alias="fileName")
    filePath: str = ""
    fileSize: int = 0
    format: str = "unknown"

    model_config = {"populate_by_name": True}


class ChatRequest(BaseModel):
    """POST /api/chat/send 请求体"""

    content: str = ""
    attachments: list[FileAttachment] = Field(default_factory=list)
    session_id: str = Field(default="default", alias="sessionId")
    user_id: str = Field(default="default", alias="userId")

    model_config = {"populate_by_name": True}


class SSEEvent(BaseModel):
    """SSE 事件"""

    event: str  # "content" | "card" | "done" | "error"
    data: dict[str, Any]

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
    workspace_tool_ids: list[str] = Field(default_factory=list, alias="workspaceToolIds")
    session_id: str = Field(default="default", alias="sessionId")
    user_id: str = Field(default="default", alias="userId")

    model_config = {"populate_by_name": True}


class SSEEvent(BaseModel):
    """SSE 事件"""

    event: str  # "content" | "card" | "done" | "error"
    data: dict[str, Any]


class PlatformCallRequest(BaseModel):
    """POST /api/chat/platform-call 请求体（子进程/前端统一走这里）"""

    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(default="default", alias="sessionId")
    # 无登录令牌的老客户端兜底身份（有登录态时以登录用户为准）
    user_id: str = Field(default="", alias="userId")

    model_config = {"populate_by_name": True}


class PlatformConfirmRequest(BaseModel):
    """POST /api/chat/platform-call/confirm 请求体

    fingerprint 可选：前端从确认卡原样回传，网关再校验一次"确认内容=执行内容"。
    user_id 仅作无登录令牌时的兜底身份，与 platform-call 端点保持一致。
    """

    op_id: str = Field(alias="opId")
    token: str = ""
    fingerprint: str = ""
    session_id: str = Field(default="default", alias="sessionId")
    user_id: str = Field(default="", alias="userId")

    model_config = {"populate_by_name": True}

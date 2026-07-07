"""Agent 管理相关的 Pydantic 模型"""

from pydantic import BaseModel, Field


class GenerateSpecRequest(BaseModel):
    """自然语言 → MD 规范文档"""

    description: str = ""


class GenerateCodeRequest(BaseModel):
    """MD 规范文档 → Python 代码"""

    spec_md: str = Field(alias="specMd")
    role: str = "task"
    agent_id: str = Field(default="custom-agent", alias="agentId")
    agent_name: str = Field(default="Custom Agent", alias="agentName")

    model_config = {"populate_by_name": True}


class RegisterAgentRequest(BaseModel):
    """注册 Agent"""

    spec_md: str = Field(alias="specMd")
    code: str = ""
    agent_id: str = Field(alias="agentId")
    agent_name: str = Field(default="Custom Agent", alias="agentName")
    role: str = "task"
    version: str = "0.1.0"
    tags: list[str] = Field(default_factory=list)
    demand_desc: str = Field(default="", alias="demandDesc")

    model_config = {"populate_by_name": True}


class AgentExecuteRequest(BaseModel):
    """向 Agent 发送输入"""

    content: str = ""


class AgentStatusResponse(BaseModel):
    """Agent 状态"""

    id: str
    name: str
    role: str
    status: str
    version: str
    health: str = "healthy"
    created_at: str = ""

"""Agent 管理相关的 Pydantic 模型"""

from pydantic import BaseModel, Field


class GenerateSpecRequest(BaseModel):
    """自然语言 → MD 规范文档"""

    description: str = ""
    # Agent 类型（三类体系）：interactive | task | web-service
    # 用于在生成 MD 时注入类型专属的结构约束（如 task 的 flow、web-service 的 apis）
    role: str | None = None


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
    """向 Agent 发送输入。

    三类 Agent 的入口参数不同：
      - interactive：content
      - task：MD「## 输入」声明的启动参数（如 name / city ...）
      - web-service：由 /test-api 走 HTTP，不经此路由

    因此不能只声明 content —— 多余字段必须放行，否则任务式 Agent 的
    启动参数会被静默丢弃，表现为「参数明明填了却报缺少必填参数」。
    """

    content: str = ""

    model_config = {"extra": "allow"}


class AgentStatusResponse(BaseModel):
    """Agent 状态"""

    id: str
    name: str
    role: str
    status: str
    version: str
    health: str = "healthy"
    created_at: str = ""

"""Tool 相关的 Pydantic 模型"""

from pydantic import BaseModel, Field


class GenerateToolSpecRequest(BaseModel):
    """自然语言 → MD 工具描述文档"""
    description: str = ""
    reference_code: str = ""  # 用户提供的参考代码


class GenerateToolCodeRequest(BaseModel):
    """MD → 工具代码 + 测试数据"""
    spec_md: str = Field(alias="specMd")
    tool_id: str = Field(default="custom-tool", alias="toolId")
    tool_name: str = Field(default="Custom Tool", alias="toolName")
    code: str = ""  # 可选：已有代码时跳过 LLM 重新生成

    model_config = {"populate_by_name": True}


class RegisterToolRequest(BaseModel):
    """注册工具"""
    spec_md: str = Field(alias="specMd")
    code: str = ""
    tool_id: str = Field(alias="toolId")
    tool_name: str = Field(default="Custom Tool", alias="toolName")
    # 编辑已发布工具时传入「原工具 id」——注册的权威身份。
    # 未传时退回 tool_id（由 MD 内容推导），即新建工具的旧行为。
    base_tool_id: str = Field(default="", alias="baseToolId")
    # 留空表示由服务端决定：更新时 patch+1，新建时 0.1.0
    version: str = ""
    tags: list[str] = Field(default_factory=list)
    test_data: dict = Field(default_factory=dict)
    demand_desc: str = Field(default="", alias="demandDesc")
    reference_code: str = Field(default="", alias="referenceCode")

    model_config = {"populate_by_name": True}


class ModifyCodeRequest(BaseModel):
    """AI 辅助修改代码"""
    current_code: str = Field(default="", alias="currentCode")
    request: str = ""  # 用户的自然语言修改描述

    model_config = {"populate_by_name": True}


class ModifyAndDebugRequest(BaseModel):
    """AI 辅助修改 + 自动调试"""
    current_code: str = Field(default="", alias="current_code")
    request: str = ""  # 用户修改描述
    spec_md: str = Field(default="", alias="spec_md")
    tool_id: str = Field(default="", alias="tool_id")
    test_params: dict = Field(default_factory=dict, alias="test_params")
    modify_history: list[str] = Field(default_factory=list, alias="modify_history")

    model_config = {"populate_by_name": True}


class ExecuteToolRequest(BaseModel):
    """调用工具"""
    params: dict = Field(default_factory=dict)

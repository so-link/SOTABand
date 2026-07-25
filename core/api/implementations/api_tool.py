"""工具相关 API 实现"""
from core.resource.registry.tool_registry import ToolRegistry


class ApiToolRegister:
    """api-tool-register: 注册工具"""
    registry = ToolRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        resource = {
            "id": kwargs.get("id", ""),
            "name": kwargs.get("name", ""),
            "raw_md": kwargs.get("raw_md", ""),
            "code": kwargs.get("code", ""),
            "tags": kwargs.get("tags", []),
        }
        tool_id = await ApiToolRegister.registry.register(resource)
        return {"tool_id": tool_id}


class ApiToolList:
    """api-tool-list: 列出所有工具"""
    registry = ToolRegistry()

    @staticmethod
    async def call(**kwargs) -> dict:
        tools = await ApiToolList.registry.list_all()
        return {"tools": tools}

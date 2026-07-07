"""API 管理路由 — 列出系统已注册的 API（供 Agent/Tool 编辑器补全）"""

from fastapi import APIRouter
from core.api.registry import ApiRegistry

router = APIRouter()


@router.get("/list")
async def list_apis():
    """列出所有已注册的系统 API（id, name, category, tags 等）"""
    registry = ApiRegistry()
    apis = await registry.list_all()
    return {"apis": apis}

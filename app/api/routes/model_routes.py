"""模型空间 API 路由"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.resource.registry.model_registry import ModelRegistry

router = APIRouter(tags=["model"])
registry = ModelRegistry()


class RegisterModelRequest(BaseModel):
    id: str | None = None
    name: str
    version: str = "0.1.0"
    framework: str = ""
    model_path: str = ""
    input_format: str = ""
    output_format: str = ""
    tags: list[str] = []
    associated_tool_id: str = ""
    raw_md: str = ""


@router.get("/list")
async def list_models():
    """列出所有已注册模型"""
    models = await registry.list_all()
    return {"models": models}


@router.get("/{model_id}")
async def get_model(model_id: str):
    """获取模型详情"""
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    return {"model": model}


@router.post("/register")
async def register_model(req: RegisterModelRequest):
    """注册一个新模型"""
    if not req.name.strip():
        raise HTTPException(400, "模型名称不能为空")
    resource = req.model_dump()
    model_id = await registry.register(resource)
    entry = await registry.get(model_id)
    return {"model_id": model_id, "entry": entry}


@router.delete("/{model_id}")
async def delete_model(model_id: str):
    """删除模型"""
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    await registry.unregister(model_id)
    return {"message": f"Model '{model_id}' deleted"}

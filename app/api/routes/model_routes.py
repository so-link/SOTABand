"""模型空间 API 路由"""

from fastapi import APIRouter, HTTPException

from core.resource.registry.model_registry import ModelRegistry

router = APIRouter(tags=["model"])
registry = ModelRegistry()


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


@router.delete("/{model_id}")
async def delete_model(model_id: str):
    """删除模型"""
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    await registry.unregister(model_id)
    return {"message": f"Model '{model_id}' deleted"}

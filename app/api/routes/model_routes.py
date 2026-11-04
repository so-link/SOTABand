"""模型空间 API 路由"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.resource.registry.model_registry import ModelRegistry

router = APIRouter(tags=["model"])
registry = ModelRegistry()


class ModelResourceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str = ""
    name: str
    version: str = "0.1.0"
    framework: str = "PyTorch"
    model_path: str = Field(default="", alias="modelPath")
    input_format: str = Field(default="timeseries", alias="inputFormat")
    output_format: str = Field(default="binary-classification", alias="outputFormat")
    tags: list[str] = Field(default_factory=list)
    raw_md: str = Field(default="", alias="rawMd")
    metadata: dict[str, Any] = Field(default_factory=dict)
    group_path: str = Field(default="", alias="groupPath")


@router.get("/list")
async def list_models():
    """列出所有已注册模型"""
    models = await registry.list_all()
    return {"models": models}


@router.get("/groups")
async def list_model_groups():
    return {"groups": await registry.list_groups()}


class ModelGroupRequest(BaseModel):
    name: str
    category: str = "architecture"


@router.post("/groups")
async def create_model_group(req: ModelGroupRequest):
    try:
        group = await registry.create_group(req.name, req.category)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"group": group}


@router.delete("/groups/{group_id}")
async def delete_model_group(group_id: str):
    try:
        await registry.delete_group(group_id)
    except KeyError as exc:
        raise HTTPException(404, "模型分组不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"message": "模型分组已删除"}


@router.post("")
async def create_model(req: ModelResourceRequest):
    """Create a model resource while keeping artifacts at their source path."""
    resource = req.model_dump(by_alias=False)
    metadata = resource.pop("metadata", {})
    resource.update(metadata)
    if not resource.get("id"):
        resource.pop("id", None)
    model_id = await registry.register(resource)
    return {"model_id": model_id, "model": await registry.get(model_id)}


@router.patch("/{model_id}")
async def update_model(model_id: str, updates: dict[str, Any]):
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    if "group_path" in updates:
        destination = {**model, **updates}
        category = registry.model_category(destination)
        groups = await registry.list_groups()
        if not any(item["category"] == category and item["name"] == updates["group_path"] for item in groups):
            raise HTTPException(400, "目标类别中不存在该模型分组")
    protected = {"id", "type", "created_at", "model_category", "artifact_available"}
    await registry.update(model_id, {k: v for k, v in updates.items() if k not in protected})
    return {"model": await registry.get(model_id)}


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

"""模型空间 API 路由

## 权限口径（D2，2026-11-04 拍板）

- **读/用**：登录即可（模型是共享资产，与时序工作流/工具链共用）；
- **写/删**：资源 owner 或租户 Owner/Admin；
- **内置/无 owner 模型**：仅租户 Owner/Admin 可写删；
- **model_path**：必须落在文件允许根内（``core.file_guard``），否则 400。

认证基线由 ``app/main.py`` 的 include_router 统一挂载（未登录 401）。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_auth_context
from core.auth.context import AuthContext
from core.file_guard import PathAccessError, resolve_checked_path
from core.resource.registry.model_registry import ModelRegistry
from core.user_context import resolve_owner_id

router = APIRouter(tags=["model"])
registry = ModelRegistry()

_PRIVILEGED_TENANT_ROLES = ("owner", "admin")


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


def _can_write(ctx: AuthContext, entry: dict | None) -> bool:
    """是否可以写/删该模型（D2 口径）。"""
    if ctx is None:
        return False
    owner = str((entry or {}).get("owner") or "")
    if owner:
        return owner == str(ctx.user_id)
    return getattr(ctx, "tenant_role", None) in _PRIVILEGED_TENANT_ROLES


def _require_can_write(ctx: AuthContext, entry: dict | None) -> None:
    if not _can_write(ctx, entry):
        # 404 而不是 403：不确认"存在但不属于你"
        raise HTTPException(404, "模型不存在")


def _validate_model_path(raw: str, owner_id: str | None = None) -> str:
    """model_path 必须位于文件允许根内，且不得指向他人上传目录 / 敏感路径。

    必须传 ``owner_id``（v3 评审）：不传时上传目录的归属规则不生效，任何登录
    用户都能把他人上传目录里的 artifact 登记成自己的模型路径。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return str(
            resolve_checked_path(text, must_exist=False, owner_id=owner_id)
        )
    except PathAccessError as exc:
        raise HTTPException(400, f"model_path 不合法：{exc}") from exc


def _with_permissions(entry: dict, ctx: AuthContext) -> dict:
    entry = dict(entry)
    entry["is_owner"] = bool(entry.get("owner")) and str(entry.get("owner")) == str(ctx.user_id)
    entry["can_edit"] = _can_write(ctx, entry)
    return entry


@router.get("/list")
async def list_models(ctx: AuthContext = Depends(get_auth_context)):
    """列出所有已注册模型（共享读，附带当前用户的可编辑标记）。"""
    models = await registry.list_all()
    return {"models": [_with_permissions(m, ctx) for m in models]}


@router.get("/groups")
async def list_model_groups():
    return {"groups": await registry.list_groups()}


class ModelGroupRequest(BaseModel):
    name: str
    category: str = "architecture"


@router.post("/groups")
async def create_model_group(
    req: ModelGroupRequest, ctx: AuthContext = Depends(get_auth_context)
):
    if getattr(ctx, "tenant_role", None) not in _PRIVILEGED_TENANT_ROLES:
        raise HTTPException(403, "只有租户所有者/管理员可以维护模型分组")
    try:
        group = await registry.create_group(req.name, req.category)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"group": group}


@router.delete("/groups/{group_id}")
async def delete_model_group(
    group_id: str, ctx: AuthContext = Depends(get_auth_context)
):
    if getattr(ctx, "tenant_role", None) not in _PRIVILEGED_TENANT_ROLES:
        raise HTTPException(403, "只有租户所有者/管理员可以维护模型分组")
    try:
        await registry.delete_group(group_id)
    except KeyError as exc:
        raise HTTPException(404, "模型分组不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"message": "模型分组已删除"}


@router.post("")
async def create_model(
    req: ModelResourceRequest, ctx: AuthContext = Depends(get_auth_context)
):
    """Create a model resource while keeping artifacts at their source path."""
    resource = req.model_dump(by_alias=False)
    metadata = resource.pop("metadata", {})
    resource.update(metadata)
    if not resource.get("id"):
        resource.pop("id", None)
    if resource.get("model_path"):
        resource["model_path"] = _validate_model_path(
            resource["model_path"], owner_id=resolve_owner_id(ctx)
        )
    existing_id = str(resource.get("id") or "").strip()
    if existing_id:
        existing = await registry.get(existing_id)
        if existing is not None:
            _require_can_write(ctx, existing)
    model_id = await registry.register(resource, owner=resolve_owner_id(ctx))
    return {"model_id": model_id, "model": await registry.get(model_id)}


@router.patch("/{model_id}")
async def update_model(
    model_id: str,
    updates: dict[str, Any],
    ctx: AuthContext = Depends(get_auth_context),
):
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    _require_can_write(ctx, model)
    if "group_path" in updates:
        destination = {**model, **updates}
        category = registry.model_category(destination)
        groups = await registry.list_groups()
        if not any(item["category"] == category and item["name"] == updates["group_path"] for item in groups):
            raise HTTPException(400, "目标类别中不存在该模型分组")
    if updates.get("model_path"):
        updates = {
            **updates,
            "model_path": _validate_model_path(
                updates["model_path"], owner_id=resolve_owner_id(ctx)
            ),
        }
    protected = {"id", "type", "created_at", "model_category", "artifact_available", "owner"}
    await registry.update(model_id, {k: v for k, v in updates.items() if k not in protected})
    updated = await registry.get(model_id)
    return {"model": _with_permissions(updated, ctx) if updated else updated}


@router.get("/{model_id}")
async def get_model(model_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """获取模型详情"""
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    return {"model": _with_permissions(model, ctx)}


@router.delete("/{model_id}")
async def delete_model(model_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """删除模型（owner 或租户管理员）"""
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    _require_can_write(ctx, model)
    await registry.unregister(model_id)
    return {"message": f"Model '{model_id}' deleted"}

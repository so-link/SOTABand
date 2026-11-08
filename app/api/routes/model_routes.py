"""模型空间 API 路由"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.resource.registry.model_registry import ModelRegistry
from core.resource import paths

router = APIRouter(tags=["model"])
registry = ModelRegistry()


class RegisterModelRequest(BaseModel):
    """注册模型（补齐 REST 缺口，与对话内 register_model 共用 ApiModelRegister）"""

    name: str
    description: str = ""
    model_path: str = ""
    framework: str = ""
    version: str = "0.1.0"
    tags: list[str] = Field(default_factory=list)


@router.get("/list")
async def list_models():
    """列出所有已注册模型"""
    models = await registry.list_all()
    return {"models": models}


@router.post("/register")
async def register_model(req: RegisterModelRequest):
    """注册模型 — 名称自动追加时间戳后缀；tags 留空则由系统生成"""
    from core.api.implementations.api_model import ApiModelRegister

    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name 不能为空")
    lines = [f"# {name}", "", req.description.strip() or "（手工注册的模型）", ""]
    if req.framework.strip():
        lines.append(f"- **框架**: {req.framework.strip()}")
    if req.model_path.strip():
        lines.append(f"- **模型路径**: `{req.model_path.strip()}`")
    lines.append(f"- **版本**: {req.version.strip() or '0.1.0'}")
    return await ApiModelRegister.call(
        name=name, raw_md="\n".join(lines) + "\n",
        framework=req.framework.strip(), model_path=req.model_path.strip(),
        version=req.version.strip() or "0.1.0", tags=req.tags,
    )


def _read_model_spec(model_id: str) -> str:
    """读取模型规范 MD：resource_roots() 已私有优先（覆盖层语义）。"""
    for root in paths.resource_roots():
        f = root / "models" / "definitions" / f"{model_id}.md"
        if f.exists():
            try:
                return f.read_text(encoding="utf-8")
            except OSError:
                return ""
    return ""


@router.get("/{model_id}")
async def get_model(model_id: str):
    """获取模型详情（含 MD 规范内容）"""
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    return {"model": {**model, "spec_md": _read_model_spec(model_id)}}


@router.post("/purge-missing")
async def purge_missing_models():
    """清理模型文件已不存在的僵尸注册条目（历史条目多指向他人机器的绝对路径）。

    与内置模型删除同属全局操作，仅超级用户可执行。
    """
    from app.api.deps import current_role

    if current_role() != "admin":
        raise HTTPException(403, "仅超级用户可批量清理模型")
    removed = await registry.purge_missing()
    return {"removed": removed, "count": len(removed)}


@router.delete("/{model_id}")
async def delete_model(model_id: str):
    """删除模型（注册条目 + 规范文档 + 产物文件；内置模型写全局删除掩码）"""
    from app.api.deps import ensure_can_touch_builtin

    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    # 内置模型删除对全员生效（共享层掩码），普通用户只能删自己的私有模型
    private_ids = {e.get("id") for e in registry._read_private()}
    ensure_can_touch_builtin("private" if model_id in private_ids else None, action="删除")
    await registry.unregister(model_id)
    return {"message": f"Model '{model_id}' deleted"}

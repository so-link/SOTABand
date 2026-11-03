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


@router.delete("/{model_id}")
async def delete_model(model_id: str):
    """删除模型"""
    model = await registry.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    await registry.unregister(model_id)
    return {"message": f"Model '{model_id}' deleted"}

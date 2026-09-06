"""LLM 配置 API 路由

提供大模型配置的读取与更新：
- GET  /api/config/llm  — 获取当前 LLM 配置（api_key 脱敏）
- POST /api/config/llm  — 更新 LLM 配置，写入 .env 并动态刷新内存配置

支持的模型类型（下拉框选项）由 SUPPORTED_MODELS 定义。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import settings

router = APIRouter(tags=["config"])

# 项目根目录 .env 文件
ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"

# 目前支持的大模型类型（后续可扩展）
SUPPORTED_MODELS = [
    {"value": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "provider": "deepseek"},
]

# 默认 base_url（按 provider 区分，当前仅 deepseek）
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


class LLMConfigUpdate(BaseModel):
    """更新 LLM 配置的请求体"""

    model: str
    api_key: str


def _mask_key(key: str) -> str:
    """脱敏 api_key，仅保留前后若干字符"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _read_env() -> dict[str, str]:
    """读取 .env 文件为字典（保留原始顺序无关紧要，仅作更新用）"""
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _write_env(env: dict[str, str]) -> None:
    """将字典写回 .env 文件（保留注释与空行）"""
    lines: list[str] = []
    if ENV_FILE.exists():
        raw = ENV_FILE.read_text(encoding="utf-8").splitlines()
    else:
        raw = []

    updated = {"DEEPSEEK_API_KEY": False, "DEEPSEEK_MODEL": False}
    for line in raw:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in env:
            lines.append(f"{key}={env[key]}")
            if key in updated:
                updated[key] = True
        else:
            lines.append(line)

    # 追加缺失的键
    for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"):
        if not updated[key] and key in env:
            lines.append(f"{key}={env[key]}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _refresh_settings(model: str, api_key: str) -> None:
    """动态刷新内存中的 LLM 配置，使运行中的进程立即使用新配置"""
    settings.llm.model = model
    settings.llm.api_key = api_key
    import os

    os.environ["DEEPSEEK_MODEL"] = model
    os.environ["DEEPSEEK_API_KEY"] = api_key


@router.get("/llm")
async def get_llm_config():
    """获取当前 LLM 配置（api_key 脱敏）"""
    return {
        "model": settings.llm.model,
        "api_key": _mask_key(settings.llm.api_key),
        "has_api_key": bool(settings.llm.api_key),
        "provider": settings.llm.provider,
        "base_url": settings.llm.base_url,
        "supported_models": SUPPORTED_MODELS,
    }


@router.post("/llm")
async def update_llm_config(payload: LLMConfigUpdate):
    """更新 LLM 配置，写入 .env 并动态刷新"""
    model = payload.model.strip()
    api_key = payload.api_key.strip()

    if not model:
        raise HTTPException(400, "模型类型不能为空")

    # 校验模型是否在支持列表内
    if model not in {m["value"] for m in SUPPORTED_MODELS}:
        raise HTTPException(400, f"不支持的模型类型: {model}")

    # 更新 .env 文件
    env = _read_env()
    env["DEEPSEEK_MODEL"] = model
    env["DEEPSEEK_API_KEY"] = api_key
    _write_env(env)

    # 动态刷新内存配置，使当前进程立即生效
    _refresh_settings(model, api_key)

    return {
        "message": "配置已更新",
        "model": model,
        "api_key": _mask_key(api_key),
        "has_api_key": bool(api_key),
    }

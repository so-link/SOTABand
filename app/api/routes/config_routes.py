"""LLM 配置 API 路由

提供大模型配置的读取与更新（SettingsView 设置页的后端）：
- GET  /api/config/llm  — 获取当前 LLM 配置（api_key 脱敏）
- POST /api/config/llm  — 更新 LLM 配置，写入 .env 并动态刷新内存配置

下拉选项由 config.settings.PROVIDER_PRESETS 自动生成（value 格式
`<provider>:<默认模型>`），覆盖全部已登记服务商；保存时写入
LLM_PROVIDER / LLM_API_KEY / LLM_MODEL 三件套（主 key 体系，见条目 32）。
"""

from pathlib import Path
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import LLMConfig, PROVIDER_PRESETS, settings
from core.llm.client import get_llm_concurrency, set_llm_concurrency

router = APIRouter(tags=["config"])

# 项目根目录 .env 文件
ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"

# 下拉选项随 PROVIDER_PRESETS 自动扩展（新增服务商无需改本文件）
SUPPORTED_MODELS = [
    {
        "value": f"{pid}:{preset['default_model']}",
        "label": f"{preset['name']} · {preset['default_model']}",
        "provider": pid,
    }
    for pid, preset in PROVIDER_PRESETS.items()
]
_MODEL_LOOKUP = {m["value"]: m for m in SUPPORTED_MODELS}


class LLMConfigUpdate(BaseModel):
    """更新 LLM 配置的请求体"""

    model: str   # 下拉选项的 value（<provider>:<默认模型>）
    api_key: str = ""  # 为空表示保留现有 key，不更新
    timeout: int | None = None            # LLM 请求超时（秒）
    max_concurrency: int | None = None    # LLM 并发上限
    debug_exec_timeout: float | None = None  # 调试单轮执行超时（秒）
    debug_max_rounds: int | None = None      # 调试最大轮数


def _mask_key(key: str) -> str:
    """脱敏 api_key，仅保留前后若干字符"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _env_float(name: str, default: float) -> float:
    """读取浮点型环境变量，非法值回退默认"""
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，非法值回退默认"""
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


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

    seen: set[str] = set()
    for line in raw:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in env:
            lines.append(f"{key}={env[key]}")
            seen.add(key)
        else:
            lines.append(line)

    # 追加缺失的键（env 里新增的配置项一并写入）
    for key, val in env.items():
        if key not in seen:
            lines.append(f"{key}={val}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _refresh_settings(
    provider: str, model: str, api_key: str, custom_base_url: str = ""
) -> None:
    """动态刷新内存中的 LLM 配置，使运行中的进程立即使用新配置"""
    settings.llm.provider = provider
    settings.llm.model = model
    settings.llm.api_key = api_key
    # 端点按预设/Key 前缀重新解析（条目 31 的端点自适应）；
    # 自定义服务商（不在预置列表，如自建网关）沿用 .env 的 LLM_BASE_URL
    if provider not in PROVIDER_PRESETS:
        settings.llm.base_url = custom_base_url or settings.llm.base_url
    else:
        settings.llm.base_url = LLMConfig(provider=provider).base_url

    os.environ["LLM_PROVIDER"] = provider
    os.environ["LLM_API_KEY"] = api_key
    os.environ["LLM_MODEL"] = model


@router.get("/llm")
async def get_llm_config():
    """获取当前 LLM 配置（api_key 脱敏）

    注意：model 返回 `<provider>:<模型>` 复合值，须与 supported_models 的
    value 格式一致，否则 SettingsView 的下拉框回显为空（该值原样回传给 POST）。
    """
    provider = settings.llm.provider or ""
    model = settings.llm.model or ""
    model_value = f"{provider}:{model}" if provider and model else model

    options = list(SUPPORTED_MODELS)
    # 当前模型不是各家默认模型时（如 .env 里手工指定了 mimo-v2.5-pro），
    # 补一个"当前"选项，避免下拉框无匹配项、也避免保存时被判为不支持
    if model_value and model_value not in _MODEL_LOOKUP:
        preset_name = PROVIDER_PRESETS.get(provider, {}).get("name", provider)
        options.insert(0, {
            "value": model_value,
            "label": f"{preset_name} · {model}（当前）",
            "provider": provider,
        })

    return {
        "model": model_value,
        "model_name": model,
        "api_key": _mask_key(settings.llm.api_key),
        "has_api_key": bool(settings.llm.api_key),
        "provider": provider,
        "base_url": settings.llm.base_url,
        "supported_models": options,
        "timeout": settings.llm.timeout,
        "max_concurrency": get_llm_concurrency(),
        "debug_exec_timeout": _env_float("DEBUG_EXEC_TIMEOUT", 60.0),
        "debug_max_rounds": _env_int("DEBUG_MAX_ROUNDS", 10),
    }


@router.post("/llm")
async def update_llm_config(payload: LLMConfigUpdate):
    """更新 LLM 配置（设定主 key），写入 .env 并动态刷新"""
    model = payload.model.strip()
    api_key = payload.api_key.strip()

    if not model:
        raise HTTPException(400, "模型类型不能为空")

    # 兼容两种 value：<provider>:<模型>（含"当前"项）与纯模型名（旧格式）
    if ":" in model:
        provider, model_name = model.split(":", 1)
    else:
        entry = _MODEL_LOOKUP.get(model)
        if not entry:
            raise HTTPException(400, f"不支持的模型类型: {model}")
        provider, model_name = entry["provider"], model

    # 更新 .env 文件（主 key 三件套）
    env = _read_env()
    # 自定义服务商（自建网关 / 聚合平台）不在预置列表也允许，
    # 但必须已有端点（LLM_BASE_URL），否则保存后无法调用
    if provider not in PROVIDER_PRESETS and not env.get("LLM_BASE_URL", ""):
        raise HTTPException(
            400, f"自定义服务商 {provider} 需要在 .env 中配置 LLM_BASE_URL"
        )
    env["LLM_PROVIDER"] = provider
    env["LLM_MODEL"] = model_name
    # api_key 为空时，保留原有 key（不覆盖）
    if api_key:
        env["LLM_API_KEY"] = api_key
    # 可选运行时参数：超时 / 并发 / 调试执行超时 / 调试最大轮数
    if payload.timeout is not None:
        env["LLM_TIMEOUT"] = str(max(30, min(7200, int(payload.timeout))))
    if payload.max_concurrency is not None:
        env["LLM_MAX_CONCURRENCY"] = str(max(1, min(16, int(payload.max_concurrency))))
    if payload.debug_exec_timeout is not None:
        env["DEBUG_EXEC_TIMEOUT"] = str(max(5, min(1800, int(payload.debug_exec_timeout))))
    if payload.debug_max_rounds is not None:
        env["DEBUG_MAX_ROUNDS"] = str(max(1, min(100, int(payload.debug_max_rounds))))
    _write_env(env)

    # 动态刷新内存配置，使当前进程立即生效
    final_key = env.get("LLM_API_KEY", "")
    _refresh_settings(provider, model_name, final_key, env.get("LLM_BASE_URL", ""))

    if "LLM_TIMEOUT" in env:
        settings.llm.timeout = int(env["LLM_TIMEOUT"])
        os.environ["LLM_TIMEOUT"] = env["LLM_TIMEOUT"]
    if "LLM_MAX_CONCURRENCY" in env:
        set_llm_concurrency(int(env["LLM_MAX_CONCURRENCY"]))
        os.environ["LLM_MAX_CONCURRENCY"] = env["LLM_MAX_CONCURRENCY"]
    if "DEBUG_EXEC_TIMEOUT" in env:
        os.environ["DEBUG_EXEC_TIMEOUT"] = env["DEBUG_EXEC_TIMEOUT"]
    if "DEBUG_MAX_ROUNDS" in env:
        os.environ["DEBUG_MAX_ROUNDS"] = env["DEBUG_MAX_ROUNDS"]

    return {
        "message": "配置已更新",
        "model": model_name,
        "provider": provider,
        "api_key": _mask_key(final_key),
        "has_api_key": bool(final_key),
        "timeout": settings.llm.timeout,
        "max_concurrency": get_llm_concurrency(),
        "debug_exec_timeout": _env_float("DEBUG_EXEC_TIMEOUT", 60.0),
        "debug_max_rounds": _env_int("DEBUG_MAX_ROUNDS", 10),
    }

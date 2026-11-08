"""LLM 配置 API 路由

提供大模型配置的读取与更新（SettingsView 设置页的后端）：
- GET  /api/config/llm  — 获取当前 LLM 配置（api_key 脱敏）
- POST /api/config/llm  — 更新 LLM 配置，写入 .env 并动态刷新内存配置

下拉选项由 config.settings.PROVIDER_PRESETS 自动生成（value 格式
`<provider>:<默认模型>`），覆盖全部已登记服务商；保存时写入
LLM_PROVIDER / LLM_API_KEY / LLM_MODEL 三件套（主 key 体系，见条目 32）。
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context
from config.settings import LLMConfig, PROVIDER_PRESETS, settings
from core.auth.audit import add_audit_event
from core.auth.context import AuthContext
from core.auth.platform_admin import is_platform_admin
from core.db.models import RESULT_SUCCESS
from core.db.session import get_db_session

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

# ── 写入 .env 的值格式白名单（v3 评审：防换行注入任意配置行）──
# 攻击面：_write_env 直接 f"{k}={v}" 落盘，api_key/model 若允许换行，就能
# 注入 `EVIL=...` 任意行（下次重启生效）。这里按字符集白名单收口。
_API_KEY_RE = re.compile(r"^[A-Za-z0-9._\-]{8,300}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:\-/]{1,200}$")
# .env 的 key 只允许字母/数字/下划线（不能含 = 或换行）；值禁止控制字符（含 \r \n NUL）
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_VALUE_FORBIDDEN_RE = re.compile(r"[\x00-\x1f\x7f]")


def _check_env_value(key: str, value: str) -> None:
    """写 .env 前的兜底校验：key 合法、值不含控制字符（防注入新行）。"""
    if not _ENV_KEY_RE.match(str(key or "")):
        raise HTTPException(400, f".env 键名非法: {key!r}")
    if _ENV_VALUE_FORBIDDEN_RE.search(str(value or "")):
        raise HTTPException(400, f"{key} 的值含非法控制字符（可能注入 .env 行）")


class LLMConfigUpdate(BaseModel):
    """更新 LLM 配置的请求体"""

    model: str   # 下拉选项的 value（<provider>:<默认模型>）
    api_key: str = ""  # 为空表示保留现有 key，不更新


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
    """将字典写回 .env 文件（保留注释与空行）。

    写前统一校验 key/值：值里出现换行等控制字符时拒绝，避免 `api_key` 里的
    换行把后续内容变成独立的配置行（v3 评审）。
    """
    for key, value in env.items():
        _check_env_value(key, value)

    lines: list[str] = []
    if ENV_FILE.exists():
        raw = ENV_FILE.read_text(encoding="utf-8").splitlines()
    else:
        raw = []

    # 主 key 三件套：与 config.settings 的新体系（条目 32）对齐。
    # 旧版曾写 DEEPSEEK_MODEL/DEEPSEEK_API_KEY，新体系不读，重启即丢配置。
    target_keys = ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL")
    updated = {k: False for k in target_keys}
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
    for key in target_keys:
        if not updated[key] and key in env:
            lines.append(f"{key}={env[key]}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _refresh_settings(provider: str, model: str, api_key: str) -> None:
    """动态刷新内存中的 LLM 配置，使运行中的进程立即使用新配置"""
    settings.llm.provider = provider
    settings.llm.model = model
    settings.llm.api_key = api_key
    # 端点按预设/Key 前缀重新解析（条目 31 的端点自适应）
    settings.llm.base_url = LLMConfig(provider=provider).base_url

    import os

    os.environ["LLM_PROVIDER"] = provider
    os.environ["LLM_API_KEY"] = api_key
    os.environ["LLM_MODEL"] = model


@router.get("/llm")
async def get_llm_config(ctx: AuthContext = Depends(get_auth_context)):
    """获取当前 LLM 配置（api_key 脱敏）。

    v3 评审：与 POST 口径统一为**平台管理员**。该接口会暴露全局 LLM 端点、
    模型名与掩码后的 key（掩码本身也是凭据指纹），不该向普通租户成员开放。

    注意：model 返回 `<provider>:<模型>` 复合值，须与 supported_models 的
    value 格式一致，否则 SettingsView 的下拉框回显为空（该值原样回传给 POST）。
    """
    if not is_platform_admin(ctx):
        raise HTTPException(403, "只有平台管理员可以读取全局 LLM 配置")
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
    }


@router.post("/llm")
async def update_llm_config(
    payload: LLMConfigUpdate,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
):
    """更新 LLM 配置（设定主 key），写入 .env 并动态刷新。

    ⚠️ 这是**全租户共享**的全局配置（劫持它等于把所有人的对话路由到攻击者端点），
    因此仅**平台管理员**可改，并写审计。
    """
    if not is_platform_admin(ctx):
        raise HTTPException(403, "只有平台管理员可以修改全局 LLM 配置")

    model = payload.model.strip()
    api_key = payload.api_key.strip()

    if not model:
        raise HTTPException(400, "模型类型不能为空")

    # 字符集白名单：值会被原样写进 .env，绝不允许换行等可注入新配置行的字符
    if not _MODEL_RE.match(model):
        raise HTTPException(400, "模型类型含非法字符（只允许字母/数字/. _ : - /）")
    if api_key and not _API_KEY_RE.match(api_key):
        raise HTTPException(
            400, "api_key 格式不合法（8-300 位字母/数字/. _ -，且不得含空白与换行）"
        )

    # 兼容两种 value：<provider>:<模型>（含"当前"项）与纯模型名（旧格式）
    if ":" in model:
        provider, model_name = model.split(":", 1)
    else:
        entry = _MODEL_LOOKUP.get(model)
        if not entry:
            raise HTTPException(400, f"不支持的模型类型: {model}")
        provider, model_name = entry["provider"], model

    if provider not in PROVIDER_PRESETS:
        raise HTTPException(400, f"不支持的服务商: {provider}")

    # 更新 .env 文件（主 key 三件套）
    env = _read_env()
    env["LLM_PROVIDER"] = provider
    env["LLM_MODEL"] = model_name
    # api_key 为空时，保留原有 key（不覆盖）
    if api_key:
        env["LLM_API_KEY"] = api_key
    _write_env(env)

    # 动态刷新内存配置，使当前进程立即生效
    final_key = env.get("LLM_API_KEY", "")
    _refresh_settings(provider, model_name, final_key)

    import uuid as _uuid

    add_audit_event(
        session,
        action="config.llm.change",
        result=RESULT_SUCCESS,
        request_id=ctx.request_id,
        actor_id=ctx.user_id,
        tenant_id=_uuid.UUID(ctx.tenant_id),
        target_type="llm_config",
        target_id=provider,
    )
    await session.commit()

    return {
        "message": "配置已更新",
        "model": model_name,
        "provider": provider,
        "api_key": _mask_key(final_key),
        "has_api_key": bool(final_key),
    }

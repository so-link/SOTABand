"""LLM 配置 API 路由

提供大模型配置的读取与更新（SettingsView 设置页的后端）：
- GET  /api/config/llm  — 获取当前 LLM 配置（api_key 脱敏）
- POST /api/config/llm  — 更新 LLM 配置，写入 .env 并动态刷新内存配置

下拉选项由 config.settings.PROVIDER_PRESETS 自动生成（value 格式
`<provider>:<默认模型>`），覆盖全部已登记服务商；保存时写入
LLM_PROVIDER / LLM_API_KEY / LLM_MODEL 三件套（主 key 体系，见条目 32）。

除「对话模型」外还可单独配置「生成/推理增强模型」（响应里的 gen 字段）：
它只服务于工具/Agent 的规格与代码生成链，允许使用不支持 function calling
但写代码更强的模型；写入 LLM_GEN_PROVIDER / LLM_GEN_MODEL / LLM_GEN_API_KEY，
未配置时生成链自动回退到对话模型。
"""

from pathlib import Path
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import (
    LLMConfig,
    PROVIDER_PRESETS,
    get_debug_exec_timeout,
    get_debug_max_rounds,
    resolve_gen_llm_config,
    settings,
)
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
    max_concurrency: int | None = None    # LLM 并发上限（chat 池：对话/工具调用）
    gen_concurrency: int | None = None    # 生成链并发上限（gen 池：规格/代码/图片）
    aux_concurrency: int | None = None    # 后台短任务并发上限（aux 池：标题/标签/匹配）
    debug_exec_timeout: float | None = None  # 调试单轮执行超时（秒）
    debug_max_rounds: int | None = None      # 调试最大轮数
    # Agent 轮次防卡死（None = 本次不改动）
    agent_round_timeout: int | None = None   # 对话单轮硬超时（秒），0=不限制
    agent_idle_timeout: int | None = None    # 空闲心跳探活间隔（秒），0=不检查
    agent_cancel_grace: int | None = None    # 温和取消等待窗口（秒）
    # 对话内平台控制（None = 本次不改动）
    rate_limit_per_minute: int | None = None    # 每用户每分钟 platform 调用上限
    gen_limit_per_10min: int | None = None      # 生成类调用每 10 分钟上限
    platform_control_enabled: bool | None = None  # 平台控制熔断总开关
    # 「生成/推理增强模型」（工具与 Agent 的规格/代码生成链专用，可选）：
    # None = 本次不改动；"" = 关闭并回退全局对话模型；
    # 其他 = <provider>:<模型> 复合值
    gen_model: str | None = None
    gen_api_key: str | None = None  # 为空字符串表示保留现有 gen key


def _split_model_value(value: str) -> tuple[str, str]:
    """拆解下拉值 `<provider>:<model>`；兼容纯模型名（取预设默认 provider）"""
    value = (value or "").strip()
    if ":" in value:
        provider, model_name = value.split(":", 1)
        return provider.strip(), model_name.strip()
    entry = _MODEL_LOOKUP.get(value)
    if not entry:
        raise HTTPException(400, f"不支持的模型类型: {value}")
    return entry["provider"], value


def _mask_key(key: str) -> str:
    """脱敏 api_key，仅保留前后若干字符"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _gen_snapshot() -> dict:
    """生成模型当前配置（api_key 脱敏）。未配置 provider 时 follow_global=True。"""
    if not os.getenv("LLM_GEN_PROVIDER", "").strip():
        return {
            "enabled": False,
            "follow_global": True,
            "model": "",
            "model_name": "",
            "provider": "",
            "base_url": "",
            "api_key": "",
            "has_api_key": False,
        }
    cfg = resolve_gen_llm_config()
    model_value = f"{cfg.provider}:{cfg.model}" if cfg.provider and cfg.model else cfg.model
    return {
        "enabled": True,
        "follow_global": False,
        "model": model_value,
        "model_name": cfg.model,
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "api_key": _mask_key(cfg.api_key),
        "has_api_key": bool(cfg.api_key),
    }


def _concurrency_snapshot() -> dict:
    """三池并发上限当前值（chat/gen/aux），供 GET 与 POST 响应复用"""
    return {
        "max_concurrency": get_llm_concurrency("chat"),
        "gen_concurrency": get_llm_concurrency("gen"),
        "aux_concurrency": get_llm_concurrency("aux"),
    }


def _chat_snapshot() -> dict:
    """对话内平台控制的当前生效值（网关每次调用现读 settings.chat）"""
    chat = settings.chat
    return {
        "platform_control_enabled": bool(chat.platform_control_enabled),
        "rate_limit_per_minute": int(chat.rate_limit_per_minute),
        "gen_limit_per_10min": int(chat.gen_limit_per_10min),
    }


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


def _write_env(env: dict[str, str], remove_keys: list[str] | None = None) -> None:
    """将字典写回 .env 文件（保留注释与空行）

    remove_keys 中的键直接从文件里删掉（用于「关闭生成模型」这类
    需要清除已有条目的场景——仅从 env 字典里 pop 是无法影响磁盘的）。
    """
    drop = set(remove_keys or [])
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
        if key in drop:
            seen.add(key)  # 视为已处理，避免尾部又被追加回来
            continue
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
    # .env 含 LLM API Key 等凭据，落盘即收紧权限（对照 secret.key 的 0600）
    os.chmod(ENV_FILE, 0o600)


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


def _apply_gen_update(payload: "LLMConfigUpdate", env: dict[str, str]) -> list[str]:
    """把生成模型的改动写进待落盘的 env 字典。

    返回需要**删除**的环境变量名（关闭生成模型时，内存里也要清掉，
    否则旧的 LLM_GEN_* 会继续生效、回退不到全局模型）。

    gen_model 语义：None=不改动 / ""=关闭并回退全局 / 其他=<provider>:<模型>
    """
    if payload.gen_model is None:
        return []
    value = payload.gen_model.strip()
    if not value:
        for k in ("LLM_GEN_PROVIDER", "LLM_GEN_MODEL", "LLM_GEN_API_KEY"):
            env.pop(k, None)
        return ["LLM_GEN_PROVIDER", "LLM_GEN_MODEL", "LLM_GEN_API_KEY"]

    provider, model_name = _split_model_value(value)
    # 自定义服务商必须显式给端点；生成链可独立于全局 LLM_BASE_URL 指定
    if provider not in PROVIDER_PRESETS and not (
        env.get("LLM_GEN_BASE_URL", "") or env.get("LLM_BASE_URL", "")
    ):
        raise HTTPException(
            400, f"自定义服务商 {provider} 需要在 .env 中配置 LLM_GEN_BASE_URL"
        )
    env["LLM_GEN_PROVIDER"] = provider
    env["LLM_GEN_MODEL"] = model_name
    gen_key = (payload.gen_api_key or "").strip()
    if gen_key:
        env["LLM_GEN_API_KEY"] = gen_key
    # 提前校验 Key 可用性：留空时可复用 LLM_API_KEY（provider 与全局一致）
    # 或 <PROVIDER>_API_KEY，全都没有的话保存后必然 401，不如现在就报出来
    if not env.get("LLM_GEN_API_KEY", ""):
        fallback = env.get(f"{provider.upper()}_API_KEY", "") or os.getenv(
            f"{provider.upper()}_API_KEY", ""
        )
        if not fallback and provider == env.get("LLM_PROVIDER", "").strip():
            fallback = env.get("LLM_API_KEY", "")
        if not fallback:
            raise HTTPException(
                400,
                f"生成模型 {provider} 没有可用 Key：请在上方填写 API Key，"
                f"或在 .env 中配置 {provider.upper()}_API_KEY",
            )
    return []


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
        **_concurrency_snapshot(),
        "debug_exec_timeout": get_debug_exec_timeout(),
        "debug_max_rounds": get_debug_max_rounds(),
        "agent_round_timeout": int(os.getenv("AGENT_ROUND_TIMEOUT", "7200") or 7200),
        "agent_idle_timeout": int(os.getenv("AGENT_IDLE_TIMEOUT", "300") or 300),
        "agent_cancel_grace": int(os.getenv("AGENT_CANCEL_GRACE", "10") or 10),
        # 生成/推理增强模型（可选，未配置时跟随上面的对话模型）
        "gen": _gen_snapshot(),
        # 对话内平台控制（限流与熔断）
        "chat": _chat_snapshot(),
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
    if payload.gen_concurrency is not None:
        env["LLM_GEN_MAX_CONCURRENCY"] = str(max(1, min(16, int(payload.gen_concurrency))))
    if payload.aux_concurrency is not None:
        env["LLM_AUX_MAX_CONCURRENCY"] = str(max(1, min(16, int(payload.aux_concurrency))))
    if payload.debug_exec_timeout is not None:
        env["DEBUG_EXEC_TIMEOUT"] = str(max(5, min(1800, int(payload.debug_exec_timeout))))
    if payload.debug_max_rounds is not None:
        env["DEBUG_MAX_ROUNDS"] = str(max(1, min(100, int(payload.debug_max_rounds))))
    # Agent 轮次防卡死：0 表示不限制（保持原「不设总超时」语义）
    if payload.agent_round_timeout is not None:
        env["AGENT_ROUND_TIMEOUT"] = str(max(0, min(86400, int(payload.agent_round_timeout))))
    if payload.agent_idle_timeout is not None:
        env["AGENT_IDLE_TIMEOUT"] = str(max(0, min(86400, int(payload.agent_idle_timeout))))
    if payload.agent_cancel_grace is not None:
        env["AGENT_CANCEL_GRACE"] = str(max(1, min(300, int(payload.agent_cancel_grace))))
    # 对话内平台控制：限流阈值 + 熔断开关
    if payload.rate_limit_per_minute is not None:
        env["SOTABAND_CHAT_RATE_LIMIT_PER_MIN"] = str(
            max(1, min(1000, int(payload.rate_limit_per_minute)))
        )
    if payload.gen_limit_per_10min is not None:
        env["SOTABAND_CHAT_GEN_LIMIT_PER_10MIN"] = str(
            max(1, min(100, int(payload.gen_limit_per_10min)))
        )
    if payload.platform_control_enabled is not None:
        env["SOTABAND_PLATFORM_CONTROL"] = "1" if payload.platform_control_enabled else "0"
    gen_removals = _apply_gen_update(payload, env)
    _write_env(env, gen_removals)

    # 动态刷新内存配置，使当前进程立即生效
    final_key = env.get("LLM_API_KEY", "")
    _refresh_settings(provider, model_name, final_key, env.get("LLM_BASE_URL", ""))

    if "LLM_TIMEOUT" in env:
        settings.llm.timeout = int(env["LLM_TIMEOUT"])
        os.environ["LLM_TIMEOUT"] = env["LLM_TIMEOUT"]
    for pool, key in (("chat", "LLM_MAX_CONCURRENCY"),
                      ("gen", "LLM_GEN_MAX_CONCURRENCY"),
                      ("aux", "LLM_AUX_MAX_CONCURRENCY")):
        if key in env:
            try:
                set_llm_concurrency(int(env[key]), pool)
            except ValueError:
                pass  # .env 里写了非数字：保持当前池值，不阻断其他配置保存
            os.environ[key] = env[key]
    if "DEBUG_EXEC_TIMEOUT" in env:
        os.environ["DEBUG_EXEC_TIMEOUT"] = env["DEBUG_EXEC_TIMEOUT"]
    if "DEBUG_MAX_ROUNDS" in env:
        os.environ["DEBUG_MAX_ROUNDS"] = env["DEBUG_MAX_ROUNDS"]
    # Agent 防卡死参数：工厂每轮现读环境变量，同步即即时生效
    for k in ("AGENT_ROUND_TIMEOUT", "AGENT_IDLE_TIMEOUT", "AGENT_CANCEL_GRACE"):
        if k in env:
            os.environ[k] = env[k]
    # 对话内平台控制：网关每次调用现读 settings.chat，同步内存 + 环境变量即即时生效
    if "SOTABAND_CHAT_RATE_LIMIT_PER_MIN" in env:
        settings.chat.rate_limit_per_minute = int(env["SOTABAND_CHAT_RATE_LIMIT_PER_MIN"])
        os.environ["SOTABAND_CHAT_RATE_LIMIT_PER_MIN"] = env["SOTABAND_CHAT_RATE_LIMIT_PER_MIN"]
    if "SOTABAND_CHAT_GEN_LIMIT_PER_10MIN" in env:
        settings.chat.gen_limit_per_10min = int(env["SOTABAND_CHAT_GEN_LIMIT_PER_10MIN"])
        os.environ["SOTABAND_CHAT_GEN_LIMIT_PER_10MIN"] = env["SOTABAND_CHAT_GEN_LIMIT_PER_10MIN"]
    if "SOTABAND_PLATFORM_CONTROL" in env:
        settings.chat.platform_control_enabled = env["SOTABAND_PLATFORM_CONTROL"] != "0"
        os.environ["SOTABAND_PLATFORM_CONTROL"] = env["SOTABAND_PLATFORM_CONTROL"]
    if payload.gen_model is not None:
        # 生成链客户端每次调用现算配置，同步 os.environ 即即时生效
        for k in ("LLM_GEN_PROVIDER", "LLM_GEN_MODEL", "LLM_GEN_API_KEY"):
            if k in env:
                os.environ[k] = env[k]
        # 关闭生成模型：内存里也要清掉，否则旧值继续生效、回退不到全局模型
        for k in gen_removals:
            os.environ.pop(k, None)

    return {
        "message": "配置已更新",
        "model": model_name,
        "provider": provider,
        "api_key": _mask_key(final_key),
        "has_api_key": bool(final_key),
        "timeout": settings.llm.timeout,
        **_concurrency_snapshot(),
        "debug_exec_timeout": get_debug_exec_timeout(),
        "debug_max_rounds": get_debug_max_rounds(),
        "agent_round_timeout": int(os.getenv("AGENT_ROUND_TIMEOUT", "7200") or 7200),
        "agent_idle_timeout": int(os.getenv("AGENT_IDLE_TIMEOUT", "300") or 300),
        "agent_cancel_grace": int(os.getenv("AGENT_CANCEL_GRACE", "10") or 10),
        "gen": _gen_snapshot(),
        "chat": _chat_snapshot(),
    }

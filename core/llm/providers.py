"""服务商目录：能力、推荐模型、端点与官方文档

## 为什么需要它

原先调用自定义模型时，使用者必须自己填 ``base_url``。但 base_url 是实现
细节，多数人并不清楚各家端点该填什么；填错还会导致请求发到错误地址。

本模块把「服务商 → 端点 / 能力 / 可用模型 / 官方文档」集中维护，使得：

- **选供应商即可，base_url 自动解析**——使用者只需填供应商、模型、Key
- **可按能力筛选**——例如"我要多模态，哪家支持？"一键列出
- **天然白名单**——只能选已登记的服务商，避免请求发往未知地址
- **给出官方文档链接**——需要查模型名时可直接跳转

## 维护说明

- ``base_url`` 以 ``config.settings.PROVIDER_PRESETS`` 为准（可被 .env 覆盖），
  这里只补充能力/模型/文档等元信息，避免两处维护端点导致不一致。
- 新增服务商：在 ``_PROVIDER_META`` 加条目即可；若端点也需要，同步加进
  ``PROVIDER_PRESETS``。
- 不在目录中的服务商仍可用：显式传 ``base_url`` 即可（custom 模式）。

## 关于「一个厂商多个端点」

相当多厂商按计费方式 / 订阅套餐 / 站点区域拆分出**互不通用的多套端点 + 多套
Key**。若把端点写死成其中一个，用另一类 Key 调用必然鉴权失败（401），
而报错信息通常只说 "invalid api key"，使用者很难自查。

因此端点解析统一走 :func:`resolve` 并带上 ``api_key``：
能按 Key 前缀区分的厂商（如 MiMo 的 ``sk-`` / ``tp-``）自动命中正确端点；
前缀无区分度的厂商（如 Moonshot 中国站/国际站）保留默认端点，
并在连接失败时通过 :func:`describe_endpoint_variants` 列出备选端点提示使用者。
"""

from __future__ import annotations

from config.settings import (
    PROVIDER_PRESETS,
    list_endpoint_variants,
    resolve_base_url,
)

# 能力标识
CAP_TEXT = "text"        # 纯文本对话
CAP_VISION = "vision"    # 图像理解（多模态）
CAP_REASONING = "reasoning"  # 推理模型（会先输出思考，max_tokens 要给足）

# 服务商元信息：能力、推荐模型、官方文档、Key 获取地址
# 说明：base_url 不在此维护，统一取自 PROVIDER_PRESETS（支持 .env 覆盖）
_PROVIDER_META: dict[str, dict] = {
    "deepseek": {
        "capabilities": [CAP_TEXT, CAP_REASONING],
        "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner"],
        "key_url": "https://platform.deepseek.com/api_keys",
        "docs_url": "https://api-docs.deepseek.com/",
        "notes": "性价比高；不支持图像输入。reasoner 为推理模型，max_tokens 需给足。",
    },
    "openai": {
        "capabilities": [CAP_TEXT, CAP_VISION],
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
        "key_url": "https://platform.openai.com/api-keys",
        "docs_url": "https://platform.openai.com/docs/models",
        "notes": "gpt-4o / gpt-4.1 系列支持图像输入。",
    },
    "doubao": {
        "capabilities": [CAP_TEXT, CAP_VISION],
        "models": ["doubao-pro-32k", "doubao-vision-pro-32k", "doubao-1.5-pro-32k"],
        "key_url": "https://console.volcengine.com/ark",
        "docs_url": "https://www.volcengine.com/docs/82379/1099320",
        "notes": "火山方舟；vision 系列支持图像输入。注意需在控制台创建推理接入点。",
    },
    "zhipu": {
        "capabilities": [CAP_TEXT, CAP_VISION],
        "models": ["glm-4-plus", "glm-4v-plus", "glm-4.5", "glm-4.5v"],
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "docs_url": "https://open.bigmodel.cn/dev/howuse/model",
        "notes": "glm-4v / glm-4.5v 支持图像输入。",
    },
    "qwen": {
        "capabilities": [CAP_TEXT, CAP_VISION],
        "models": ["qwen-plus", "qwen-max", "qwen-vl-max", "qwen2.5-vl-72b-instruct"],
        "key_url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
        "docs_url": "https://help.aliyun.com/zh/model-studio/models",
        "notes": "阿里云百炼；qwen-vl 系列支持图像输入。",
    },
    "moonshot": {
        "capabilities": [CAP_TEXT],
        "models": ["kimi-k2", "moonshot-v1-32k", "moonshot-v1-128k"],
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "docs_url": "https://platform.moonshot.cn/docs/guide/agent-support",
        "notes": "长文本能力强；当前不支持图像输入。",
    },
    "siliconflow": {
        "capabilities": [CAP_TEXT, CAP_VISION],
        "models": [
            "deepseek-ai/DeepSeek-V3",
            "Qwen/Qwen2.5-VL-72B-Instruct",
            "Pro/Qwen/Qwen2.5-VL-7B-Instruct",
        ],
        "key_url": "https://cloud.siliconflow.cn/account/ak",
        "docs_url": "https://docs.siliconflow.cn/",
        "notes": "聚合平台，一家 Key 可调用多个厂商模型；含 Qwen-VL 等视觉模型。",
    },
    "minimax": {
        "capabilities": [CAP_TEXT],
        "models": ["MiniMax-Text-01", "MiniMax-M1"],
        "key_url": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
        "docs_url": "https://platform.minimaxi.com/document/Models",
        "notes": "MiniMax-M1 为推理模型，max_tokens 需给足。",
    },
    "mimo": {
        "capabilities": [CAP_TEXT, CAP_VISION, CAP_REASONING],
        # 官方 curl 示例用小写 mimo-v2.5 / mimo-v2.5-pro，平台展示为
        # MiMo-V2.5 / MiMo-V2.5-Pro；两种写法服务端均可识别。
        "models": ["mimo-v2.5", "mimo-v2.5-pro", "MiMo-V2.5", "MiMo-V2.5-Pro"],
        "key_url": "https://platform.xiaomimimo.com/",
        "docs_url": "https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call",
        "notes": (
            "两套方案端点与 Key 互不通用，已按 Key 前缀自动选择："
            "按量付费 sk- → api.xiaomimimo.com；Token Plan 订阅 tp- → "
            "token-plan-cn.xiaomimimo.com。支持图像输入；"
            "推理模型，max_tokens 务必 >= 1500。"
        ),
    },
}

# 模型名 → 服务商 的推断规则（前缀匹配，用于用户只填模型名的场景）
_MODEL_PREFIX_HINTS: list[tuple[str, str]] = [
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("deepseek", "deepseek"),
    ("doubao", "doubao"),
    ("glm-", "zhipu"),
    ("qwen", "qwen"),
    ("kimi", "moonshot"),
    ("moonshot", "moonshot"),
    ("mimo", "mimo"),
    ("minimax", "minimax"),
]


def get_provider_meta(provider: str) -> dict:
    """取服务商元信息（能力/模型/文档），未知服务商返回空字典"""
    return _PROVIDER_META.get(provider, {})


def get_base_url(provider: str, api_key: str = "") -> str:
    """取服务商端点。以 PROVIDER_PRESETS 为准（可被 .env 覆盖）

    Args:
        provider: 服务商 id
        api_key:  API Key。同一厂商的多套方案（按量付费 / 订阅套餐 / 区域站点）
                  端点不同且 Key 不通用，能按 Key 前缀区分时会据此自动选端点
                  （如 MiMo 的 sk- / tp-）。不传则用预设默认端点。
    """
    preset = PROVIDER_PRESETS.get(provider, {})
    default_url = preset.get("default_base_url", "")
    return resolve_base_url(provider, api_key, default_url)


def describe_endpoint_variants(provider: str,
                               exclude: str = "") -> list[dict]:
    """列出该服务商的备选端点（排除 ``exclude``），用于连接失败时给出提示。

    解决的场景：使用者拿到的是「另一套方案」的 Key（例如 MiMo Token Plan 的
    tp- key 配了按量付费端点），服务端只会回 401/invalid api key，
    看不出该换哪个端点。这里把备选端点连同用途一起列出来。
    """
    out = []
    for v in list_endpoint_variants(provider):
        if exclude and v.get("base_url") == exclude:
            continue
        out.append({
            "base_url": v.get("base_url", ""),
            "label": v.get("label", ""),
            "key_prefix": v.get("key_prefix", ""),
            "docs_url": v.get("docs_url", ""),
        })
    return out


def format_endpoint_hint(provider: str, exclude: str = "") -> str:
    """把备选端点格式化为一行中文提示（用于报错 message）"""
    variants = describe_endpoint_variants(provider, exclude=exclude)
    if not variants:
        return ""
    parts = []
    for v in variants:
        prefix = f"key 前缀 {v['key_prefix']}" if v["key_prefix"] else "key 前缀无法区分"
        parts.append(f"{v['base_url']}（{v['label']}，{prefix}）")
    return (
        f"该服务商有多套端点且 Key 互不通用，请确认你的 Key 属于哪一套："
        + "；".join(parts)
        + "。可设 <PROVIDER>_BASE_URL 或 LLM_BASE_URL 显式指定。"
    )


def infer_provider_from_model(model: str) -> str | None:
    """根据模型名推断服务商。无法推断时返回 None"""
    m = (model or "").strip().lower()
    if not m:
        return None
    for prefix, provider in _MODEL_PREFIX_HINTS:
        if m.startswith(prefix):
            return provider
    return None


def list_providers(capability: str | None = None) -> list[dict]:
    """列出服务商目录。

    Args:
        capability: 按能力过滤，如 "vision" 只返回支持图像输入的服务商。

    返回按服务商 id 排序的列表，每项含：
        id / name / base_url / capabilities / models / key_url / docs_url / notes
    """
    providers = set(PROVIDER_PRESETS) | set(_PROVIDER_META)
    out = []
    for pid in sorted(providers):
        preset = PROVIDER_PRESETS.get(pid, {})
        meta = _PROVIDER_META.get(pid, {})
        caps = meta.get("capabilities", [])
        if capability and capability not in caps:
            continue
        out.append({
            "id": pid,
            "name": preset.get("name") or meta.get("name") or pid,
            "base_url": preset.get("default_base_url", ""),
            "capabilities": caps,
            "models": meta.get("models", [preset.get("default_model")] if preset.get("default_model") else []),
            "default_model": preset.get("default_model", ""),
            "endpoint_variants": [
                {"base_url": v.get("base_url", ""), "label": v.get("label", ""),
                 "key_prefix": v.get("key_prefix", ""), "docs_url": v.get("docs_url", "")}
                for v in list_endpoint_variants(pid)
            ],
            "key_url": meta.get("key_url", ""),
            "docs_url": meta.get("docs_url", ""),
            "notes": meta.get("notes", ""),
        })
    return out


def resolve(provider: str | None = None, model: str | None = None,
            base_url: str | None = None,
            api_key: str | None = None) -> tuple[str, str]:
    """解析出最终使用的 (provider, base_url)。

    优先级：显式 base_url > provider 查表 > model 名推断。

    Args:
        api_key: 用于在同一厂商的多套端点间自动选择（按 Key 前缀，
            如 MiMo 的 sk- / tp-）。不传则用预设默认端点。

    Returns:
        (provider, base_url)。若无法解析，base_url 为空串，由调用方报错。
    """
    base_url = (base_url or "").strip()
    key = (api_key or "").strip()

    # 1) 显式给了 base_url —— 自定义模式，直接采用
    if base_url:
        return (provider or "custom"), base_url

    # 2) 按 provider 查表
    p = (provider or "").strip()
    if p:
        url = get_base_url(p, api_key=key)
        if url:
            return p, url
        # provider 已知但无预设端点 → 留空让调用方报错并提示
        return p, ""

    # 3) 按 model 名推断
    inferred = infer_provider_from_model(model or "")
    if inferred:
        url = get_base_url(inferred, api_key=key)
        if url:
            return inferred, url

    return "", ""


def suggest_providers_for(capability: str) -> list[dict]:
    """列出支持某能力的服务商（用于"我要多模态，该选哪家"）"""
    return list_providers(capability=capability)


def infer_capabilities(model: str) -> list[str]:
    """根据模型名推断能力。

    静态目录无法覆盖所有模型，而服务商的 /v1/models 接口只返回模型 id，
    不返回能力。因此这里按业界命名惯例做启发式推断，目录中的显式标注
    优先级更高。

    推断规则（仅作提示，实际以服务商文档为准）：
    - 含 vl / vision / 4v / -v- 等 → 支持图像输入
    - 含 reasoner / o1 / o3 / r1 / m1 / think → 推理模型
    """
    m = (model or "").lower()
    caps = [CAP_TEXT]
    vision_hints = ("vl", "vision", "4v", "-v-", "omni", "image")
    reasoning_hints = ("reasoner", "reasoning", "r1", "o1", "o3", "o4", "m1", "think")
    if any(h in m for h in vision_hints):
        caps.append(CAP_VISION)
    if any(h in m for h in reasoning_hints):
        caps.append(CAP_REASONING)
    return caps


async def fetch_remote_models(provider: str | None = None,
                              base_url: str | None = None,
                              api_key: str = "",
                              timeout: float = 15.0) -> dict:
    """从服务商的 /v1/models 端点实时拉取可用模型列表。

    静态目录维护的模型名会过时（厂商上下线模型很频繁），而 OpenAI 兼容
    协议提供了标准的模型列表端点，用使用者的 Key 可以拉到**当前该账号
    真实可用**的模型。因此模型列表应以动态拉取为准，静态目录只保留
    能力标注与文档链接等 API 不提供的信息。

    Returns:
        {"ok": True,  "models": [...], "count": N, "source": "remote"}
        {"ok": False, "error": "..."}   拉取失败时（含脱敏后的错误说明）
    """
    from core.security.secrets import scrub_text

    resolved_provider, resolved_url = resolve(
        provider=provider, base_url=base_url, api_key=api_key
    )
    if not resolved_url:
        return {"ok": False, "error": "无法解析服务地址：请提供 provider 或 base_url"}
    if not api_key:
        return {"ok": False, "error": "未提供 api_key"}

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=resolved_url, timeout=timeout)
        try:
            resp = await client.models.list()
            models = []
            for m in getattr(resp, "data", []) or []:
                mid = getattr(m, "id", "") or ""
                if not mid:
                    continue
                # 目录有显式标注则以其为准，否则按命名推断
                caps = _lookup_capabilities(resolved_provider, mid)
                models.append({
                    "id": mid,
                    "owned_by": getattr(m, "owned_by", "") or "",
                    "capabilities": caps,
                    "from_catalog": bool(_lookup_catalog_caps(resolved_provider, mid)),
                })
            models.sort(key=lambda x: x["id"])
            return {
                "ok": True,
                "models": models,
                "count": len(models),
                "source": "remote",
                "provider": resolved_provider,
                "base_url": resolved_url,
            }
        finally:
            try:
                await client.close()
            except Exception:
                pass
    except Exception as e:
        # 脱敏后再返回：错误信息里可能带凭据
        return {
            "ok": False,
            "error": scrub_text(str(e), max_len=300),
            "provider": resolved_provider,
            "base_url": resolved_url,
        }


def _lookup_catalog_caps(provider: str | None, model: str) -> list[str] | None:
    """在静态目录里查某模型的显式能力标注；无则返回 None"""
    meta = _PROVIDER_META.get(provider or "", {})
    for m in meta.get("models", []):
        if m and (m == model or m.lower() == (model or "").lower()):
            return meta.get("capabilities")
    return None


def _lookup_capabilities(provider: str | None, model: str) -> list[str]:
    """优先用目录标注，目录没有则按命名推断"""
    return _lookup_catalog_caps(provider, model) or infer_capabilities(model)

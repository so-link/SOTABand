"""密钥安全：脱敏与泄露防护

## 背景

系统允许使用者为单个工具临时指定自己的 LLM 服务商、模型与 API Key
（见 ``api-llm-chat-with-config``）。这带来一个现实风险：API Key 会在
多个环节被记录，一旦落盘就长期留存。

已确认的泄露路径：

1. **调试日志** —— ``tool_builder._write_debug_log`` 会把工具的 stdout
   完整写入 ``logs/<tool_id>_<ts>.md``。工具若 print 了 key，它明文入库。
2. **自动调试 Prompt** —— 执行结果（stdout/stderr）会被拼进发给 LLM 的
   调试 Prompt。key 会离开本机，进入第三方模型服务商。
3. **工具异常信息** —— key 拼在 URL 或报错文本里时，会随异常向外传播。
4. **生成工具代码** —— LLM 可能把使用者填的 key 硬编码进生成的代码。

## 防护策略

单一依赖"工具作者自觉不打印 key"是不可靠的，因此在**基础设施层**统一拦截：
在日志写入、调试 Prompt 构造、异常返回三处对文本做脱敏。

本模块提供：
- ``looks_like_secret(value)``   判断值是否像密钥
- ``mask_secret(value)``         对单个值脱敏（保留前4后4）
- ``scrub_text(text)``           对任意文本脱敏（替换所有疑似密钥）
- ``scrub_mapping(obj)``         对 dict 脱敏（用于工具输入参数）
- ``SENSITIVE_KEY_NAMES``        需要整值脱敏的参数名
- ``KEY_PREFIXES``               各厂商 API Key 前缀登记表

## 关于 Key 前缀

密钥形态的判断不能写死在某家厂商上：同一厂商的不同计费/订阅方案前缀也不同
（小米 MiMo 按量付费为 ``sk-``、Token Plan 订阅为 ``tp-``），厂商还会不断增加。
因此把前缀抽成 ``KEY_PREFIXES`` 登记表，正则由其动态生成——
接入新厂商时只需加一项，而不是改正则，也不必担心漏掉某个前缀导致密钥未脱敏。
"""

from __future__ import annotations

import re

# 参数名包含这些词时，整个值视为敏感，直接整值脱敏
SENSITIVE_KEY_NAMES = {
    "api_key", "apikey", "api-key",
    "secret", "secret_key", "secretkey",
    "token", "access_token", "access-token", "refresh_token",
    "password", "passwd", "pwd",
    "authorization", "auth",
    "credential", "credentials",
    "private_key", "private-key",
}

# 已知厂商的 API Key 前缀（小写）。
#
# 各厂商前缀层出不穷，而且**同一厂商的不同计费/订阅方案前缀也不同**
# （典型如小米 MiMo：按量付费是 sk-，Token Plan 订阅是 tp-）。
# 因此这里集中登记前缀，新增厂商/新方案时只需往元组里加一项。
#
# 刻意**不做**「任意小写词 + 连字符 + 长随机串」这种宽泛匹配：
# 那会误伤 tool-id、日志文件名等普通文本（如 large-model-bounding-box、
# lunwen-pingshen_20260729_151202），把无关信息也打码，反而妨碍排查。
KEY_PREFIXES: tuple[str, ...] = (
    "sk-or-v1-",   # OpenRouter
    "sk-proj-",    # OpenAI 项目级 Key
    "sk-ant-",     # Anthropic
    "github_pat_", # GitHub 细粒度令牌
    "glpat-",      # GitLab
    "dop_v1_",     # DigitalOcean
    "sk-",         # OpenAI / DeepSeek / Moonshot / 通义 / 硅基流动 / MiMo 按量付费 ...
    "tp-",         # 小米 MiMo Token Plan 订阅
    "gsk_",        # Groq
    "xai-",        # xAI
    "ghp_",        # GitHub
)

# 由 KEY_PREFIXES 动态生成：「已知前缀 + 至少 12 位随机串」。
# 长前缀排在前面，保证 sk-ant- 优先于 sk- 命中。
_PREFIX_SECRET_PATTERN = re.compile(
    r"\b(?:%s)[A-Za-z0-9_\-]{12,}\b"
    % "|".join(re.escape(p) for p in sorted(KEY_PREFIXES, key=len, reverse=True))
)

# 常见密钥形态（按“长得像不像”判断，与参数名无关）
_SECRET_PATTERNS = [
    # 已知厂商前缀：sk-xxx / sk-ant-xxx / tp-xxx / gsk_xxx ...
    _PREFIX_SECRET_PATTERN,
    # Google AI: AIzaSy...
    re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}\b"),
    # AWS Access Key
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # 智谱式 {32位id}.{16位secret}：中间的点会打断“长随机串”规则，需单独匹配
    re.compile(r"\b[A-Za-z0-9]{24,}\.[A-Za-z0-9]{16,}\b"),
    # 通用长随机串（32位以上 hex / base62，无空格）
    re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"),
    # JWT
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
]

# URL 中 ?key=xxx / &api_key=xxx 形式
_URL_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key|secret)=)([^&\s\"']+)"
)

_MASK = "****"


def looks_like_secret(value: object) -> bool:
    """判断一个值是否像密钥。

    用于未知字段名的场景（如工具自己打印的日志）。
    注意：存在误判可能（很长的普通字符串也会被识别），
    但安全场景下"宁可多脱敏"优于"漏脱敏"。
    """
    if not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) < 16:
        return False
    return any(p.search(v) for p in _SECRET_PATTERNS)


def mask_secret(value: object) -> str:
    """对单个值脱敏：保留前 4 后 4，过短则全遮。

    对非字符串也生效（转成字符串后处理），保证返回值一定是 str，
    调用方无需关心类型。
    """
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    if not s:
        return ""
    if len(s) <= 8:
        return _MASK
    return f"{s[:4]}{_MASK}{s[-4:]}"


def _scrub_url_secrets(text: str) -> str:
    """脱敏 URL 查询参数中的密钥"""
    return _URL_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{_MASK}", text)


def scrub_text(text: object, max_len: int | None = None) -> str:
    """对任意文本脱敏，替换其中所有疑似密钥。

    用于日志写入、调试 Prompt 构造、异常信息返回前的统一处理。
    """
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    if not s:
        return ""

    s = _scrub_url_secrets(s)
    for pattern in _SECRET_PATTERNS:
        s = pattern.sub(_MASK, s)

    if max_len is not None and len(s) > max_len:
        s = s[:max_len] + f"...[已截断，原长 {len(s)}]"
    return s


def scrub_mapping(obj, _depth: int = 0):
    """对 dict / list 递归脱敏。

    - 参数名命中 SENSITIVE_KEY_NAMES → 整值脱敏（不依赖内容判断）
    - 参数名未命中，但值长得像密钥 → 也脱敏
    - 字符串值内部若嵌有密钥（如报错文本） → 文本级脱敏

    用于工具输入参数的落盘前处理。返回新对象，不修改原对象。
    """
    if _depth > 8:  # 防止循环引用或超深结构
        return obj

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key_str = str(k)
            normalized = key_str.lower().replace(" ", "_")
            is_sensitive_name = any(
                sn == normalized or sn in normalized for sn in SENSITIVE_KEY_NAMES
            )

            if is_sensitive_name and v:
                out[k] = mask_secret(v)
            elif isinstance(v, str):
                out[k] = scrub_text(v)
            elif isinstance(v, (dict, list)):
                out[k] = scrub_mapping(v, _depth + 1)
            else:
                out[k] = v
        return out

    if isinstance(obj, list):
        return [scrub_mapping(x, _depth + 1) if isinstance(x, (dict, list)) else
                (scrub_text(x) if isinstance(x, str) else x)
                for x in obj]

    return scrub_text(obj) if isinstance(obj, str) else obj


def redact_for_prompt(text: object, max_len: int = 4000) -> str:
    """为"发给 LLM 的 Prompt"做脱敏。

    这是最危险的一条路径：一旦 key 进入调试 Prompt，就会被发送到
    第三方模型服务商，且无法撤回。因此这里同时做脱敏与长度限制。
    """
    return scrub_text(text, max_len=max_len)

"""LLM 相关 HTTP 路由 — 配置查询与连接测试

## 权限与 SSRF（2026-11-04）

- 需要登录（路由级认证）。
- ``/test-connection`` 会向调用方给出的 ``base_url`` 发起请求，天然是 SSRF 面：
  仅租户 Owner/Admin 可用；``base_url`` 只允许 http/https，默认禁止内网/回环
  地址（本机 Ollama 场景用 ``SOTABAND_LLM_ALLOW_LOCAL=1`` 显式放开）。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_auth_context
from config.settings import PROVIDER_PRESETS, settings
from core.api.implementations.api_llm import ApiLlmGetConfig, ApiLlmTestConnection
from core.auth.context import AuthContext
from core.auth.platform_admin import is_platform_admin

#: OpenAI SDK / httpx 在 3xx 上抛出的错误文本（跟随重定向已关闭）
_REDIRECT_ERROR_RE = re.compile(r"(error code:\s*3\d{2})|redirect|moved", re.IGNORECASE)


def _is_redirect_failure(message: str) -> bool:
    """失败信息是否属于"端点返回 3xx 重定向"（→ 路由层映射为 400）。"""
    return bool(_REDIRECT_ERROR_RE.search(str(message or "")))

router = APIRouter()


@router.get("/config")
async def get_config(provider: str = None):
    """获取 LLM 配置（API Key 脱敏显示）

    provider 缺省时返回当前默认配置（LLM_PROVIDER / deepseek）。
    同时列出所有支持的服务商预设，方便前端做选择器。
    """
    cfg = ApiLlmGetConfig.call(provider=provider)
    key = cfg.get("api_key", "")
    masked = f"{key[:4]}****{key[-4:]}" if len(key) > 8 else ("****" if key else "")
    return {
        "provider": cfg["provider"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_masked": masked,
        "has_api_key": bool(key),
        "providers": [
            {"id": pid, "name": p["name"],
             "default_base_url": p["default_base_url"],
             "default_model": p["default_model"],
             # 同一厂商可能有多套互不通用的端点（按量付费 / 订阅套餐 / 区域站点），
             # 系统按 Key 前缀自动选择；这里一并下发，便于前端提示与手动切换。
             "endpoint_variants": [
                 {"base_url": v.get("base_url", ""), "label": v.get("label", ""),
                  "key_prefix": v.get("key_prefix", ""), "docs_url": v.get("docs_url", "")}
                 for v in p.get("endpoint_variants", [])
             ]}
            for pid, p in PROVIDER_PRESETS.items()
        ],
    }


def _host_is_internal(host: str) -> bool:
    """解析主机名并判断是否内网/回环/保留地址（防 SSRF 探测内网）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # 解析失败按"不可信"处理（fail-closed）
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def _validate_test_url(base_url: str) -> None:
    raw = (base_url or "").strip()
    if not raw:
        return
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "base_url 只允许 http/https")
    # userinfo（user:pass@host）既可能被用于混淆真实主机，也会随请求带出凭据；
    # LLM 端点没有这种用法，直接拒绝（v3 评审）。
    if "@" in (parsed.netloc or "") or parsed.username or parsed.password:
        raise HTTPException(400, "base_url 不允许包含用户名/密码（userinfo）")
    host = parsed.hostname or ""
    if not host:
        raise HTTPException(400, "base_url 缺少主机名")
    if not settings.file_access.llm_allow_local and _host_is_internal(host):
        raise HTTPException(
            400,
            "默认禁止测试内网/回环地址；本机 Ollama 等场景请设置 "
            "SOTABAND_LLM_ALLOW_LOCAL=1",
        )


@router.get("/test-connection")
async def test_connection(
    provider: str = None,
    api_key: str = None,
    base_url: str = None,
    model: str = None,
    ctx: AuthContext = Depends(get_auth_context),
):
    """测试 LLM 连接：验证 API Key / 端点 / 模型是否可用（仅平台管理员）。

    示例:
      curl "http://localhost:8000/api/llm/test-connection"
      curl "http://localhost:8000/api/llm/test-connection?provider=mimo"

    未传的参数自动从 .env 读取；api_key 建议放在 .env 中，
    query 传参仅用于前端"未保存先测试"的场景。

    ## SSRF（v3 评审）

    - ``base_url`` 只允许 http/https，拒绝 userinfo 与内网/回环地址；
    - 真正的客户端**不跟随重定向**（见 ``core.llm.client._make_http_client``），
      否则先校验初始 URL、再被 302 到内网即可绕过 —— 这里把 3xx 失败映射为 400。
    """
    # 先做 URL 安全校验，再做平台管理员授权：两者都失败时优先反馈 SSRF 拒绝，
    # 避免把"地址被拒"与"权限不足"混在一起（也便于部署方自检）。
    _validate_test_url(base_url or "")
    if not is_platform_admin(ctx):
        raise HTTPException(403, "只有平台管理员可以测试 LLM 连接")

    result = await ApiLlmTestConnection.call(
        provider=provider, api_key=api_key, base_url=base_url, model=model,
    )
    if not result.get("ok"):
        message = str(result.get("message") or result.get("error") or "")
        if _is_redirect_failure(message):
            raise HTTPException(
                400,
                "LLM 端点返回重定向（3xx），已拒绝跟随（防 SSRF 绕过）："
                f"{message[:200]}",
            )
    return result

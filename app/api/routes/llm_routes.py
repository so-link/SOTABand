"""LLM 相关 HTTP 路由 — 配置查询与连接测试"""

from fastapi import APIRouter

from config.settings import PROVIDER_PRESETS
from core.api.implementations.api_llm import ApiLlmGetConfig, ApiLlmTestConnection

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
             "default_model": p["default_model"]}
            for pid, p in PROVIDER_PRESETS.items()
        ],
    }


@router.get("/test-connection")
async def test_connection(provider: str = None, api_key: str = None,
                          base_url: str = None, model: str = None):
    """测试 LLM 连接：验证 API Key / 端点 / 模型是否可用

    示例:
      curl "http://localhost:8000/api/llm/test-connection"
      curl "http://localhost:8000/api/llm/test-connection?provider=mimo"
      curl "http://localhost:8000/api/llm/test-connection?provider=mimo&model=MiMo-V2.5-Pro"

    未传的参数自动从 .env 读取；api_key 建议放在 .env 中，
    query 传参仅用于前端"未保存先测试"的场景。
    """
    return await ApiLlmTestConnection.call(
        provider=provider, api_key=api_key, base_url=base_url, model=model,
    )

"""LLM 自定义端点密钥安全回归测试。

漏洞：不传 api_key + 传 provider=deepseek + 自定义 base_url 时，
降级链会取到 .env 的 LLM_API_KEY（平台主密钥）并发往任意端点。
修复：自定义 base_url 必须显式传 api_key，禁止借用 .env 主密钥。
"""

import pytest

pytestmark = pytest.mark.unit

from core.api.implementations.api_llm import ApiLlmChatWithConfig
from tests.conftest import run_coro


def _call(**overrides):
    params = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "deepseek-flash",
    }
    params.update(overrides)
    return run_coro(ApiLlmChatWithConfig.call(**params))


def test_custom_base_url_without_key_rejected(monkeypatch):
    """核心漏洞：外部 base_url + 不传 key → 拒绝，且不得带出主密钥。"""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 32)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-" + "y" * 32)

    r = _call(provider="deepseek", base_url="http://evil.example.com/v1")
    assert r.get("content") == ""
    assert "自定义 base_url" in r.get("error", "")
    assert "api_key" in r.get("error", "")
    # 错误信息不得泄露任何密钥片段
    assert "sk-" not in r.get("error", "")


def test_custom_base_url_with_explicit_key_allowed(monkeypatch):
    """显式给了 key 的自定义端点不拦截（由后续网络逻辑处理）。"""
    r = _call(provider="custom", base_url="http://my.local/v1",
              api_key="my-own-key")
    assert "自定义 base_url" not in (r.get("error") or "")


def test_official_provider_without_base_url_still_uses_env(monkeypatch):
    """只传 provider（无 base_url）时仍走 .env 降级，不被误伤。"""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "z" * 32)

    r = _call(provider="deepseek")
    # 不应被安全闸门拦截（可能因为网络/k8s 环境报别的错，但绝不是那条闸门）
    assert "自定义 base_url" not in (r.get("error") or "")

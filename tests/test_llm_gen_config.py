"""多 LLM 用途配置测试：对话模型 vs 生成/推理增强模型

覆盖：
- resolve_gen_llm_config 未配置 provider 时回退 settings.llm（同一对象，
  保证全局配置改动即时可见）
- 配置 LLM_GEN_* 后独立解析 provider/model/key/端点
- create_gen_llm_client 每次调用现算配置（设置页保存后无需重启）
"""

import os

import pytest

from tests.conftest import run_coro

pytestmark = pytest.mark.unit


from config.settings import LLMConfig, Settings, resolve_gen_llm_config, settings
from core.llm.client import create_gen_llm_client

_GEN_ENVS = (
    "LLM_GEN_PROVIDER", "LLM_GEN_MODEL", "LLM_GEN_API_KEY", "LLM_GEN_BASE_URL",
)


@pytest.fixture(autouse=True)
def _clean_gen_env(monkeypatch):
    """隔离生成模型的环境变量，避免 .env 的既有配置干扰断言"""
    for k in _GEN_ENVS:
        monkeypatch.delenv(k, raising=False)


def test_fallback_to_chat_config(monkeypatch):
    """未配置 LLM_GEN_PROVIDER → 直接复用对话模型配置对象"""
    monkeypatch.setattr(settings, "llm", LLMConfig(provider="deepseek", api_key="sk-a"))
    assert resolve_gen_llm_config() is settings.llm


def test_gen_config_is_independent(monkeypatch):
    """配置 LLM_GEN_* → 独立 provider/model/key，不影响对话模型"""
    monkeypatch.setattr(settings, "llm", LLMConfig(provider="deepseek", api_key="sk-a"))
    monkeypatch.setenv("LLM_GEN_PROVIDER", "mimo")
    monkeypatch.setenv("LLM_GEN_MODEL", "mimo-v2.5-pro")
    monkeypatch.setenv("LLM_GEN_API_KEY", "tp-gen-key")

    cfg = resolve_gen_llm_config()
    assert cfg.provider == "mimo"
    assert cfg.model == "mimo-v2.5-pro"
    assert cfg.api_key == "tp-gen-key"
    # tp- 前缀自动命中 Token Plan 端点（PROVIDER_PRESETS 的 endpoint_variants）
    assert "token-plan" in cfg.base_url
    # 对话模型不受污染
    assert settings.llm.provider == "deepseek"


def test_gen_key_falls_back_to_provider_env(monkeypatch):
    """LLM_GEN_API_KEY 留空 → 用 <PROVIDER>_API_KEY，模型用 <PROVIDER>_MODEL"""
    monkeypatch.setenv("LLM_GEN_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-provider")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    cfg = resolve_gen_llm_config()
    assert cfg.api_key == "sk-provider"
    assert cfg.model == "deepseek-chat"


def test_client_resolves_config_dynamically(monkeypatch):
    """生成链客户端每次读取时现算配置：改环境变量即刻生效，无需重建客户端"""
    monkeypatch.setattr(settings, "llm", LLMConfig(provider="deepseek", api_key="sk-a", model="m-chat"))
    client = create_gen_llm_client()
    assert client.config.provider == "deepseek"

    os.environ["LLM_GEN_PROVIDER"] = "mimo"
    os.environ["LLM_GEN_MODEL"] = "mimo-v2.5-pro"
    os.environ["LLM_GEN_API_KEY"] = "tp-gen"
    try:
        assert client.config.provider == "mimo"
        assert client.config.model == "mimo-v2.5-pro"
    finally:
        for k in _GEN_ENVS:
            os.environ.pop(k, None)

    # 关闭生成模型（清掉环境变量）后回到对话模型
    assert client.config.provider == "deepseek"


def test_settings_llm_snapshot_ignores_gen_env():
    """Settings() 构造不受 LLM_GEN_* 影响（生成配置只走 resolve_gen_llm_config）"""
    os.environ["LLM_GEN_PROVIDER"] = "mimo"
    try:
        snap = Settings()
        assert snap.llm.provider != "mimo" or os.getenv("LLM_PROVIDER") == "mimo"
    finally:
        os.environ.pop("LLM_GEN_PROVIDER", None)


def test_write_env_removes_keys(tmp_path, monkeypatch):
    """关闭生成模型：LLM_GEN_* 条目要从 .env 真正删除，且不被重新追加"""
    from app.api.routes import config_routes as cr

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 注释保留\nLLM_PROVIDER=deepseek\n"
        "LLM_GEN_PROVIDER=mimo\nLLM_GEN_MODEL=mimo-v2.5-pro\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "ENV_FILE", env_file)

    env = cr._read_env()
    cr._write_env(env, remove_keys=["LLM_GEN_PROVIDER", "LLM_GEN_MODEL"])
    text = env_file.read_text(encoding="utf-8")
    assert "LLM_GEN_" not in text
    assert "# 注释保留" in text
    assert "LLM_PROVIDER=deepseek" in text


def test_update_route_enable_then_disable(tmp_path, monkeypatch):
    """设置页「启用 → 关闭」全链路：落盘 + 内存（os.environ）同步清干净

    回归点：关闭生成模型时只改了 .env、没清 os.environ，
    会导致进程内 continue 用旧模型、GET 仍回显 enabled=true。
    """
    import asyncio

    from app.api.routes import config_routes as cr

    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_PROVIDER=deepseek\nLLM_API_KEY=sk-chat\nDEEPSEEK_API_KEY=sk-chat\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "ENV_FILE", env_file)
    monkeypatch.setattr(settings, "llm", LLMConfig(provider="deepseek", api_key="sk-chat"))
    # 路由会直接改这几个全局变量，用 monkeypatch 保证用例结束后还原
    for k, v in (
        ("LLM_PROVIDER", "deepseek"), ("LLM_MODEL", "deepseek-v4-pro"),
        ("LLM_API_KEY", "sk-chat"), ("LLM_BASE_URL", ""),
    ):
        monkeypatch.setenv(k, v)

    # 启用独立生成模型
    run_coro(cr.update_llm_config(cr.LLMConfigUpdate(
        model="deepseek:deepseek-v4-pro",
        gen_model="mimo:mimo-v2.5-pro",
        gen_api_key="tp-gen-key",
    )))
    assert os.getenv("LLM_GEN_PROVIDER") == "mimo"
    assert resolve_gen_llm_config().provider == "mimo"

    # 关闭 → 磁盘与内存都不得残留，且回退到对话模型
    run_coro(cr.update_llm_config(cr.LLMConfigUpdate(
        model="deepseek:deepseek-v4-pro", gen_model="",
    )))
    assert "LLM_GEN_" not in env_file.read_text(encoding="utf-8")
    assert os.getenv("LLM_GEN_PROVIDER") is None
    assert resolve_gen_llm_config() is settings.llm

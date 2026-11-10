"""get-key 明文密钥族下线回归（批次2-9，评审 C-3）。

api-doubao-get-key / api-deepseek-get-key 曾把平台密钥明文交给任何注册工具。
锁定替代后的不变量：

- 注册表不再含 get-key 条目，api-llm-images 已登记且定义文档存在
- get-key 实现类与定义文档已删除
- api-llm-get-config 只返回掩码视图（api_key 脱敏 + api_key_configured）
- 内置工具源码不再引用 get-key API
- 迁移脚本幂等：清除下线条目、补登记 images、重复执行无变更
"""

from __future__ import annotations

import importlib
import json

import pytest

pytestmark = pytest.mark.unit

RETIRED_IDS = ["api-doubao-get-key", "api-deepseek-get-key"]


def _registry() -> list[dict]:
    from core.api.registry import REGISTRY_FILE
    return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))


def test_registry_has_no_get_key_entries():
    ids = {e["id"] for e in _registry()}
    for retired in RETIRED_IDS:
        assert retired not in ids


def test_registry_has_llm_images_with_spec():
    entries = {e["id"]: e for e in _registry()}
    assert "api-llm-images" in entries
    entry = entries["api-llm-images"]
    from core.api.registry import API_DIR
    assert (API_DIR / entry["spec_path"]).exists()


def test_get_key_impl_classes_removed():
    mod = importlib.import_module("core.api.implementations.api_llm")
    assert not hasattr(mod, "ApiDoubaoGetKey")
    assert not hasattr(mod, "ApiDeepseekGetKey")
    assert hasattr(mod, "ApiLlmImages")


def test_get_key_definition_docs_removed():
    from core.api.registry import API_DIR
    for retired in RETIRED_IDS:
        assert not (API_DIR / "definitions" / f"{retired}.md").exists()


def test_get_config_returns_masked_key(monkeypatch):
    import core.api.implementations.api_llm as mod

    fake = {
        "provider": "deepseek",
        "api_key": "sk-abcdefgh12345678",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    }
    monkeypatch.setattr(mod, "get_llm_api_config", lambda provider: dict(fake))
    out = mod.ApiLlmGetConfig.call()
    assert out["api_key"] != fake["api_key"]
    assert "abcdefgh" not in out["api_key"]
    assert out["api_key_configured"] is True
    assert out["base_url"] == fake["base_url"]


def test_get_config_unconfigured_flag(monkeypatch):
    import core.api.implementations.api_llm as mod

    fake = {"provider": "doubao", "api_key": "", "base_url": "", "model": ""}
    monkeypatch.setattr(mod, "get_llm_api_config", lambda provider: dict(fake))
    out = mod.ApiLlmGetConfig.call()
    assert out["api_key"] == ""
    assert out["api_key_configured"] is False


def test_builtin_tools_have_no_get_key_refs():
    from pathlib import Path

    import config.settings as st
    root = Path(getattr(st, "PROJECT_ROOT", Path.cwd())) / "resources" / "tools" / "implementations"
    if not root.is_dir():
        root = Path("resources/tools/implementations")
    offenders = [
        str(p) for p in root.glob("*/tool.py")
        if any(rid in p.read_text(encoding="utf-8", errors="ignore") for rid in RETIRED_IDS)
    ]
    assert not offenders, f"内置工具仍引用已下线 API: {offenders}"


def test_migration_script_idempotent(tmp_path):
    from scripts.retire_get_key_apis import migrate_registry

    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps([
        {"id": "api-llm-chat", "name": "x"},
        {"id": "api-doubao-get-key", "name": "y"},
        {"id": "api-deepseek-get-key", "name": "z"},
    ]), encoding="utf-8")

    migrate_registry(reg)
    ids = [e["id"] for e in json.loads(reg.read_text(encoding="utf-8"))]
    assert "api-llm-chat" in ids
    assert "api-doubao-get-key" not in ids
    assert "api-deepseek-get-key" not in ids
    assert "api-llm-images" in ids

    before = reg.read_text(encoding="utf-8")
    migrate_registry(reg)
    assert reg.read_text(encoding="utf-8") == before

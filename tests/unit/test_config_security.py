"""v3 终审回归：配置注入、.env 行内注释、平台管理员、workload 令牌基线。

覆盖独立评审实测复现的四条：

1. **H config .env 注入** —— ``api_key`` / ``model`` 带换行可写入任意配置行；
2. **M `.env.example` 行内注释** —— ``SOTABAND_COOKIE_SECURE=1 # 注释`` 被解析成
   非 1 的字符串（布尔项静默为 False、int 项 ValueError）；
3. **M 平台管理员** —— 新增 ``is_platform_admin`` 与配置解析（空名单 = 无人）；
4. **M workload 令牌基线** —— TTL 无上限、短密钥无告警；
5. **H LLM test-connection SSRF** —— 拒绝 userinfo，客户端不跟随重定向，3xx → 400。
"""

from __future__ import annotations

import logging
import os
import uuid
from types import SimpleNamespace

import pytest


def _ctx(user_id: str, role: str = "member") -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, tenant_role=role)


# ══════════════════════════════════════════════════════════════════
# ⑤ config .env 注入
# ══════════════════════════════════════════════════════════════════


def test_write_env_rejects_newline_injection(tmp_path, monkeypatch):
    from app.api.routes import config_routes

    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=deepseek-v4-pro\n", encoding="utf-8")
    monkeypatch.setattr(config_routes, "ENV_FILE", env_file)

    with pytest.raises(config_routes.HTTPException) as exc:
        config_routes._write_env({"LLM_MODEL": "safe\nEVIL_ADMIN=1"})
    assert exc.value.status_code == 400
    assert "EVIL_ADMIN" not in env_file.read_text(encoding="utf-8")

    with pytest.raises(config_routes.HTTPException):
        config_routes._write_env({"LLM_API_KEY": "sk-x\r\nEVIL=1"})
    with pytest.raises(config_routes.HTTPException):
        # 键名同样不能注入
        config_routes._write_env({"EVIL\nKEY": "1"})
    # 正常值仍可写入
    config_routes._write_env({"LLM_MODEL": "deepseek-v4-pro"})
    assert "LLM_MODEL=deepseek-v4-pro" in env_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_config_update_rejects_injected_values(monkeypatch):
    """路由层字符集白名单：平台管理员也不能把换行写进 .env。"""
    from app.api.routes import config_routes
    from config.settings import settings

    admin = str(uuid.uuid4())
    monkeypatch.setattr(settings.security, "platform_admins", [admin])
    written: list[dict] = []
    monkeypatch.setattr(config_routes, "_write_env", lambda env: written.append(env))

    payload = config_routes.LLMConfigUpdate(model="deepseek:v4\nEVIL=1", api_key="")
    with pytest.raises(config_routes.HTTPException) as exc:
        await config_routes.update_llm_config(payload, ctx=_ctx(admin), session=None)
    assert exc.value.status_code == 400

    payload = config_routes.LLMConfigUpdate(
        model="deepseek:deepseek-v4-pro", api_key="sk-ok\nEVIL=1"
    )
    with pytest.raises(config_routes.HTTPException) as exc:
        await config_routes.update_llm_config(payload, ctx=_ctx(admin), session=None)
    assert exc.value.status_code == 400
    assert written == [], "非法值必须在写 .env 之前被拒绝"


# ══════════════════════════════════════════════════════════════════
# ⑧ _load_dotenv 行内注释
# ══════════════════════════════════════════════════════════════════


def test_parse_dotenv_value_strips_inline_comment():
    import config.settings as cs

    assert cs._parse_dotenv_value("1 # 生产 https 环境必须置 1") == "1"
    assert cs._parse_dotenv_value("900  # 15 分钟") == "900"
    # URL 里的 #（前面没有空格）必须保留
    assert (
        cs._parse_dotenv_value("http://localhost:5173/#/home")
        == "http://localhost:5173/#/home"
    )
    # 引号包裹的值原样保留（含 # 与空格）
    assert cs._parse_dotenv_value("'a # b'") == "a # b"
    assert cs._parse_dotenv_value('"x # y" # 注释') == "x # y"


def test_load_dotenv_handles_inline_comments(tmp_path, monkeypatch):
    import config.settings as cs

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SOTABAND_COOKIE_SECURE=1 # 生产 https 必须置 1",
                "LLM_TIMEOUT=900  # 15 分钟",
                "SOTABAND_CORS_ORIGINS=http://localhost:5173#frag",
                "SOTABAND_TEST_QUOTED='a # b'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    names = [
        "SOTABAND_COOKIE_SECURE",
        "LLM_TIMEOUT",
        "SOTABAND_CORS_ORIGINS",
        "SOTABAND_TEST_QUOTED",
    ]
    saved = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    try:
        cs._load_dotenv(env_file)
        # 布尔项能被正确识别（此前是 "1 # 注释" → False）
        assert os.environ["SOTABAND_COOKIE_SECURE"] == "1"
        # int 项不再 ValueError
        assert int(os.environ["LLM_TIMEOUT"]) == 900
        # URL 片段保留
        assert os.environ["SOTABAND_CORS_ORIGINS"] == "http://localhost:5173#frag"
        assert os.environ["SOTABAND_TEST_QUOTED"] == "a # b"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# ══════════════════════════════════════════════════════════════════
# ⑩ 平台管理员
# ══════════════════════════════════════════════════════════════════


def test_platform_admin_empty_list_means_nobody(monkeypatch):
    from config.settings import settings
    from core.auth.platform_admin import is_platform_admin

    monkeypatch.setattr(settings.security, "platform_admins", [])
    # 即使是租户 Owner 也不是平台管理员（两者语义不同）
    assert is_platform_admin(_ctx("tenant-owner", role="owner")) is False
    assert is_platform_admin(None) is False


def test_platform_admin_matches_uuid_case_insensitively(monkeypatch):
    from config.settings import settings
    from core.auth.platform_admin import is_platform_admin, platform_admin_ids

    admin_id = str(uuid.uuid4())
    monkeypatch.setattr(
        settings.security, "platform_admins", [f" {admin_id.upper()} ", ""]
    )
    # 集合内部统一成 32 位 hex（不带横线），与 SQLite 里 user.id 的形态一致
    assert platform_admin_ids() == {uuid.UUID(admin_id).hex}
    assert is_platform_admin(_ctx(admin_id.lower())) is True
    assert is_platform_admin(_ctx(str(uuid.uuid4()))) is False


def test_platform_admin_accepts_hex_and_dashed_forms(monkeypatch):
    """配置写 32 位 hex、上下文是带横线 UUID 时也必须匹配。

    这是真实踩过的坑：SQLite 的 ``user.id`` 落盘是不带横线的 hex，运维从
    数据库抄进 ``SOTABAND_PLATFORM_ADMINS``；而 ``AuthContext.user_id`` /
    ``/api/v1/me`` 给的是带横线的标准 UUID。只做 ``strip().lower()`` 比较会
    永远不相等，表现为「名单配好了，界面仍说你不是平台管理员」。
    """
    from config.settings import settings
    from core.auth.platform_admin import is_platform_admin, platform_admin_ids

    uuid_obj = uuid.uuid4()
    dashed = str(uuid_obj)
    hex32 = uuid_obj.hex

    # 配置 hex + 上下文 dashed → True（用户实际遇到的组合）
    monkeypatch.setattr(settings.security, "platform_admins", [hex32])
    assert platform_admin_ids() == {hex32}
    assert is_platform_admin(_ctx(dashed)) is True

    # 反过来：配置 dashed（大写）+ 上下文 hex → True
    monkeypatch.setattr(settings.security, "platform_admins", [dashed.upper()])
    assert is_platform_admin(_ctx(hex32)) is True

    # 误填邮箱等非 UUID 内容不会被误判
    monkeypatch.setattr(settings.security, "platform_admins", ["squall887@163.com"])
    assert is_platform_admin(_ctx(dashed)) is False


@pytest.mark.asyncio
async def test_config_get_and_test_connection_require_platform_admin(monkeypatch):
    from app.api.routes import config_routes, llm_routes
    from config.settings import settings

    monkeypatch.setattr(settings.security, "platform_admins", [])
    with pytest.raises(config_routes.HTTPException) as exc:
        await config_routes.get_llm_config(ctx=_ctx("u1", role="owner"))
    assert exc.value.status_code == 403

    with pytest.raises(llm_routes.HTTPException) as exc:
        await llm_routes.test_connection(
            provider=None, api_key="sk-x", base_url=None, model=None,
            ctx=_ctx("u1", role="owner"),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_config_get_allows_platform_admin(monkeypatch):
    from app.api.routes import config_routes
    from config.settings import settings

    admin = str(uuid.uuid4())
    monkeypatch.setattr(settings.security, "platform_admins", [admin])
    out = await config_routes.get_llm_config(ctx=_ctx(admin))
    assert "supported_models" in out
    assert "api_key" in out  # 已脱敏


# ══════════════════════════════════════════════════════════════════
# ⑨ workload 令牌 / secret 基线
# ══════════════════════════════════════════════════════════════════


def _now() -> int:
    from core.db.base import utcnow

    return int(utcnow().timestamp())


def test_workload_ttl_is_clamped_to_short_lived_range():
    from core.auth import workload

    now = _now()
    token, expires_at = workload.issue_workload_token(
        uuid.uuid4(), ttl_seconds=10**9
    )
    assert expires_at - now <= 3601, "TTL 必须有上限（短期凭证不能变成永久凭证）"
    assert workload.verify_workload_token(token).expires_at == expires_at

    # 过小 TTL 也夹到下限（60s），避免"签发即过期"的诡异行为
    _, short_exp = workload.issue_workload_token(uuid.uuid4(), ttl_seconds=1)
    assert 55 <= short_exp - now <= 65

    # 合法范围内按原值
    _, mid_exp = workload.issue_workload_token(uuid.uuid4(), ttl_seconds=120)
    assert 115 <= mid_exp - now <= 125


def test_short_workload_secret_warns_but_does_not_block(monkeypatch, caplog):
    from config.settings import settings
    from core.auth import workload

    monkeypatch.setattr(settings.workload, "secret", "too-short")
    monkeypatch.setattr(workload, "_short_secret_warned", False)
    with caplog.at_level(logging.WARNING, logger="core.auth.workload"):
        assert workload._secret() == b"too-short"
    assert "SOTABAND_WORKLOAD_SECRET" in caplog.text
    assert str(workload.MIN_SECRET_LENGTH) in caplog.text


# ══════════════════════════════════════════════════════════════════
# ⑥ LLM test-connection SSRF：userinfo / 不跟随重定向 / 3xx → 400
# ══════════════════════════════════════════════════════════════════


def test_validate_test_url_rejects_userinfo(monkeypatch):
    from app.api.routes import llm_routes

    fake_settings = SimpleNamespace(
        file_access=SimpleNamespace(llm_allow_local=True)
    )
    monkeypatch.setattr(llm_routes, "settings", fake_settings)

    for url in (
        "https://user:pass@api.example.com/v1",
        "https://user@api.example.com/v1",
    ):
        with pytest.raises(llm_routes.HTTPException) as exc:
            llm_routes._validate_test_url(url)
        assert exc.value.status_code == 400
    # 正常外网地址通过
    llm_routes._validate_test_url("https://api.example.com/v1")


@pytest.mark.asyncio
async def test_llm_client_disables_redirects():
    """OpenAI SDK 默认 http 客户端会跟随重定向 → 必须换成不跟随的自定义客户端。"""
    from config.settings import LLMConfig
    from core.llm.client import OpenAICompatibleClient

    cfg = LLMConfig(
        provider="deepseek",
        api_key="sk-" + "x" * 20,
        base_url="https://api.deepseek.com/v1",
    )
    client = OpenAICompatibleClient(cfg)
    client._build_client()
    try:
        http_client = client._last_http_client
        assert http_client is not None
        assert http_client.follow_redirects is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_test_connection_redirect_maps_to_400(monkeypatch):
    from app.api.routes import llm_routes
    from config.settings import settings

    admin = str(uuid.uuid4())
    monkeypatch.setattr(settings.security, "platform_admins", [admin])
    monkeypatch.setattr(llm_routes, "_validate_test_url", lambda _url: None)

    async def _fake_call(**kwargs):
        return {
            "ok": False,
            "message": "Error code: 302 - Found (redirect to http://169.254.169.254)",
            "provider": "deepseek",
            "model": "m",
            "base_url": "https://api.example.com/v1",
        }

    monkeypatch.setattr(
        llm_routes.ApiLlmTestConnection, "call", staticmethod(_fake_call)
    )
    with pytest.raises(llm_routes.HTTPException) as exc:
        await llm_routes.test_connection(
            provider=None, api_key="sk-x", base_url=None, model=None,
            ctx=_ctx(admin),
        )
    assert exc.value.status_code == 400

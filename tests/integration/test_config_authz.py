"""配置接口权限与 SSRF 防护（P1-4）测试。"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_config_update_requires_admin_role(monkeypatch, db_session):
    """非租户管理员无法修改全局 LLM 配置（403）。"""
    from app.api.routes import config_routes
    from core.auth.context import AuthContext
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from core.db.session import get_db_session

    async def _override() -> AsyncIterator[object]:
        yield db_session

    from collections.abc import AsyncIterator

    app.dependency_overrides[get_db_session] = _override
    # 直接覆盖认证依赖：member 角色
    from app.api.deps import get_auth_context

    member_ctx = AuthContext(
        user_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        tenant_role="member",
        request_id="t",
    )
    app.dependency_overrides[get_auth_context] = lambda: member_ctx
    monkeypatch.setattr(config_routes, "_write_env", lambda env: None)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/config/llm", json={"model": "deepseek:deepseek-v4-pro"}
            )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_llm_test_connection_blocks_internal_urls(monkeypatch):
    """SSRF：默认拒绝内网/回环地址；显式允许时可放行校验。"""
    from app.api.routes import llm_routes

    class _Ctx:
        user_id = str(uuid.uuid4())
        tenant_id = str(uuid.uuid4())
        tenant_role = "owner"
        request_id = "t"

    with pytest.raises(Exception) as exc:
        await llm_routes.test_connection(
            provider=None, api_key="sk-x", base_url="http://127.0.0.1:9999/v1",
            model=None, ctx=_Ctx(),
        )
    assert "内网" in str(exc.value)

    # 显式允许本机（Ollama 场景）时不应因地址被拒
    class _Allow:
        llm_allow_local = True

    class _FakeAccess:
        file_access = _Allow()

    monkeypatch.setattr(llm_routes, "settings", _FakeAccess())
    # 这里只验证 URL 校验通过（不发真实请求）：校验函数单独调用
    llm_routes._validate_test_url("http://127.0.0.1:11434/v1")

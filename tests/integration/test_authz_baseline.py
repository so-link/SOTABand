"""鉴权基线（D1）与资源写权限（D2）测试。

- 业务 API 未登录 → 401；认证生命周期/健康检查保持公开；
- 登录后业务 API 可访问；
- 资源写权限口径：owner 或租户 Owner/Admin；内置资产仅租户管理员。
"""

from __future__ import annotations

import pytest

#: 未登录必须 401 的代表性端点（覆盖每组业务路由）
PROTECTED = [
    ("GET", "/api/devices"),
    ("GET", "/api/data/list"),
    ("GET", "/api/file/list"),
    ("GET", "/api/tool/list"),
    ("GET", "/api/model/list"),
    ("GET", "/api/task/list"),
    ("GET", "/api/config/llm"),
    ("GET", "/api/migrate/bundles"),
    ("GET", "/api/apis/list"),
    ("POST", "/api/chat/stop"),
    ("GET", "/api/agent/list"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", PROTECTED)
async def test_anonymous_gets_401(api_client, method, path):
    resp = await api_client.request(method, path, json={} if method == "POST" else None)
    assert resp.status_code == 401, f"{method} {path} 应要求登录，实际 {resp.status_code}"


@pytest.mark.asyncio
async def test_public_endpoints_stay_public(api_client):
    assert (await api_client.get("/api/health")).status_code == 200
    probe = await api_client.get("/api/auth/session")
    assert probe.status_code == 200
    assert probe.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_authenticated_can_access_business_apis(authed_client):
    for path in (
        "/api/data/list",
        "/api/file/list",
        "/api/tool/list",
        "/api/model/list",
        "/api/task/list",
        "/api/migrate/bundles",
        "/api/apis/list",
    ):
        resp = await authed_client.get(path)
        assert resp.status_code == 200, f"{path} 登录后应可访问，实际 {resp.status_code}: {resp.text}"


# ── D2 写权限口径（纯规则层，不依赖 DB）──────────────────────────────


class _Ctx:
    def __init__(self, user_id: str, role: str = "member"):
        self.user_id = user_id
        self.tenant_role = role


def test_can_write_own_resource():
    from core.auth.ownership import can_write_resource

    assert can_write_resource(_Ctx("u1"), {"owner": "u1"}) is True


def test_member_cannot_write_others_resource():
    from core.auth.ownership import can_write_resource

    assert can_write_resource(_Ctx("u2"), {"owner": "u1"}) is False


def test_tenant_admin_can_write_others_resource():
    from core.auth.ownership import can_write_resource

    assert can_write_resource(_Ctx("u1", role="admin"), {"owner": "u2"}) is True
    assert can_write_resource(_Ctx("u1", role="owner"), {"owner": "u2"}) is True


def test_builtin_resource_requires_tenant_admin():
    from core.auth.ownership import can_write_resource

    assert can_write_resource(_Ctx("u1"), {"id": "x"}) is False
    assert can_write_resource(_Ctx("u1", role="admin"), {"id": "x"}) is True


def test_anonymous_cannot_write():
    from core.auth.ownership import can_write_resource

    assert can_write_resource(None, {"owner": "u1"}) is False

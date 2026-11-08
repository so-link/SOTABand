"""租户 API 集成测试 —— 创建、邀请、加入、切换激活租户（PRD FR-A6）。

## 这组测试解决了一个真实阻碍

在租户邀请实现之前，网络测试只能靠**直接改 ``TenantMembership``** 来搭建
"两人同租户"的前置条件 —— 也就是说 8.2 的多用户流程在真实 API 上走不通。

本文件的 ``test_full_multi_user_network_flow`` 走的是**纯 API 链路**：

```text
Bob 注册 → Alice 邀请 Bob 进租户 → Bob 接受 → Bob 切到该租户
→ Alice 建网络 → Alice 邀请 Bob 入网 → Bob 接受 → Bob 能读该网络
```

这条通过，8.1 + 8.2 才算真正打通。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

PW = "Passw0rd2026"
TENANTS = "/api/v1/tenants"


@asynccontextmanager
async def new_client():
    """独立 Cookie 罐的客户端（模拟不同浏览器）。"""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def register(client: AsyncClient, email: str, name: str) -> tuple[str, str]:
    """注册并返回 ``(user_id, tenant_id)``。"""
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PW, "displayName": name},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["user"]["id"], body["tenant"]["id"]


async def invite_and_accept(
    owner: AsyncClient, tenant_id: str, email: str, invitee: AsyncClient
) -> str:
    """Owner 邀请 → 受邀人接受。返回邀请 id。"""
    created = await owner.post(f"{TENANTS}/{tenant_id}/invitations", json={"email": email})
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    accepted = await invitee.post(f"/api/v1/tenant-invitations/{token}/accept")
    assert accepted.status_code == 200, accepted.text
    return created.json()["invitation"]["id"]


@pytest_asyncio.fixture
async def pair(db_session) -> AsyncIterator[dict[str, Any]]:
    """Alice（租户 A）与 Bob（租户 B）两个独立会话。

    ``db_session`` 参数虽然没直接用，但**必须有** —— 它负责建表并覆盖
    ``get_db_session`` 依赖；少了它 API 会打到空库上报 "no such table"。
    """
    async with new_client() as alice, new_client() as bob:
        alice_id, alice_tenant = await register(alice, "alice@example.com", "Alice")
        bob_id, bob_tenant = await register(bob, "bob@example.com", "Bob")
        yield {
            "alice": alice,
            "bob": bob,
            "alice_id": alice_id,
            "bob_id": bob_id,
            "alice_tenant": alice_tenant,
            "bob_tenant": bob_tenant,
        }


# ── 创建 ──────────────────────────────────────────────────────────────


class TestCreateTenant:
    async def test_creator_becomes_owner(self, pair) -> None:
        resp = await pair["alice"].post(TENANTS, json={"name": "飞控团队"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["role"] == "owner"
        assert body["status"] == "active"
        assert body["slug"]

    async def test_empty_name_rejected(self, pair) -> None:
        resp = await pair["alice"].post(TENANTS, json={"name": "   "})
        assert resp.status_code == 422

    async def test_create_does_not_switch_active_tenant(self, pair) -> None:
        """创建租户**不**自动切换 —— 切换永远是显式动作。"""
        before = (await pair["alice"].get("/api/v1/me")).json()["active_tenant_id"]
        await pair["alice"].post(TENANTS, json={"name": "另一个团队"})
        after = (await pair["alice"].get("/api/v1/me")).json()["active_tenant_id"]
        assert before == after


# ── 邀请与加入 ────────────────────────────────────────────────────────


class TestTenantInvitation:
    async def test_invite_and_accept(self, pair) -> None:
        await invite_and_accept(
            pair["alice"], pair["alice_tenant"], "bob@example.com", pair["bob"]
        )

        members = (
            await pair["alice"].get(f"{TENANTS}/{pair['alice_tenant']}/members")
        ).json()
        roles = {m["email"]: m["role"] for m in members}
        assert roles["bob@example.com"] == "member"

        # Bob 的租户列表现在有 2 个
        tenants = (await pair["bob"].get("/api/v1/me/tenants")).json()
        assert len(tenants) == 2

    async def test_token_returned_only_once(self, pair) -> None:
        created = await pair["alice"].post(
            f"{TENANTS}/{pair['alice_tenant']}/invitations",
            json={"email": "bob@example.com"},
        )
        assert created.json()["token"]

        listed = (
            await pair["alice"].get(f"{TENANTS}/{pair['alice_tenant']}/invitations")
        ).json()
        assert all("token" not in inv for inv in listed)

    async def test_inviting_existing_member_conflicts(self, pair) -> None:
        await invite_and_accept(
            pair["alice"], pair["alice_tenant"], "bob@example.com", pair["bob"]
        )
        resp = await pair["alice"].post(
            f"{TENANTS}/{pair['alice_tenant']}/invitations",
            json={"email": "bob@example.com"},
        )
        assert resp.status_code == 409

    async def test_only_owner_or_admin_can_invite(self, pair) -> None:
        """普通成员不能邀请 —— 租户层只有 Owner/Admin 能管邀请。"""
        await invite_and_accept(
            pair["alice"], pair["alice_tenant"], "bob@example.com", pair["bob"]
        )
        # Bob 切到 Alice 的租户后是 member
        await pair["bob"].post(
            "/api/v1/me/switch-tenant", json={"tenantId": pair["alice_tenant"]}
        )

        resp = await pair["bob"].post(
            f"{TENANTS}/{pair['alice_tenant']}/invitations",
            json={"email": "carol@example.com"},
        )
        assert resp.status_code == 403

    async def test_email_must_match(self, pair) -> None:
        """邀请发给 bob，Alice 自己拿着令牌也不能接受。"""
        created = await pair["alice"].post(
            f"{TENANTS}/{pair['alice_tenant']}/invitations",
            json={"email": "bob@example.com"},
        )
        token = created.json()["token"]

        wrong = await pair["alice"].post(f"/api/v1/tenant-invitations/{token}/accept")
        assert wrong.status_code == 403

    async def test_accept_twice_returns_410(self, pair) -> None:
        created = await pair["alice"].post(
            f"{TENANTS}/{pair['alice_tenant']}/invitations",
            json={"email": "bob@example.com"},
        )
        token = created.json()["token"]

        first = await pair["bob"].post(f"/api/v1/tenant-invitations/{token}/accept")
        assert first.status_code == 200

        second = await pair["bob"].post(f"/api/v1/tenant-invitations/{token}/accept")
        assert second.status_code == 410

    async def test_revoked_invitation_cannot_be_accepted(self, pair) -> None:
        created = await pair["alice"].post(
            f"{TENANTS}/{pair['alice_tenant']}/invitations",
            json={"email": "bob@example.com"},
        )
        body = created.json()

        revoke = await pair["alice"].delete(
            f"{TENANTS}/{pair['alice_tenant']}/invitations/{body['invitation']['id']}"
        )
        assert revoke.status_code == 204

        accepted = await pair["bob"].post(
            f"/api/v1/tenant-invitations/{body['token']}/accept"
        )
        assert accepted.status_code == 410


# ── 切换激活租户 ──────────────────────────────────────────────────────


class TestSwitchTenant:
    async def test_switch_after_join(self, pair) -> None:
        await invite_and_accept(
            pair["alice"], pair["alice_tenant"], "bob@example.com", pair["bob"]
        )

        resp = await pair["bob"].post(
            "/api/v1/me/switch-tenant", json={"tenantId": pair["alice_tenant"]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "member"

        me = (await pair["bob"].get("/api/v1/me")).json()
        assert me["active_tenant_id"] == pair["alice_tenant"]

    async def test_switch_is_a_server_side_fact(self, pair) -> None:
        """切换后**同会话的后续请求**都按新租户判定 —— 不是前端状态。"""
        await invite_and_accept(
            pair["alice"], pair["alice_tenant"], "bob@example.com", pair["bob"]
        )
        # 切换前：Bob 看 Alice 租户的成员列表 → 404（不是他当前租户）
        before = await pair["bob"].get(f"{TENANTS}/{pair['alice_tenant']}/members")
        assert before.status_code == 404

        await pair["bob"].post(
            "/api/v1/me/switch-tenant", json={"tenantId": pair["alice_tenant"]}
        )

        # 切换后：能看到
        after = await pair["bob"].get(f"{TENANTS}/{pair['alice_tenant']}/members")
        assert after.status_code == 200

    async def test_cannot_switch_to_foreign_tenant(self, pair) -> None:
        """Bob 不属于 Alice 的租户 → 403，且**保持原租户上下文**。"""
        before = (await pair["bob"].get("/api/v1/me")).json()["active_tenant_id"]

        resp = await pair["bob"].post(
            "/api/v1/me/switch-tenant", json={"tenantId": pair["alice_tenant"]}
        )
        assert resp.status_code == 403

        after = (await pair["bob"].get("/api/v1/me")).json()["active_tenant_id"]
        assert after == before, "切换失败不能改变激活租户"

    async def test_switch_back(self, pair) -> None:
        await invite_and_accept(
            pair["alice"], pair["alice_tenant"], "bob@example.com", pair["bob"]
        )
        await pair["bob"].post(
            "/api/v1/me/switch-tenant", json={"tenantId": pair["alice_tenant"]}
        )
        back = await pair["bob"].post(
            "/api/v1/me/switch-tenant", json={"tenantId": pair["bob_tenant"]}
        )
        assert back.status_code == 200
        assert (await pair["bob"].get("/api/v1/me")).json()["active_tenant_id"] == (
            pair["bob_tenant"]
        )


# ── 端到端：8.1 + 8.2 真正打通 ────────────────────────────────────────


class TestFullMultiUserFlow:
    async def test_bob_joins_alices_network_via_pure_api(self, pair) -> None:
        """纯 API 链路走通 8.2 的多用户场景（之前只能靠直接改数据库搭前置）。

        步骤：
        1. Bob 接受 Alice 的租户邀请
        2. Bob 切到 Alice 的租户
        3. Alice 建网络并邀请 Bob
        4. Bob 接受网络邀请
        5. Bob 能读该网络，且角色是 member
        """
        alice, bob = pair["alice"], pair["bob"]
        tenant_id = pair["alice_tenant"]

        # 1-2. 进租户 + 切换
        await invite_and_accept(alice, tenant_id, "bob@example.com", bob)
        switched = await bob.post(
            "/api/v1/me/switch-tenant", json={"tenantId": tenant_id}
        )
        assert switched.status_code == 200, switched.text

        # 3. Alice 建网络
        created = await alice.post("/api/v1/networks", json={"name": "飞控算法组"})
        assert created.status_code == 201, created.text
        network_id = created.json()["id"]

        # 4. 邀请 Bob 入网 + Bob 接受
        invitation = await alice.post(
            f"/api/v1/networks/{network_id}/invitations",
            json={"email": "bob@example.com"},
        )
        assert invitation.status_code == 201, invitation.text
        accepted = await bob.post(
            f"/api/v1/network-invitations/{invitation.json()['token']}/accept"
        )
        assert accepted.status_code == 200, accepted.text

        # 5. Bob 能读网络，角色是 member
        detail = await bob.get(f"/api/v1/networks/{network_id}")
        assert detail.status_code == 200
        assert detail.json()["role"] == "member"

        # Bob 也在网络列表里看到它
        listed = (await bob.get("/api/v1/networks")).json()
        assert [n["id"] for n in listed] == [network_id]

        # Alice 的成员列表里能看到 Bob
        members = (await alice.get(f"/api/v1/networks/{network_id}/members")).json()
        assert {m["email"] for m in members} == {
            "alice@example.com",
            "bob@example.com",
        }

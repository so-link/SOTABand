"""网络 API 集成测试 —— 契约 §3.2、§5、§6。

## ⚠️ 测试前置：用户必须同租户（这里直改数据库只为提速）

注册时每个用户都会自动获得**自己的个人租户**（PRD FR-A1），而网络**属且仅属
一个租户**（PRD 开放问题 2）。所以 Bob 要加入 Alice 的网络，必须先身处 Alice
的租户。

**"加入他人租户 + 切换激活租户"的 API 现在已存在**（Step 3：
``POST /api/v1/tenants/{id}/invitations`` + ``POST /api/v1/me/switch-tenant``）。
真实链路在 ``tests/integration/test_tenant_api.py::TestFullMultiUserFlow``
有完整覆盖。

本文件仍用 :func:`move_to_tenant` 直改 ``TenantMembership``，纯粹是为了给每个
网络用例省掉 3 次 HTTP 往返 —— 被测的是网络授权链，不是租户模块，前置搭建方式
不影响结论。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import TENANT_ROLE_MEMBER, TenantMembership

PW = "Passw0rd2026"

NETWORKS = "/api/v1/networks"


# ── 测试脚手架 ────────────────────────────────────────────────────────


@asynccontextmanager
async def new_client():
    """独立 Cookie 罐的客户端（模拟不同浏览器）。"""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def register(client: AsyncClient, email: str, display_name: str) -> tuple[str, str]:
    """注册并返回 ``(user_id, tenant_id)``。"""
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PW, "displayName": display_name},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["user"]["id"], body["tenant"]["id"]


async def move_to_tenant(
    session: AsyncSession, *, user_id: str, tenant_id: str
) -> None:
    """把用户从个人租户挪进共享租户（见模块文档的说明）。"""
    uid = uuid.UUID(user_id)
    tid = uuid.UUID(tenant_id)

    rows = list(
        (
            await session.execute(
                select(TenantMembership).where(TenantMembership.user_id == uid)
            )
        ).scalars()
    )
    for membership in rows:
        if membership.tenant_id != tid:
            await session.delete(membership)
    await session.flush()

    if not any(membership.tenant_id == tid for membership in rows):
        session.add(
            TenantMembership(
                tenant_id=tid,
                user_id=uid,
                role=TENANT_ROLE_MEMBER,
                status="active",
            )
        )
    await session.commit()


@dataclass
class World:
    """契约 §12.2 的测试环境：两租户、四用户、一个网络。"""

    alice: AsyncClient  # N1 的 owner
    bob: AsyncClient  # N1 的 member
    carol: AsyncClient  # 与 Alice 同租户，但不是 N1 成员
    dave: AsyncClient  # 另一个租户，完全无关
    alice_id: str
    bob_id: str
    carol_id: str
    dave_id: str
    tenant_id: str
    network_id: str


@pytest_asyncio.fixture
async def world(db_session: AsyncSession) -> World:
    async with new_client() as alice, new_client() as bob, new_client() as carol, new_client() as dave:
        alice_id, tenant_id = await register(alice, "alice@example.com", "Alice")
        bob_id, _ = await register(bob, "bob@example.com", "Bob")
        carol_id, _ = await register(carol, "carol@example.com", "Carol")
        # Dave 留在自己的租户里，用来验证跨租户隔离
        dave_id, _ = await register(dave, "dave@example.com", "Dave")

        # Bob 与 Carol 进入 Alice 的租户（Carol 仍不是网络成员）
        await move_to_tenant(db_session, user_id=bob_id, tenant_id=tenant_id)
        await move_to_tenant(db_session, user_id=carol_id, tenant_id=tenant_id)

        created = await alice.post(NETWORKS, json={"name": "飞控算法组"})
        assert created.status_code == 201, created.text
        network_id = created.json()["id"]

        yield World(
            alice=alice,
            bob=bob,
            carol=carol,
            dave=dave,
            alice_id=alice_id,
            bob_id=bob_id,
            carol_id=carol_id,
            dave_id=dave_id,
            tenant_id=tenant_id,
            network_id=network_id,
        )


async def invite_and_join(world: World, *, email: str, client: AsyncClient) -> str:
    """Alice 邀请 → 该用户接受。返回邀请 id。"""
    resp = await world.alice.post(
        f"{NETWORKS}/{world.network_id}/invitations", json={"email": email}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    token = body["token"]

    accepted = await client.post(f"/api/v1/network-invitations/{token}/accept")
    assert accepted.status_code == 200, accepted.text
    return body["invitation"]["id"]


# ── 创建与列表 ────────────────────────────────────────────────────────


class TestCreateNetwork:
    async def test_creator_becomes_owner(self, world: World) -> None:
        resp = await world.alice.get(f"{NETWORKS}/{world.network_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "owner"
        assert body["status"] == "active"
        assert body["member_count"] == 1

    async def test_default_policy_is_conservative(self, world: World) -> None:
        """默认策略两个开关都关（契约 §2.3）。"""
        body = (await world.alice.get(f"{NETWORKS}/{world.network_id}")).json()
        assert body["policy"] == {
            "allowMemberPublish": False,
            "allowViewerInvoke": False,
        }

    async def test_duplicate_name_in_same_tenant_conflicts(self, world: World) -> None:
        resp = await world.alice.post(NETWORKS, json={"name": "飞控算法组"})
        assert resp.status_code == 409

    async def test_same_name_in_other_tenant_is_allowed(self, world: World) -> None:
        """网络名只在租户内唯一（PRD FR-N1）。Dave 在他的租户里可以重名。"""
        resp = await world.dave.post(NETWORKS, json={"name": "飞控算法组"})
        assert resp.status_code == 201

    async def test_list_only_shows_my_networks(self, world: World) -> None:
        alice_list = (await world.alice.get(NETWORKS)).json()
        assert len(alice_list) == 1
        # Carol 同租户但不是成员 → 看不到
        carol_list = (await world.carol.get(NETWORKS)).json()
        assert carol_list == []


# ── 隔离：同租户非成员 / 跨租户 ───────────────────────────────────────


class TestIsolation:
    async def test_same_tenant_non_member_gets_404(self, world: World) -> None:
        """Carol 与 Alice 同租户但不是 N1 成员 —— 全部 404（不泄露存在性）。"""
        for path in (
            f"{NETWORKS}/{world.network_id}",
            f"{NETWORKS}/{world.network_id}/members",
            f"{NETWORKS}/{world.network_id}/invitations",
        ):
            resp = await world.carol.get(path)
            assert resp.status_code == 404, f"{path} 应返回 404，实际 {resp.status_code}"

    async def test_cross_tenant_gets_404(self, world: World) -> None:
        resp = await world.dave.get(f"{NETWORKS}/{world.network_id}")
        assert resp.status_code == 404

    async def test_guessing_id_is_indistinguishable_from_missing(self, world: World) -> None:
        """猜 ID 与真正不存在必须同形（契约 §4）。"""
        fake = str(uuid.uuid4())
        guessed = await world.carol.get(f"{NETWORKS}/{fake}")
        real_but_hidden = await world.carol.get(f"{NETWORKS}/{world.network_id}")
        assert guessed.status_code == real_but_hidden.status_code == 404
        assert guessed.json() == real_but_hidden.json()

    async def test_malformed_id_is_404_not_400(self, world: World) -> None:
        resp = await world.alice.get(f"{NETWORKS}/not-a-uuid")
        assert resp.status_code == 404

    async def test_unauthenticated_is_401(self, world: World) -> None:
        async with new_client() as anon:
            resp = await anon.get(f"{NETWORKS}/{world.network_id}")
        assert resp.status_code == 401


# ── 邀请与加入 ────────────────────────────────────────────────────────


class TestInvitation:
    async def test_invite_and_accept(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)

        members = (await world.alice.get(f"{NETWORKS}/{world.network_id}/members")).json()
        assert len(members) == 2
        roles = {m["email"]: m["role"] for m in members}
        assert roles["bob@example.com"] == "member"

        # Bob 现在能读网络
        assert (await world.bob.get(f"{NETWORKS}/{world.network_id}")).status_code == 200

    async def test_token_is_returned_exactly_once(self, world: World) -> None:
        """明文令牌只在创建响应里出现；库里只有 hash。"""
        resp = await world.alice.post(
            f"{NETWORKS}/{world.network_id}/invitations", json={"email": "bob@example.com"}
        )
        token = resp.json()["token"]
        assert token

        listed = (
            await world.alice.get(f"{NETWORKS}/{world.network_id}/invitations")
        ).json()
        assert all("token" not in inv for inv in listed)
        # 列表里的 email/status 可见，但令牌不可见
        assert listed[0]["email"] == "bob@example.com"
        assert listed[0]["status"] == "pending"

    async def test_accept_twice_returns_410(self, world: World) -> None:
        resp = await world.alice.post(
            f"{NETWORKS}/{world.network_id}/invitations", json={"email": "bob@example.com"}
        )
        token = resp.json()["token"]

        first = await world.bob.post(f"/api/v1/network-invitations/{token}/accept")
        assert first.status_code == 200

        second = await world.bob.post(f"/api/v1/network-invitations/{token}/accept")
        assert second.status_code == 410, "同一令牌二次使用必须失败"

    async def test_invitation_email_must_match_account(self, world: World) -> None:
        """邀请发给 bob，carol 用这个令牌不能接受（PRD FR-N3）。"""
        resp = await world.alice.post(
            f"{NETWORKS}/{world.network_id}/invitations", json={"email": "bob@example.com"}
        )
        token = resp.json()["token"]

        wrong_user = await world.carol.post(f"/api/v1/network-invitations/{token}/accept")
        assert wrong_user.status_code == 403

    async def test_inviting_existing_member_conflicts(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        resp = await world.alice.post(
            f"{NETWORKS}/{world.network_id}/invitations", json={"email": "bob@example.com"}
        )
        assert resp.status_code == 409

    async def test_revoked_invitation_cannot_be_accepted(self, world: World) -> None:
        resp = await world.alice.post(
            f"{NETWORKS}/{world.network_id}/invitations", json={"email": "bob@example.com"}
        )
        body = resp.json()
        invitation_id = body["invitation"]["id"]
        token = body["token"]

        revoke = await world.alice.delete(
            f"{NETWORKS}/{world.network_id}/invitations/{invitation_id}"
        )
        assert revoke.status_code == 204

        accepted = await world.bob.post(f"/api/v1/network-invitations/{token}/accept")
        assert accepted.status_code == 410


# ── 角色与权限 ────────────────────────────────────────────────────────


class TestRoles:
    async def test_member_cannot_invite(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        resp = await world.bob.post(
            f"{NETWORKS}/{world.network_id}/invitations", json={"email": "x@example.com"}
        )
        assert resp.status_code == 403

    async def test_member_cannot_archive(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        resp = await world.bob.patch(
            f"{NETWORKS}/{world.network_id}", json={"status": "archived"}
        )
        assert resp.status_code == 403

    async def test_owner_promotes_member_to_admin(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        resp = await world.alice.patch(
            f"{NETWORKS}/{world.network_id}/members/{world.bob_id}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"
        assert resp.json()["version"] == 2

    async def test_admin_cannot_remove_owner(self, world: World) -> None:
        """Admin 对 Owner 的动作要表现为"成员不存在"（404），不是 403。

        403 等于向 Admin 确认"这里有个我动不了的 Owner"。
        """
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        await world.alice.patch(
            f"{NETWORKS}/{world.network_id}/members/{world.bob_id}", json={"role": "admin"}
        )

        resp = await world.bob.delete(
            f"{NETWORKS}/{world.network_id}/members/{world.alice_id}"
        )
        assert resp.status_code == 404

    async def test_admin_cannot_grant_owner(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        await world.alice.patch(
            f"{NETWORKS}/{world.network_id}/members/{world.bob_id}", json={"role": "admin"}
        )
        # 再拉一个人进来，让 Bob 尝试把他升为 owner
        carol_invite = await world.alice.post(
            f"{NETWORKS}/{world.network_id}/invitations", json={"email": "carol@example.com"}
        )
        await world.carol.post(
            f"/api/v1/network-invitations/{carol_invite.json()['token']}/accept"
        )

        resp = await world.bob.patch(
            f"{NETWORKS}/{world.network_id}/members/{world.carol_id}",
            json={"role": "owner"},
        )
        assert resp.status_code == 403

    async def test_last_owner_cannot_leave(self, world: World) -> None:
        resp = await world.alice.post(f"{NETWORKS}/{world.network_id}/leave")
        assert resp.status_code == 409

    async def test_last_owner_cannot_be_demoted(self, world: World) -> None:
        resp = await world.alice.patch(
            f"{NETWORKS}/{world.network_id}/members/{world.alice_id}",
            json={"role": "admin"},
        )
        assert resp.status_code == 409

    async def test_stale_version_conflicts(self, world: World) -> None:
        """并发改角色：旧 version 的请求必须 409（contract §4）。"""
        await invite_and_join(world, email="bob@example.com", client=world.bob)

        first = await world.alice.patch(
            f"{NETWORKS}/{world.network_id}/members/{world.bob_id}",
            json={"role": "admin", "expectedVersion": 1},
        )
        assert first.status_code == 200

        stale = await world.alice.patch(
            f"{NETWORKS}/{world.network_id}/members/{world.bob_id}",
            json={"role": "viewer", "expectedVersion": 1},
        )
        assert stale.status_code == 409

    async def test_member_can_leave(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        resp = await world.bob.post(f"{NETWORKS}/{world.network_id}/leave")
        assert resp.status_code == 204

        # 退出后立即失去访问（无权限缓存，每次读事实源）
        assert (await world.bob.get(f"{NETWORKS}/{world.network_id}")).status_code == 404


# ── 网络状态机 ────────────────────────────────────────────────────────


class TestNetworkStatus:
    async def test_suspended_allows_read_but_blocks_write(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        await world.alice.patch(
            f"{NETWORKS}/{world.network_id}", json={"status": "suspended"}
        )

        # 读仍然可以（便于排查问题）
        assert (await world.bob.get(f"{NETWORKS}/{world.network_id}")).status_code == 200
        # 写被拒（409，不是 403 —— 这是状态冲突而非权限不足）
        resp = await world.bob.post(
            f"{NETWORKS}/{world.network_id}/leave"
        )
        assert resp.status_code == 409

    async def test_archived_hides_network_from_members(self, world: World) -> None:
        await invite_and_join(world, email="bob@example.com", client=world.bob)
        await world.alice.patch(
            f"{NETWORKS}/{world.network_id}", json={"status": "archived"}
        )

        # 归档后普通成员看不到（404），owner 仍能看到（审计需要）
        assert (await world.bob.get(f"{NETWORKS}/{world.network_id}")).status_code == 404
        assert (await world.alice.get(f"{NETWORKS}/{world.network_id}")).status_code == 200

    async def test_invalid_status_transition(self, world: World) -> None:
        """archived 不能直接变 suspended（必须先恢复 active）。"""
        await world.alice.patch(
            f"{NETWORKS}/{world.network_id}", json={"status": "archived"}
        )
        resp = await world.alice.patch(
            f"{NETWORKS}/{world.network_id}", json={"status": "suspended"}
        )
        assert resp.status_code == 409

    async def test_archived_can_restore(self, world: World) -> None:
        await world.alice.patch(
            f"{NETWORKS}/{world.network_id}", json={"status": "archived"}
        )
        resp = await world.alice.patch(
            f"{NETWORKS}/{world.network_id}", json={"status": "active"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

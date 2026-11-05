"""共享（发布 / 目录 / 安装）集成测试 —— 契约 §3.3。

## 前置搭建方式

用**直接写数据库**的方式铺好"三个人在同一个租户、其中两人在同一个网络"的
前置条件，再全部通过 HTTP 测共享接口。理由与 ``test_network_api.py`` 相同：
被测的是**共享授权链**，不是租户/网络模块；省掉每例 7 次 HTTP 往返。

真实的多用户链路（邀请进空间 → 切空间 → 邀请入网）由
``test_tenant_api.py::TestFullMultiUserFlow`` 覆盖。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.service import normalize_email
from core.db.base import utcnow
from core.db.models import (
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    TENANT_ROLE_MEMBER,
    Network,
    NetworkMembership,
    Tenant,
    TenantMembership,
    User,
)

PW = "Passw0rd2026"
CATALOG = "/api/v1/network-catalog"


@asynccontextmanager
async def new_client():
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def register(client: AsyncClient, email: str, name: str) -> dict:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PW, "displayName": name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@dataclass
class World:
    alice: AsyncClient  # 网络 Owner
    bob: AsyncClient  # 网络 member
    carol: AsyncClient  # 同租户，但不是网络成员
    network_id: str
    tenant_id: str


@pytest_asyncio.fixture
async def world(db_session: AsyncSession, release_space) -> World:
    """一个租户、一个网络、三个用户（alice=owner, bob=member, carol=非成员）。"""
    tag = uuid.uuid4().hex[:8]

    # 直接建租户与用户（不经过注册接口，省限流与往返）
    tenant = Tenant(name=f"共享测试-{tag}", slug=f"share-{tag}")
    db_session.add(tenant)
    await db_session.flush()

    users: dict[str, User] = {}
    for name in ("alice", "bob", "carol"):
        user = User(
            email=normalize_email(f"{name}-{tag}@sharing.test"),
            display_name=name,
            status="active",
        )
        db_session.add(user)
        users[name] = user
    await db_session.flush()

    # 三人都是租户成员（alice 是 owner，其余 member）
    db_session.add_all(
        [
            TenantMembership(
                tenant_id=tenant.id,
                user_id=users["alice"].id,
                role="owner",
                status="active",
            ),
            *[
                TenantMembership(
                    tenant_id=tenant.id,
                    user_id=users[name].id,
                    role=TENANT_ROLE_MEMBER,
                    status="active",
                )
                for name in ("bob", "carol")
            ],
        ]
    )

    network = Network(
        tenant_id=tenant.id,
        name=f"N-{tag}",
        owner_id=users["alice"].id,
        status="active",
        policy={},
    )
    db_session.add(network)
    await db_session.flush()

    db_session.add_all(
        [
            NetworkMembership(
                network_id=network.id,
                user_id=users["alice"].id,
                role=NETWORK_ROLE_OWNER,
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            ),
            NetworkMembership(
                network_id=network.id,
                user_id=users["bob"].id,
                role=NETWORK_ROLE_MEMBER,
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            ),
        ]
    )
    await db_session.commit()

    # ── 合成 release（发布接口会校验资源归属，见管理员评审意见 2）──
    # 每个 release_id 的 owner 与「发布者身份」对应，保证测试验证的是
    # 发布范围权限 / 网络成员关系，而不是先被归属校验挡掉。
    _owner_of: dict[str, str] = {
        # alice 发布
        **{rid: "alice" for rid in (
            "agent-v1", "agent-v3", "agent-v4", "agent-v6",
            "agent-cat", "agent-detail", "agent-revoke", "agent-suspended",
            "tool-v1", "tool-inst", "tool-idem", "tool-secret", "tool-uninst",
            "tool-other", "tool-gone", "tool-private-list",
        )},
        # bob 发布
        "tool-v2": "bob",
        "tool-v3": "bob",
        "agent-v5": "bob",
        # carol 发布（验证"非网络成员" → 404）
        "agent-v2": "carol",
    }
    for rid, who in _owner_of.items():
        rtype = "tool" if rid.startswith("tool") else "agent"
        await release_space(rtype, rid, owner=str(users[who].id))

    # 三个客户端各注入一个有效会话（这些用户是直接写库建的，没有密码）
    async with new_client() as alice, new_client() as bob, new_client() as carol:
        for client, name in ((alice, "alice"), (bob, "bob"), (carol, "carol")):
            await _login_via_session(db_session, client, users[name])

        yield World(
            alice=alice,
            bob=bob,
            carol=carol,
            network_id=str(network.id),
            tenant_id=str(tenant.id),
        )


async def _login_via_session(
    db_session: AsyncSession, client: AsyncClient, user: User
) -> None:
    """给"直接写库建出来的用户"签一个会话，并把令牌注入客户端 Cookie。

    为什么不走 ``/api/auth/login``：这些用户不是通过注册创建的，没有密码。
    而走真实注册再登录会消耗**注册限流（5 次/小时/IP）** —— 一个测试文件跑
    十几个用例会被打穿。

    这里只跳过**认证**这一步；被测的授权逻辑完全不受影响。
    """
    from config.settings import settings
    from core.auth.service import issue_session, plaintext_token_of

    user_session = await issue_session(
        db_session, user=user, user_agent="sharing-test", ip="127.0.0.1"
    )
    await db_session.commit()
    client.cookies.set(settings.auth.cookie_name, plaintext_token_of(user_session))


# ── 发布 ──────────────────────────────────────────────────────────────


def publish_url(resource_type: str, release_id: str) -> str:
    return f"/api/v1/{resource_type}-releases/{release_id}/publications"


class TestPublish:
    async def test_owner_publishes_to_network(self, world: World) -> None:
        resp = await world.alice.post(
            publish_url("agent", "agent-v1"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["resource_type"] == "agent"
        assert body["release_id"] == "agent-v1"
        assert body["scope"] == "network"
        assert body["status"] == "active"

    async def test_publish_is_idempotent(self, world: World) -> None:
        """重复发布返回**同一条**记录，不是 409，也不是新行。"""
        first = await world.alice.post(
            publish_url("tool", "tool-v1"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        second = await world.alice.post(
            publish_url("tool", "tool-v1"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

    async def test_member_cannot_publish_by_default_policy(
        self, world: World
    ) -> None:
        """member 的发布权受 ``policy.allowMemberPublish`` 约束（契约 §5.2）。"""
        resp = await world.bob.post(
            publish_url("tool", "tool-v2"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        assert resp.status_code == 403

    async def test_member_can_publish_when_policy_allows(self, world: World) -> None:
        allow = await world.alice.patch(
            f"/api/v1/networks/{world.network_id}",
            json={"policy": {"allowMemberPublish": True}},
        )
        assert allow.status_code == 200, allow.text

        resp = await world.bob.post(
            publish_url("tool", "tool-v3"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        assert resp.status_code == 201, resp.text

    async def test_non_member_cannot_publish(self, world: World) -> None:
        """Carol 同租户但不是网络成员 → 404（不泄露网络存在性）。"""
        resp = await world.carol.post(
            publish_url("agent", "agent-v2"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        assert resp.status_code == 404

    async def test_network_scope_requires_scope_id(self, world: World) -> None:
        resp = await world.alice.post(
            publish_url("agent", "agent-v3"), json={"scope": "network"}
        )
        assert resp.status_code == 400

    async def test_private_scope_rejects_scope_id(self, world: World) -> None:
        resp = await world.alice.post(
            publish_url("agent", "agent-v4"),
            json={"scope": "private", "scopeId": world.network_id},
        )
        assert resp.status_code == 400

    async def test_member_cannot_publish_to_whole_tenant(self, world: World) -> None:
        resp = await world.bob.post(
            publish_url("agent", "agent-v5"),
            json={"scope": "tenant", "scopeId": world.tenant_id},
        )
        assert resp.status_code == 403

    async def test_owner_cannot_publish_to_other_tenant(self, world: World) -> None:
        """只能发布到自己的激活租户 —— 不能替别的租户决定。"""
        resp = await world.alice.post(
            publish_url("agent", "agent-v6"),
            json={"scope": "tenant", "scopeId": str(uuid.uuid4())},
        )
        assert resp.status_code == 403

    async def test_unknown_resource_type_is_404(self, world: World) -> None:
        resp = await world.alice.post(
            "/api/v1/workflow-releases/x/publications",
            json={"scope": "network", "scopeId": world.network_id},
        )
        assert resp.status_code == 404


# ── 网络资源目录 ──────────────────────────────────────────────────────


class TestCatalog:
    async def test_member_sees_published_resource(self, world: World) -> None:
        await world.alice.post(
            publish_url("agent", "agent-cat"),
            json={"scope": "network", "scopeId": world.network_id},
        )

        resp = await world.bob.get(CATALOG, params={"networkId": world.network_id})
        assert resp.status_code == 200
        assert [p["release_id"] for p in resp.json()] == ["agent-cat"]

    async def test_non_member_cannot_list_catalog(self, world: World) -> None:
        resp = await world.carol.get(CATALOG, params={"networkId": world.network_id})
        assert resp.status_code == 404

    async def test_catalog_detail_reuses_authorization(self, world: World) -> None:
        """详情接口必须与列表**同一个授权** —— 否则猜 release_id 就绕过过滤。"""
        await world.alice.post(
            publish_url("agent", "agent-detail"),
            json={"scope": "network", "scopeId": world.network_id},
        )

        ok = await world.bob.get(
            f"{CATALOG}/agent/agent-detail", params={"networkId": world.network_id}
        )
        assert ok.status_code == 200

        denied = await world.carol.get(
            f"{CATALOG}/agent/agent-detail", params={"networkId": world.network_id}
        )
        assert denied.status_code == 404, "非成员猜 release_id 也不能看到"

    async def test_revoked_publication_disappears_from_catalog(
        self, world: World
    ) -> None:
        created = await world.alice.post(
            publish_url("agent", "agent-revoke"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        pub_id = created.json()["id"]

        revoked = await world.alice.delete(f"/api/v1/publications/{pub_id}")
        assert revoked.status_code == 204

        listed = await world.bob.get(CATALOG, params={"networkId": world.network_id})
        assert listed.json() == []

        gone = await world.bob.get(
            f"{CATALOG}/agent/agent-revoke", params={"networkId": world.network_id}
        )
        assert gone.status_code == 404

    async def test_suspended_network_blocks_publishing(self, world: World) -> None:
        await world.alice.patch(
            f"/api/v1/networks/{world.network_id}", json={"status": "suspended"}
        )
        resp = await world.alice.post(
            publish_url("agent", "agent-suspended"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        assert resp.status_code == 409


# ── 安装 ──────────────────────────────────────────────────────────────


def install_url(resource_type: str, release_id: str, network_id: str) -> str:
    return (
        f"/api/v1/{resource_type}-releases/{release_id}/installations"
        f"?networkId={network_id}"
    )


class TestInstall:
    async def test_install_and_list(self, world: World) -> None:
        await world.alice.post(
            publish_url("tool", "tool-inst"),
            json={"scope": "network", "scopeId": world.network_id},
        )

        resp = await world.bob.post(install_url("tool", "tool-inst", world.network_id))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "active"
        assert body["release_id"] == "tool-inst"

        mine = await world.bob.get("/api/v1/me/installations")
        assert mine.status_code == 200
        assert [i["release_id"] for i in mine.json()] == ["tool-inst"]

    async def test_install_is_idempotent(self, world: World) -> None:
        await world.alice.post(
            publish_url("tool", "tool-idem"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        first = await world.bob.post(install_url("tool", "tool-idem", world.network_id))
        second = await world.bob.post(
            install_url("tool", "tool-idem", world.network_id)
        )
        assert first.json()["id"] == second.json()["id"]

        mine = await world.bob.get("/api/v1/me/installations")
        assert len(mine.json()) == 1

    async def test_non_member_cannot_install(self, world: World) -> None:
        await world.alice.post(
            publish_url("tool", "tool-secret"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        resp = await world.carol.post(
            install_url("tool", "tool-secret", world.network_id)
        )
        assert resp.status_code == 404

    async def test_uninstall(self, world: World) -> None:
        await world.alice.post(
            publish_url("tool", "tool-uninst"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        created = await world.bob.post(
            install_url("tool", "tool-uninst", world.network_id)
        )
        installation_id = created.json()["id"]

        removed = await world.bob.delete(f"/api/v1/installations/{installation_id}")
        assert removed.status_code == 204

        mine = await world.bob.get("/api/v1/me/installations")
        assert mine.json() == []

    async def test_cannot_uninstall_someone_elses(self, world: World) -> None:
        """Bob 装了东西，Alice（网络 Owner）也不能替他卸。

        卸载是"我的工作空间"的私有动作；要影响所有安装者应该去撤回 publication。
        """
        await world.alice.post(
            publish_url("tool", "tool-other"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        created = await world.bob.post(
            install_url("tool", "tool-other", world.network_id)
        )
        installation_id = created.json()["id"]

        resp = await world.alice.delete(f"/api/v1/installations/{installation_id}")
        assert resp.status_code == 404

    async def test_install_revoked_publication_is_404(self, world: World) -> None:
        created = await world.alice.post(
            publish_url("tool", "tool-gone"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        await world.alice.delete(f"/api/v1/publications/{created.json()['id']}")

        resp = await world.bob.post(install_url("tool", "tool-gone", world.network_id))
        assert resp.status_code == 404

    async def test_installations_are_private_to_installer(self, world: World) -> None:
        """Alice 发布的东西，Bob 装了，Alice 的安装列表里不应该出现。"""
        await world.alice.post(
            publish_url("tool", "tool-private-list"),
            json={"scope": "network", "scopeId": world.network_id},
        )
        await world.bob.post(
            install_url("tool", "tool-private-list", world.network_id)
        )

        alice_mine = await world.alice.get("/api/v1/me/installations")
        assert alice_mine.json() == []

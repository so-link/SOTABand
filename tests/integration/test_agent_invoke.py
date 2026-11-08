"""Agent 相互通信与撤权 —— PRD FR-N7 / FR-N8。

## 这个文件在验什么

FR-N7 的核心不是"能调用"，而是**三件事同时成立**：

1. **身份独立**：Agent 用工作负载凭证调用，**不能**用用户会话 Cookie；
2. **权限最小**：只能调用本网络**发布目录内**的目标（白名单），且角色要有
   ``network.agent.invoke``；
3. **可审计**：每次调用（含被拒绝的）都写 ``audit_event``，主体是安装实例。

FR-N8 的核心是**即时撤权**：卸载安装 / 撤回发布 / 移除成员 / 降级角色 / 暂停网络
之后，**下一次调用**就必须失败 —— 不需要等凭证过期。本文件逐条验证这五条路径。

## 前置搭建

沿用 ``test_sharing_api.py`` 的思路：直接写库铺好「一个租户、一个网络、
四个用户（owner/member/viewer/非成员）」，然后**全部通过 HTTP** 测调用链。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent.invoke_bus import (
    DELIVERY_OFFLINE,
    DELIVERY_OK,
    DELIVERY_TIMEOUT,
    InvocationBus,
    invocation_bus,
    set_invocation_bus,
)
from core.auth.service import normalize_email
from core.db.base import utcnow
from core.db.models import (
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    NETWORK_ROLE_VIEWER,
    TENANT_ROLE_MEMBER,
    Network,
    NetworkMembership,
    Tenant,
    TenantMembership,
    User,
)

CATALOG = "/api/v1/network-catalog"


@asynccontextmanager
async def new_client():
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def login_as(db_session: AsyncSession, client: AsyncClient, user: User) -> None:
    """给直接写库建出来的用户签一个会话并注入 Cookie（无密码，不走登录接口）。"""
    from config.settings import settings
    from core.auth.service import issue_session, plaintext_token_of

    user_session = await issue_session(
        db_session, user=user, user_agent="invoke-test", ip="127.0.0.1"
    )
    await db_session.commit()
    client.cookies.set(settings.auth.cookie_name, plaintext_token_of(user_session))


@dataclass
class World:
    alice: AsyncClient  # N1 owner（也是 N2 owner）
    bob: AsyncClient  # N1 member
    carol: AsyncClient  # N1 viewer
    dave: AsyncClient  # 同租户，非 N1 成员
    alice_user: User
    bob_user: User
    carol_user: User
    n1: str
    n2: str
    tenant_id: str


@pytest_asyncio.fixture
async def world(db_session: AsyncSession, release_space) -> World:
    tag = uuid.uuid4().hex[:8]

    tenant = Tenant(name=f"调用测试-{tag}", slug=f"invoke-{tag}")
    db_session.add(tenant)
    await db_session.flush()

    users: dict[str, User] = {}
    for name in ("alice", "bob", "carol", "dave"):
        user = User(
            email=normalize_email(f"{name}-{tag}@invoke.test"),
            display_name=name,
            status="active",
        )
        db_session.add(user)
        users[name] = user
    await db_session.flush()

    db_session.add_all(
        [
            TenantMembership(
                tenant_id=tenant.id, user_id=u.id, role="owner" if n == "alice" else TENANT_ROLE_MEMBER,
                status="active",
            )
            for n, u in users.items()
        ]
    )

    # 两个网络：N1 是主战场，N2 用来验跨网络越权
    n1 = Network(tenant_id=tenant.id, name=f"N1-{tag}", owner_id=users["alice"].id, status="active", policy={})
    n2 = Network(tenant_id=tenant.id, name=f"N2-{tag}", owner_id=users["alice"].id, status="active", policy={})
    db_session.add_all([n1, n2])
    await db_session.flush()

    roles = {
        "alice": NETWORK_ROLE_OWNER,
        "bob": NETWORK_ROLE_MEMBER,
        "carol": NETWORK_ROLE_VIEWER,
        # dave 故意不在任何网络里
    }
    db_session.add_all(
        [
            NetworkMembership(
                network_id=n1.id,
                user_id=users[name].id,
                role=role,
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            )
            for name, role in roles.items()
        ]
    )
    # N2 里只放 alice —— 用来验"用 N1 的凭证调 N2 被拒"。
    # ⚠️ 必须真的建成员关系：`owner_id` 只是创建者记录，权限来源是 membership。
    db_session.add(
        NetworkMembership(
            network_id=n2.id,
            user_id=users["alice"].id,
            role=NETWORK_ROLE_OWNER,
            status=NETWORK_MEMBERSHIP_ACTIVE,
            joined_at=utcnow(),
        )
    )
    await db_session.commit()

    # ── 合成 release（发布接口会校验资源归属，见管理员评审意见 2）──
    # 本文件所有 publish 均由 alice 发起，因此统一把合成 Agent 条目的 owner
    # 设为 alice；这样被校验的是"发布范围/网络成员"逻辑，而不是归属。
    for _rid in (
        "agent-a", "agent-b", "agent-c", "agent-target",
        "agent-bob-caller", "agent-alice-caller",
    ):
        await release_space("agent", _rid, owner=str(users["alice"].id))

    async with (
        new_client() as alice,
        new_client() as bob,
        new_client() as carol,
        new_client() as dave,
    ):
        for client, name in ((alice, "alice"), (bob, "bob"), (carol, "carol"), (dave, "dave")):
            await login_as(db_session, client, users[name])

        yield World(
            alice=alice,
            bob=bob,
            carol=carol,
            dave=dave,
            alice_user=users["alice"],
            bob_user=users["bob"],
            carol_user=users["carol"],
            n1=str(n1.id),
            n2=str(n2.id),
            tenant_id=str(tenant.id),
        )


# ── 便捷操作 ──────────────────────────────────────────────────────────


async def publish(
    client: AsyncClient, rtype: str, release_id: str, network_id: str
) -> str:
    resp = await client.post(
        f"/api/v1/{rtype}-releases/{release_id}/publications",
        json={"scope": "network", "scopeId": network_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def install(
    client: AsyncClient, rtype: str, release_id: str, network_id: str
) -> str:
    resp = await client.post(
        f"/api/v1/{rtype}-releases/{release_id}/installations?networkId={network_id}"
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def get_credential(client: AsyncClient, installation_id: str) -> dict:
    resp = await client.post(f"/api/v1/installations/{installation_id}/credential")
    assert resp.status_code == 200, resp.text
    return resp.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def invoke(
    client: AsyncClient,
    installation_id: str,
    *,
    token: str | None,
    network_id: str,
    target_type: str = "agent",
    target_release_id: str = "agent-target",
) -> object:
    body = {
        "networkId": network_id,
        "targetResourceType": target_type,
        "targetReleaseId": target_release_id,
    }
    headers = bearer(token) if token else {}
    return await client.post(
        f"/api/v1/installations/{installation_id}/invoke", json=body, headers=headers
    )


# ══════════════════════════════════════════════════════════════════════
# 凭证签发
# ══════════════════════════════════════════════════════════════════════


class TestCredential:
    async def test_installer_can_issue(self, world: World) -> None:
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)

        cred = await get_credential(world.alice, inst)
        assert cred["token"].startswith("w1.")
        assert cred["installation_id"] == inst
        assert cred["network_id"] == world.n1
        assert cred["expires_in"] > 0

    async def test_cannot_issue_for_someone_elses_installation(
        self, world: World
    ) -> None:
        """别人拿不到你安装的 Agent 的凭证 —— 否则等于把工作负载身份借出去。"""
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)

        resp = await world.bob.post(f"/api/v1/installations/{inst}/credential")
        assert resp.status_code == 404

    async def test_cannot_issue_after_uninstall(self, world: World) -> None:
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)

        removed = await world.alice.delete(f"/api/v1/installations/{inst}")
        assert removed.status_code == 204

        resp = await world.alice.post(f"/api/v1/installations/{inst}/credential")
        assert resp.status_code == 409

    async def test_cannot_issue_for_non_network_publication(
        self, world: World, db_session: AsyncSession
    ) -> None:
        """只有发布到网络的资源才能签发 —— Agent 通信只在网络内（PRD FR-N7）。

        安装接口只接受走网络目录的安装，所以这里直接写库造一条
        「private 发布 + 安装」的记录，再验证签发被拒。
        """
        from core.db.models import (
            PUBLICATION_ACTIVE,
            PUBLICATION_SCOPE_PRIVATE,
            Installation,
            Publication,
        )

        pub = Publication(
            resource_type="agent",
            release_id="agent-private",
            scope=PUBLICATION_SCOPE_PRIVATE,
            scope_id=None,
            published_by=world.alice_user.id,
            status=PUBLICATION_ACTIVE,
        )
        db_session.add(pub)
        await db_session.flush()

        inst_row = Installation(
            tenant_id=uuid.UUID(world.tenant_id),
            publication_id=pub.id,
            installed_by=world.alice_user.id,
        )
        db_session.add(inst_row)
        await db_session.commit()

        resp = await world.alice.post(
            f"/api/v1/installations/{inst_row.id}/credential"
        )
        assert resp.status_code == 400, resp.text
        assert "网络" in resp.json()["detail"]


# ══════════════════════════════════════════════════════════════════════
# 工作负载身份认证（FR-N7 第 1 条）
# ══════════════════════════════════════════════════════════════════════


class TestWorkloadIdentity:
    async def test_user_cookie_cannot_invoke(self, world: World) -> None:
        """**核心断言**：Agent 调用必须用工作负载凭证，不能用用户会话 Cookie。

        这正是 PRD FR-N7 要解决的问题 —— 否则审计里分不清是用户还是他装的 Agent，
        而且 Agent 会继承用户的全部权限。
        """
        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)

        # 完全不写 Authorization，只有 Cookie
        resp = await invoke(
            world.alice, inst, token=None, network_id=world.n1
        )
        assert resp.status_code == 401, resp.text
        assert "工作负载" in resp.json()["detail"]

    async def test_missing_token_rejected(self, world: World) -> None:
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)

        resp = await invoke(world.alice, inst, token="", network_id=world.n1)
        assert resp.status_code == 401

    async def test_forged_signature_rejected(self, world: World) -> None:
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        # 改掉签名的最后一个字符
        tampered = cred["token"][:-1] + ("0" if cred["token"][-1] != "0" else "1")
        resp = await invoke(world.alice, inst, token=tampered, network_id=world.n1)
        assert resp.status_code == 401

    async def test_tampered_installation_id_rejected(self, world: World) -> None:
        """把令牌里的 installation_id 换成别的 —— 签名会失配。"""
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        parts = cred["token"].split(".")
        parts[1] = str(uuid.uuid4())
        resp = await invoke(
            world.alice, inst, token=".".join(parts), network_id=world.n1
        )
        assert resp.status_code == 401

    async def test_token_of_other_installation_rejected(self, world: World) -> None:
        """令牌有效，但不属于路径里的安装 → 403（不能拿自己凭证替别人调用）。"""
        await publish(world.alice, "agent", "agent-a", world.n1)
        await publish(world.alice, "agent", "agent-b", world.n1)
        inst_a = await install(world.alice, "agent", "agent-a", world.n1)
        inst_b = await install(world.alice, "agent", "agent-b", world.n1)

        cred_a = await get_credential(world.alice, inst_a)
        resp = await invoke(
            world.alice, inst_b, token=cred_a["token"], network_id=world.n1
        )
        assert resp.status_code == 403

    async def test_expired_token_rejected(self, world: World) -> None:
        """过期令牌 → 401。

        用真实签名密钥手动签一个 ``exp`` 在过去的令牌 —— 比 sleep 一分钟可靠得多，
        而且能顺带证明"签名对但过期"这条分支被单独拦住了。
        """
        import hashlib
        import hmac

        from core.auth import workload as workload_mod

        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)

        expired_at = int(utcnow().timestamp()) - 10
        jti = "expired-jti"
        payload = f"{inst}.{expired_at}.{jti}"
        sig = hmac.new(
            workload_mod._secret(), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        resp = await invoke(
            world.alice,
            inst,
            token=f"w1.{payload}.{sig}",
            network_id=world.n1,
        )
        assert resp.status_code == 401
        assert "过期" in resp.json()["detail"]


# ══════════════════════════════════════════════════════════════════════
# 授权链（FR-N7 第 2 条）
# ══════════════════════════════════════════════════════════════════════


class TestInvokeAuthorization:
    async def test_happy_path(self, world: World) -> None:
        pub_target = await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        resp = await invoke(
            world.alice, inst, token=cred["token"], network_id=world.n1
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["allowed"] is True
        assert body["target_publication_id"] == pub_target
        assert body["audit_event_id"], "放行必须留审计"

    async def test_target_outside_whitelist_rejected(self, world: World) -> None:
        """目标没有发布到该网络 → 不在白名单 → 404。"""
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        resp = await invoke(
            world.alice,
            inst,
            token=cred["token"],
            network_id=world.n1,
            target_release_id="never-published",
        )
        assert resp.status_code == 404

    async def test_cross_network_invocation_rejected(self, world: World) -> None:
        """**网络隔离**：用 N1 装的 Agent 的凭证去调 N2 的资源必须被拒。"""
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        # N2 里也发布同名目标，让"目标存在"这一点成立 —— 拒绝的原因只能是跨网络
        await publish(world.alice, "agent", "agent-target", world.n2)

        resp = await invoke(
            world.alice, inst, token=cred["token"], network_id=world.n2
        )
        assert resp.status_code == 403
        assert "跨网络" in resp.json()["detail"] or "不属于该网络" in resp.json()["detail"]

    async def test_viewer_denied_by_default_policy(self, world: World) -> None:
        """viewer 默认没有 network.agent.invoke（契约 §5.2 真值表）。"""
        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-c", world.n1)
        inst = await install(world.carol, "agent", "agent-c", world.n1)
        cred = await get_credential(world.carol, inst)

        resp = await invoke(
            world.carol, inst, token=cred["token"], network_id=world.n1
        )
        assert resp.status_code == 403

    async def test_viewer_allowed_when_policy_on(self, world: World) -> None:
        """``policy.allowViewerInvoke=true`` 时 viewer 可以调用。"""
        allow = await world.alice.patch(
            f"/api/v1/networks/{world.n1}",
            json={"policy": {"allowViewerInvoke": True}},
        )
        assert allow.status_code == 200, allow.text

        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-c", world.n1)
        inst = await install(world.carol, "agent", "agent-c", world.n1)
        cred = await get_credential(world.carol, inst)

        resp = await invoke(
            world.carol, inst, token=cred["token"], network_id=world.n1
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize("status", ["suspended", "archived"])
    async def test_inactive_network_blocks_invocation(
        self, world: World, status: str
    ) -> None:
        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        patched = await world.alice.patch(
            f"/api/v1/networks/{world.n1}", json={"status": status}
        )
        assert patched.status_code == 200, patched.text

        resp = await invoke(
            world.alice, inst, token=cred["token"], network_id=world.n1
        )
        assert resp.status_code == 409


# ══════════════════════════════════════════════════════════════════════
# 撤权（FR-N8）—— 每一条都必须是「下一次调用」就失效
# ══════════════════════════════════════════════════════════════════════


class TestRevocation:
    """FR-N8 的四条撤权路径 + 凭证过期。

    **每条都不重新签发凭证**，直接复用调用前拿到的 token —— 这才能证明
    "不需要等凭证过期，下一次调用就失效"。
    """

    async def _setup(self, world: World) -> tuple[str, str]:
        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)
        return inst, cred["token"]

    async def test_uninstall_revokes_immediately(self, world: World) -> None:
        inst, token = await self._setup(world)
        # 先确认能用
        ok = await invoke(world.alice, inst, token=token, network_id=world.n1)
        assert ok.status_code == 200

        await world.alice.delete(f"/api/v1/installations/{inst}")

        after = await invoke(world.alice, inst, token=token, network_id=world.n1)
        assert after.status_code == 404, "卸载后下一次调用必须失败"

    async def test_revoking_caller_publication_revokes_immediately(
        self, world: World
    ) -> None:
        inst, token = await self._setup(world)

        # 撤回调用方来源 publication
        catalog = (
            await world.alice.get(CATALOG, params={"networkId": world.n1})
        ).json()
        caller_pub = next(p for p in catalog if p["release_id"] == "agent-a")
        await world.alice.delete(f"/api/v1/publications/{caller_pub['id']}")

        after = await invoke(world.alice, inst, token=token, network_id=world.n1)
        assert after.status_code == 404

    async def test_revoking_target_publication_revokes_immediately(
        self, world: World
    ) -> None:
        inst, token = await self._setup(world)

        catalog = (
            await world.alice.get(CATALOG, params={"networkId": world.n1})
        ).json()
        target_pub = next(p for p in catalog if p["release_id"] == "agent-target")
        await world.alice.delete(f"/api/v1/publications/{target_pub['id']}")

        after = await invoke(world.alice, inst, token=token, network_id=world.n1)
        assert after.status_code == 404

    async def test_removing_member_revokes_their_agents(self, world: World) -> None:
        """成员被移除后，他装的 Agent 立刻失去调用能力（PRD 原话：SLA 内被拒）。"""
        # 让 bob 装一个、拿凭证
        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-b", world.n1)
        inst = await install(world.bob, "agent", "agent-b", world.n1)
        cred = await get_credential(world.bob, inst)

        ok = await invoke(world.bob, inst, token=cred["token"], network_id=world.n1)
        assert ok.status_code == 200

        removed = await world.alice.delete(
            f"/api/v1/networks/{world.n1}/members/{world.bob_user.id}"
        )
        assert removed.status_code == 204

        after = await invoke(
            world.bob, inst, token=cred["token"], network_id=world.n1
        )
        assert after.status_code == 403

    async def test_demoting_member_revokes_admin_only_powers(
        self, world: World
    ) -> None:
        """把 member 降成 viewer → 下一次调用因权限不足被拒。"""
        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-b", world.n1)
        inst = await install(world.bob, "agent", "agent-b", world.n1)
        cred = await get_credential(world.bob, inst)

        ok = await invoke(world.bob, inst, token=cred["token"], network_id=world.n1)
        assert ok.status_code == 200

        changed = await world.alice.patch(
            f"/api/v1/networks/{world.n1}/members/{world.bob_user.id}",
            json={"role": "viewer"},
        )
        assert changed.status_code == 200, changed.text

        after = await invoke(
            world.bob, inst, token=cred["token"], network_id=world.n1
        )
        assert after.status_code == 403


# ══════════════════════════════════════════════════════════════════════
# 审计（FR-N7「可审计」+ FR-N9）
# ══════════════════════════════════════════════════════════════════════


class TestInvocationAudit:
    async def test_successful_call_is_audited_as_workload(
        self, world: World, db_session: AsyncSession
    ) -> None:

        from core.db.models import AuditEvent

        await publish(world.alice, "agent", "agent-target", world.n1)
        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        resp = await invoke(world.alice, inst, token=cred["token"], network_id=world.n1)
        assert resp.status_code == 200
        audit_id = resp.json()["audit_event_id"]

        event = await db_session.get(AuditEvent, uuid.UUID(audit_id))
        assert event is not None
        assert event.action == "network.agent.invoke"
        assert event.actor_kind == "workload", "主体必须是工作负载，不是 user"
        assert event.actor_id == inst, "主体 id 必须是安装实例 id"
        assert event.result == "success"
        assert event.target_id == "agent-target"
        assert str(event.network_id) == world.n1

    async def test_denied_call_is_also_audited(
        self, world: World, db_session: AsyncSession
    ) -> None:
        """**被拒绝的调用更要留痕** —— 它是识别越权尝试的唯一依据。"""
        from sqlalchemy import select

        from core.db.models import AuditEvent

        await publish(world.alice, "agent", "agent-a", world.n1)
        inst = await install(world.alice, "agent", "agent-a", world.n1)
        cred = await get_credential(world.alice, inst)

        resp = await invoke(
            world.alice,
            inst,
            token=cred["token"],
            network_id=world.n1,
            target_release_id="not-in-catalog",
        )
        assert resp.status_code == 404

        rows = (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "network.agent.invoke",
                    AuditEvent.result == "denied",
                    AuditEvent.actor_id == inst,
                )
            )
        ).scalars().all()
        assert rows, "被拒绝的调用必须写审计"
        assert rows[-1].actor_kind == "workload"


# ══════════════════════════════════════════════════════════════════════
# FR-N7 三项遗留：按目标白名单 / 速率限制 / 消息总线接线
# ══════════════════════════════════════════════════════════════════════
#
# 批次 5 如实记录了三个简化，这里逐个补上并锁定：
#
# 1. **白名单粒度**：以前只要目标发布到本网络，任何有 invoke 权限的成员都能调。
#    现在发布者可以按目标设置 `allowlist`，只有点名的人能调。
# 2. **速率限制**：白名单解决"能不能调"，限流解决"能调多快"。一个跑飞的 Agent
#    会把目标打到过载，而目标在白名单放行后无法拒绝。
# 3. **消息总线接线**：以前 /invoke 只返回授权判定，不投递。现在授权通过后
#    真正把调用送到目标 Agent 并等待响应；离线/超时不算错误。


async def set_policy(
    client: AsyncClient,
    publication_id: str,
    *,
    policy: str,
    allowlist: list[str] | None = None,
) -> object:
    return await client.patch(
        f"/api/v1/publications/{publication_id}/invoke-policy",
        json={"invokePolicy": policy, "invokeAllowlist": allowlist or []},
    )


async def _bob_caller(world: World) -> tuple[str, str]:
    """让 bob 拥有一个调用方 Agent 的**安装**，返回 (installation_id, token)。

    ⚠️ 发布动作由 alice 做 —— bob 是普通成员，默认策略下没有
    ``network.resource.publish`` 权限。但**安装**由 bob 自己做，所以
    ``installation.installed_by == bob``，白名单判定看的就是这个字段。
    """
    await publish(world.alice, "agent", "agent-bob-caller", world.n1)
    inst_id = await install(world.bob, "agent", "agent-bob-caller", world.n1)
    cred = await get_credential(world.bob, inst_id)
    return inst_id, cred["token"]


class TestInvokeAllowlist:
    """按目标粒度的调用方白名单（FR-N7 遗留项 1）。"""

    async def test_default_policy_is_network_wide(self, world: World) -> None:
        """默认策略 network：网络内有 invoke 权限的成员都能调（向后兼容）。"""
        target_pub = await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        detail = (
            await world.alice.get(f"/api/v1/network-catalog/{target_pub}")
        )
        # 目录项里能读到默认策略 —— 前端要能展示"当前谁能调"
        if detail.status_code == 200:
            assert detail.json().get("invoke_policy", "network") == "network"

        resp = await invoke(
            world.bob, inst_id, token=token, network_id=world.n1
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["allowed"] is True

    async def test_allowlist_blocks_unlisted_member(self, world: World) -> None:
        """核心用例：发布者把白名单设成只有 alice，bob 立刻被拒。"""
        target_pub = await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        # 先确认 bob 本来能调
        ok = await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        assert ok.status_code == 200

        # alice 把白名单收窄到只有自己
        resp = await set_policy(
            world.alice,
            target_pub,
            policy="allowlist",
            allowlist=[str(world.alice_user.id)],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["invoke_policy"] == "allowlist"
        assert resp.json()["invoke_allowlist"] == [str(world.alice_user.id)]

        denied = await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        assert denied.status_code == 403, denied.text
        assert "白名单" in denied.json()["detail"]

    async def test_adding_caller_to_allowlist_restores_access(
        self, world: World
    ) -> None:
        """把 bob 加进名单后立刻恢复 —— 没有缓存，下一次调用即生效。"""
        target_pub = await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        await set_policy(
            world.alice,
            target_pub,
            policy="allowlist",
            allowlist=[str(world.alice_user.id)],
        )
        assert (
            await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        ).status_code == 403

        await set_policy(
            world.alice,
            target_pub,
            policy="allowlist",
            allowlist=[str(world.alice_user.id), str(world.bob_user.id)],
        )
        assert (
            await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        ).status_code == 200

    async def test_empty_allowlist_rejected(self, world: World) -> None:
        """空名单 = 谁都调不了，这是配置错误，必须明确报错而不是默默接受。"""
        target_pub = await publish(world.alice, "agent", "agent-target", world.n1)
        resp = await set_policy(world.alice, target_pub, policy="allowlist", allowlist=[])
        assert resp.status_code == 400
        assert "不能为空" in resp.json()["detail"]

    async def test_non_member_in_allowlist_rejected(self, world: World) -> None:
        """dave 同租户但不是 N1 成员 —— 不能把非成员塞进白名单。"""
        target_pub = await publish(world.alice, "agent", "agent-target", world.n1)
        resp = await set_policy(
            world.alice,
            target_pub,
            policy="allowlist",
            allowlist=[str(world.alice_user.id), str(uuid.uuid4())],
        )
        assert resp.status_code == 400
        assert "不是该网络的成员" in resp.json()["detail"]

    async def test_invalid_policy_rejected(self, world: World) -> None:
        target_pub = await publish(world.alice, "agent", "agent-target", world.n1)
        resp = await set_policy(world.alice, target_pub, policy="nobody")
        assert resp.status_code == 400

    async def test_member_cannot_change_others_policy(self, world: World) -> None:
        """bob 是普通成员，不能改 alice 发布的资源的调用权限。"""
        target_pub = await publish(world.alice, "agent", "agent-target", world.n1)
        resp = await set_policy(
            world.bob,
            target_pub,
            policy="allowlist",
            allowlist=[str(world.bob_user.id)],
        )
        assert resp.status_code == 403


class TestInvokeRateLimit:
    """调用速率限制（FR-N7 遗留项 2）—— 防跑飞的 Agent 打爆目标。"""

    async def test_exceeding_rate_limit_returns_429(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from config.settings import settings
        from core.auth.ratelimit import reset_all_limiters

        # 把窗口压到 2 次，便于构造超限
        monkeypatch.setattr(settings.workload, "invoke_max_per_window", 2)
        reset_all_limiters()

        await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        statuses = []
        for _ in range(4):
            resp = await invoke(world.bob, inst_id, token=token, network_id=world.n1)
            statuses.append(resp.status_code)

        assert 429 in statuses, f"超过速率上限应返回 429，实际：{statuses}"
        # 前两次应成功放行
        assert statuses[0] == 200
        assert statuses[1] == 200

    async def test_rate_limit_is_per_installation(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """限流按安装实例隔离：bob 被限流不影响 alice 的安装。"""
        from config.settings import settings
        from core.auth.ratelimit import reset_all_limiters

        monkeypatch.setattr(settings.workload, "invoke_max_per_window", 1)
        reset_all_limiters()

        await publish(world.alice, "agent", "agent-target", world.n1)

        bob_inst, bob_token = await _bob_caller(world)
        alice_pub = await publish(world.alice, "agent", "agent-alice-caller", world.n1)
        alice_inst = await install(world.alice, "agent", "agent-alice-caller", world.n1)
        alice_token = (await get_credential(world.alice, alice_inst))["token"]
        assert alice_pub

        # bob 打满
        assert (await invoke(world.bob, bob_inst, token=bob_token, network_id=world.n1)).status_code == 200
        assert (await invoke(world.bob, bob_inst, token=bob_token, network_id=world.n1)).status_code == 429

        # alice 不受影响
        assert (
            await invoke(world.alice, alice_inst, token=alice_token, network_id=world.n1)
        ).status_code == 200


class TestInvocationBus:
    """消息总线接线（FR-N7 遗留项 3）—— 授权通过后真正投递。"""

    async def test_offline_target_reports_offline_not_error(self, world: World) -> None:
        """目标没上线：授权成功但 delivered=false。**不能报错** —— 重试即可。"""
        set_invocation_bus(InvocationBus())
        await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        resp = await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["allowed"] is True
        assert body["delivered"] is False
        assert body["delivery_status"] == DELIVERY_OFFLINE
        assert body["audit_event_id"]

    async def test_online_target_receives_payload_and_replies(
        self, world: World
    ) -> None:
        """目标上线后，调用真正送达并拿到响应体。"""
        set_invocation_bus(InvocationBus())
        await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        received: list[dict] = []

        async def handler(payload: dict) -> dict:
            received.append(payload)
            return {"echo": payload.get("text", ""), "by": "target-agent"}

        await invocation_bus().register_target("agent", "agent-target", handler)

        resp = await invoke(
            world.bob, inst_id, token=token, network_id=world.n1
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["delivered"] is True
        assert body["delivery_status"] == DELIVERY_OK
        assert body["response"] == {"echo": "", "by": "target-agent"}

    async def test_payload_round_trip(self, world: World) -> None:
        set_invocation_bus(InvocationBus())
        await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        async def handler(payload: dict) -> dict:
            return {"doubled": payload.get("n", 0) * 2}

        await invocation_bus().register_target("agent", "agent-target", handler)

        resp = await world.bob.post(
            f"/api/v1/installations/{inst_id}/invoke",
            json={
                "networkId": world.n1,
                "targetResourceType": "agent",
                "targetReleaseId": "agent-target",
                "payload": {"n": 21},
            },
            headers=bearer(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["response"] == {"doubled": 42}

    async def test_handler_exception_is_reported_to_caller(
        self, world: World
    ) -> None:
        """目标 Agent 内部抛异常：调用方拿到错误信息，而不是 500。"""
        set_invocation_bus(InvocationBus())
        await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        async def boom(_payload: dict) -> dict:
            raise RuntimeError("内部炸了")

        await invocation_bus().register_target("agent", "agent-target", boom)

        resp = await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["delivered"] is True
        assert "内部炸了" in body["response"]["error"]

    async def test_timeout_is_reported(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        monkeypatch.setenv("SOTABAND_INVOKE_DELIVERY_TIMEOUT", "0.05")
        set_invocation_bus(InvocationBus())
        await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        async def slow(_payload: dict) -> dict:
            await asyncio.sleep(0.5)
            return {"late": True}

        await invocation_bus().register_target("agent", "agent-target", slow)

        resp = await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["delivered"] is False
        assert body["delivery_status"] == DELIVERY_TIMEOUT

    async def test_unregister_makes_target_offline_again(self, world: World) -> None:
        set_invocation_bus(InvocationBus())
        await publish(world.alice, "agent", "agent-target", world.n1)
        inst_id, token = await _bob_caller(world)

        async def handler(_payload: dict) -> dict:
            return {"ok": True}

        await invocation_bus().register_target("agent", "agent-target", handler)
        assert (
            await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        ).json()["delivered"] is True

        await invocation_bus().unregister_target("agent", "agent-target")
        assert (
            await invoke(world.bob, inst_id, token=token, network_id=world.n1)
        ).json()["delivery_status"] == DELIVERY_OFFLINE


@pytest.fixture(autouse=True)
def _reset_invocation_bus():
    """每个用例后清空总线，避免订阅者串到下个用例。"""
    yield
    set_invocation_bus(None)

"""共享链路安全加固回归 —— PR #5 独立终审（发布/安装/调用链）。

覆盖终审实测复现的六个问题（服务层直测，不依赖 registry 文件）：

1. **撤回权限**：``policy.allowMemberPublish=true`` 后普通 member 也能下架他人发布
   （旧实现复用 ``network.resource.publish``）→ 必须走 ``network.update``，
   无权限统一 404；
2. **发布幂等不可复活他人发布**：命中别人（已撤回）的 publication 时不能
   ``status=active`` 把它复活、署名仍是被害者；
3. **private 发布不跨用户泄露**：命中别人的 private 发布必须 404，
   响应/异常里不得出现对方的 publication id / user id；
4. **tool/agent 代发布**：注册恒写 ``visibility=public`` 且无修改接口，
   "public 即允许"实际是"任何登录用户都能代发" → 要求租户 Owner/Admin；
5. **资源删除后的 publication 悬空**：删除 tool/agent 必须同时撤发布
   （``revoke_publications_for_release``）；
6. **凭证/调用链的成员状态**：被移出网络后不能再签凭证；被移出**租户** /
   账号停用后，旧工作负载凭证在下一次调用即被拒并留 denied 审计。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.context import AuthContext
from core.auth.service import normalize_email
from core.db.base import utcnow
from core.db.models import (
    MEMBERSHIP_ACTIVE,
    MEMBERSHIP_REMOVED,
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    PUBLICATION_ACTIVE,
    PUBLICATION_REVOKED,
    PUBLICATION_SCOPE_NETWORK,
    PUBLICATION_SCOPE_PRIVATE,
    PUBLICATION_SCOPE_TENANT,
    RESULT_DENIED,
    TENANT_ROLE_ADMIN,
    TENANT_ROLE_MEMBER,
    TENANT_ROLE_OWNER,
    AuditEvent,
    Installation,
    Network,
    NetworkMembership,
    Publication,
    Tenant,
    TenantMembership,
    User,
)
from core.errors import ConflictError, NotFoundError
from core.sharing import service as sharing

REQUEST_ID = "sharing-hardening"


# ══════════════════════════════════════════════════════════════════════
# 脚手架
# ══════════════════════════════════════════════════════════════════════


@dataclass
class World:
    tenant: Tenant
    network: Network
    #: alice=租户 Owner；bob=普通 member；carol=租户 Admin；dave=网络 Admin
    users: dict[str, User]

    def ctx(self, name: str) -> AuthContext:
        user = self.users[name]
        tenant_role = {
            "alice": TENANT_ROLE_OWNER,
            "carol": TENANT_ROLE_ADMIN,
        }.get(name, TENANT_ROLE_MEMBER)
        return AuthContext(
            user_id=str(user.id),
            session_id=str(uuid.uuid4()),
            tenant_id=str(self.tenant.id),
            tenant_role=tenant_role,
            request_id=REQUEST_ID,
            email=user.email,
            display_name=user.display_name,
        )


@pytest.fixture()
async def world(db_session: AsyncSession) -> World:
    """一个租户 / 一个网络 / 四个用户。

    网络策略显式打开 ``allowMemberPublish``：这样"普通 member 不能撤回他人发布"
    验证的是撤回权限本身，而不是"他本来就没有发布权"。
    """
    tag = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"加固测试-{tag}", slug=f"hardening-{tag}")
    db_session.add(tenant)
    await db_session.flush()

    users: dict[str, User] = {}
    for name in ("alice", "bob", "carol", "dave"):
        user = User(
            email=normalize_email(f"{name}-{tag}@hardening.test"),
            display_name=name,
            status="active",
        )
        db_session.add(user)
        users[name] = user
    await db_session.flush()

    tenant_roles = {
        "alice": TENANT_ROLE_OWNER,
        "bob": TENANT_ROLE_MEMBER,
        "carol": TENANT_ROLE_ADMIN,
        "dave": TENANT_ROLE_MEMBER,
    }
    db_session.add_all(
        [
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                role=tenant_roles[name],
                status=MEMBERSHIP_ACTIVE,
            )
            for name, user in users.items()
        ]
    )

    network = Network(
        tenant_id=tenant.id,
        name=f"N-{tag}",
        owner_id=users["alice"].id,
        status="active",
        # allowViewerInvoke 保持 false：验证局部更新时它是"不该被顺手改掉"的那个
        policy={"allowMemberPublish": True, "allowViewerInvoke": False},
    )
    db_session.add(network)
    await db_session.flush()

    network_roles = {
        "alice": NETWORK_ROLE_OWNER,
        "bob": NETWORK_ROLE_MEMBER,
        "carol": NETWORK_ROLE_MEMBER,
        "dave": NETWORK_ROLE_ADMIN,
    }
    db_session.add_all(
        [
            NetworkMembership(
                network_id=network.id,
                user_id=users[name].id,
                role=role,
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            )
            for name, role in network_roles.items()
        ]
    )
    await db_session.commit()
    return World(tenant=tenant, network=network, users=users)


@pytest.fixture()
def entries(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], dict]:
    """用内存条目替掉 registry 查询。

    发布侧需要 ``_find_release_entry`` 返回资源条目（工具/Agent 本体不在库里）。
    直接写真实 ``resources-private`` 会污染仓库，这里按 id 精确喂条目。
    """
    table: dict[tuple[str, str], dict] = {}

    async def _fake_find(resource_type: str, release_id: str):
        return table.get((resource_type, release_id))

    monkeypatch.setattr(sharing, "_find_release_entry", _fake_find)
    return table


def _release(
    entries: dict[tuple[str, str], dict],
    resource_type: str,
    release_id: str,
    owner: User | None,
) -> None:
    """登记一个资源版本；``owner=None`` 表示"内置共享资产（无 owner）"。"""
    entries[(resource_type, release_id)] = {
        "id": release_id,
        "name": release_id,
        "owner": str(owner.id) if owner else "",
        "visibility": "public",
    }


async def _publish(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    resource_type: str,
    release_id: str,
    scope: str = PUBLICATION_SCOPE_NETWORK,
    scope_id: uuid.UUID | None = None,
) -> Publication:
    return await sharing.publish_resource(
        session,
        ctx,
        resource_type=resource_type,
        release_id=release_id,
        scope=scope,
        scope_id=scope_id,
        request_id=REQUEST_ID,
    )


async def _revoke(
    session: AsyncSession, ctx: AuthContext, publication_id: uuid.UUID
) -> Publication:
    return await sharing.revoke_publication(
        session, ctx, publication_id=publication_id, request_id=REQUEST_ID
    )


async def _count_publications(session: AsyncSession, *, release_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Publication)
                .where(Publication.release_id == release_id)
            )
        ).scalar_one()
    )


# ══════════════════════════════════════════════════════════════════════
# ④ 撤回权限：member 不能下架他人的发布
# ══════════════════════════════════════════════════════════════════════


class TestRevokePermission:
    async def test_member_cannot_revoke_others_publication(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """``allowMemberPublish=true`` 只给"发布"，不给"替别人下架"。"""
        _release(entries, "tool", "tool-alice", world.users["alice"])
        publication = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-alice",
            scope_id=world.network.id,
        )

        with pytest.raises(NotFoundError):
            await _revoke(db_session, world.ctx("bob"), publication.id)

        await db_session.refresh(publication)
        assert publication.status == PUBLICATION_ACTIVE

    async def test_network_admin_can_revoke_others_publication(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """兜底改走 ``network.update`` 后，网络 Owner/Admin 仍然可以下架。"""
        _release(entries, "tool", "tool-builtin", None)
        publication = await _publish(
            db_session,
            world.ctx("bob"),
            resource_type="tool",
            release_id="tool-builtin",
            scope_id=world.network.id,
        )

        revoked = await _revoke(db_session, world.ctx("dave"), publication.id)
        assert revoked.status == PUBLICATION_REVOKED

    async def test_publisher_can_still_revoke_own_publication(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        _release(entries, "tool", "tool-own", world.users["alice"])
        publication = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-own",
            scope_id=world.network.id,
        )
        assert (await _revoke(db_session, world.ctx("alice"), publication.id)).status == (
            PUBLICATION_REVOKED
        )


# ══════════════════════════════════════════════════════════════════════
# ⑤ 发布幂等：不能复活他人的发布 / private 不跨用户泄露
# ══════════════════════════════════════════════════════════════════════


class TestPublishIdempotencyOwnership:
    async def test_other_user_cannot_revive_revoked_network_publication(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """bob 重发 alice 已撤回的发布 → 409，且不得把行改回 active。"""
        _release(entries, "tool", "tool-revive", None)
        publication = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-revive",
            scope_id=world.network.id,
        )
        await _revoke(db_session, world.ctx("alice"), publication.id)

        with pytest.raises(ConflictError):
            await _publish(
                db_session,
                world.ctx("bob"),
                resource_type="tool",
                release_id="tool-revive",
                scope_id=world.network.id,
            )

        await db_session.refresh(publication)
        assert publication.status == PUBLICATION_REVOKED
        assert publication.published_by == world.users["alice"].id

    async def test_same_user_can_republish_own_publication(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """发布者本人重发仍是幂等的"重新激活"（既有行为不能回退）。"""
        _release(entries, "tool", "tool-idem", world.users["alice"])
        publication = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-idem",
            scope_id=world.network.id,
        )
        await _revoke(db_session, world.ctx("alice"), publication.id)

        republished = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-idem",
            scope_id=world.network.id,
        )
        assert republished.id == publication.id
        assert republished.status == PUBLICATION_ACTIVE

    async def test_private_publication_does_not_leak_to_other_user(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """carol 对同一版本再发 private：404，且不返回 alice 的 id。"""
        _release(entries, "tool", "tool-private", None)
        publication = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-private",
            scope=PUBLICATION_SCOPE_PRIVATE,
        )

        with pytest.raises(NotFoundError) as exc_info:
            await _publish(
                db_session,
                world.ctx("bob"),
                resource_type="tool",
                release_id="tool-private",
                scope=PUBLICATION_SCOPE_PRIVATE,
            )
        message = str(exc_info.value)
        assert str(publication.id) not in message
        assert str(world.users["alice"].id) not in message

        await db_session.refresh(publication)
        assert publication.status == PUBLICATION_ACTIVE
        assert publication.published_by == world.users["alice"].id
        # 也不能悄悄给 bob 建出第二行（部分唯一索引同样不允许）
        assert await _count_publications(db_session, release_id="tool-private") == 1


# ══════════════════════════════════════════════════════════════════════
# ⑥ tool / agent 代发布：需租户 Owner/Admin
# ══════════════════════════════════════════════════════════════════════


class TestPublishOthersSharedAsset:
    @pytest.mark.parametrize("resource_type", ["tool", "agent"])
    async def test_member_cannot_publish_others_shared_asset(
        self,
        db_session: AsyncSession,
        world: World,
        entries,
        resource_type: str,
    ) -> None:
        """注册恒写 visibility=public —— 光看 public 等于人人可代发。"""
        release_id = f"{resource_type}-alice"
        _release(entries, resource_type, release_id, world.users["alice"])

        with pytest.raises(NotFoundError):
            await _publish(
                db_session,
                world.ctx("bob"),
                resource_type=resource_type,
                release_id=release_id,
                scope_id=world.network.id,
            )

    @pytest.mark.parametrize("resource_type", ["tool", "agent"])
    async def test_tenant_admin_can_publish_others_shared_asset(
        self,
        db_session: AsyncSession,
        world: World,
        entries,
        resource_type: str,
    ) -> None:
        """租户 Owner/Admin 保留"代发布"能力（目录治理兜底）。"""
        release_id = f"{resource_type}-alice-admin"
        _release(entries, resource_type, release_id, world.users["alice"])

        publication = await _publish(
            db_session,
            world.ctx("carol"),
            resource_type=resource_type,
            release_id=release_id,
            scope_id=world.network.id,
        )
        assert publication.published_by == world.users["carol"].id

    async def test_builtin_asset_still_publishable_by_member(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """内置共享资产（无 owner）不受"代发布"限制 —— 否则仓库工具无法被发布。"""
        _release(entries, "tool", "tool-builtin-member", None)
        publication = await _publish(
            db_session,
            world.ctx("bob"),
            resource_type="tool",
            release_id="tool-builtin-member",
            scope_id=world.network.id,
        )
        assert publication.published_by == world.users["bob"].id


# ══════════════════════════════════════════════════════════════════════
# ⑦ 删除资源时撤回发布
# ══════════════════════════════════════════════════════════════════════


class TestRevokePublicationsForRelease:
    async def test_helper_revokes_all_active_publications(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        _release(entries, "tool", "tool-gone", world.users["alice"])
        network_pub = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-gone",
            scope_id=world.network.id,
        )
        tenant_pub = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-gone",
            scope=PUBLICATION_SCOPE_TENANT,
            scope_id=world.tenant.id,
        )

        revoked = await sharing.revoke_publications_for_release(
            db_session, "tool", "tool-gone"
        )
        assert revoked == 2
        await db_session.refresh(network_pub)
        await db_session.refresh(tenant_pub)
        assert network_pub.status == PUBLICATION_REVOKED
        assert tenant_pub.status == PUBLICATION_REVOKED

        # 幂等：再来一次没有任何 active 行可撤
        assert (
            await sharing.revoke_publications_for_release(db_session, "tool", "tool-gone")
            == 0
        )

        # 两条撤回答辩审计（FR-N9）
        audit_rows = (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == sharing.ACTION_REVOKE_PUBLICATION,
                    AuditEvent.target_id.in_([str(network_pub.id), str(tenant_pub.id)]),
                )
            )
        ).scalars().all()
        assert len(audit_rows) == 2

    async def test_delete_tool_endpoint_revokes_publication(
        self,
        db_session: AsyncSession,
        world: World,
        entries,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.routes import tool_routes

        _release(entries, "tool", "tool-delete", world.users["alice"])
        publication = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="tool",
            release_id="tool-delete",
            scope_id=world.network.id,
        )

        class _FakeToolRegistry:
            def __init__(self) -> None:
                self.unregistered: list[str] = []

            async def get(self, tool_id: str) -> dict:
                return {"id": tool_id, "name": tool_id, "owner": str(world.users["alice"].id)}

            def _get_def_dir(self):
                return tmp_path / "definitions"

            def _get_impl_dir(self):
                return tmp_path / "implementations"

            async def unregister(self, tool_id: str) -> None:
                self.unregistered.append(tool_id)

        fake = _FakeToolRegistry()
        monkeypatch.setattr(tool_routes, "registry", fake)

        result = await tool_routes.delete_tool(
            "tool-delete", ctx=world.ctx("alice"), session=db_session
        )
        assert fake.unregistered == ["tool-delete"]
        assert result["revoked_publications"] == 1

        await db_session.refresh(publication)
        assert publication.status == PUBLICATION_REVOKED

    async def test_delete_agent_endpoint_revokes_publication(
        self,
        db_session: AsyncSession,
        world: World,
        entries,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.routes import agent_routes

        _release(entries, "agent", "agent-delete", world.users["alice"])
        publication = await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="agent",
            release_id="agent-delete",
            scope_id=world.network.id,
        )

        class _FakeAgentRegistry:
            def __init__(self) -> None:
                self.unregistered: list[str] = []

            async def get(self, agent_id: str) -> dict:
                return {"id": agent_id, "name": agent_id, "owner": str(world.users["alice"].id)}

            def _get_spec_dir(self):
                return tmp_path / "specs"

            def _get_impl_dir(self):
                return tmp_path / "implementations"

            async def unregister(self, agent_id: str) -> None:
                self.unregistered.append(agent_id)

        fake = _FakeAgentRegistry()
        monkeypatch.setattr(agent_routes, "registry", fake)

        result = await agent_routes.delete_agent(
            "agent-delete", ctx=world.ctx("alice"), session=db_session
        )
        assert fake.unregistered == ["agent-delete"]
        assert result["revoked_publications"] == 1

        await db_session.refresh(publication)
        assert publication.status == PUBLICATION_REVOKED


# ══════════════════════════════════════════════════════════════════════
# ⑧ 凭证与调用链：成员状态必须现查
# ══════════════════════════════════════════════════════════════════════


async def _install_caller(
    session: AsyncSession, world: World, release_id: str, *, installer: str
) -> tuple[Publication, Installation]:
    publication = await _publish(
        session,
        world.ctx("alice"),
        resource_type="agent",
        release_id=release_id,
        scope_id=world.network.id,
    )
    installation = await sharing.install_publication(
        session, world.ctx(installer), publication_id=publication.id, request_id=REQUEST_ID
    )
    return publication, installation


class TestCredentialAndInvokeRevocation:
    async def test_cannot_issue_credential_after_removed_from_network(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """被移出网络后旧安装不能再换新凭证（旧实现仍能签发）。"""
        _release(entries, "agent", "agent-cred", world.users["alice"])
        _, installation = await _install_caller(
            db_session, world, "agent-cred", installer="alice"
        )
        ctx = world.ctx("alice")

        # 先确认正常情况下能签
        await sharing.issue_credential(
            db_session, ctx, installation_id=installation.id, request_id=REQUEST_ID
        )

        membership = (
            await db_session.execute(
                select(NetworkMembership).where(
                    NetworkMembership.network_id == world.network.id,
                    NetworkMembership.user_id == world.users["alice"].id,
                )
            )
        ).scalar_one()
        membership.status = "removed"
        await db_session.commit()

        with pytest.raises(NotFoundError):
            await sharing.issue_credential(
                db_session, ctx, installation_id=installation.id, request_id=REQUEST_ID
            )

    async def test_invoke_denied_after_removed_from_tenant(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """租户移出后：网络 membership 还 active，但调用链必须拒绝并留 denied 审计。"""
        _release(entries, "agent", "agent-caller", world.users["alice"])
        _release(entries, "agent", "agent-target", world.users["alice"])
        _, installation = await _install_caller(
            db_session, world, "agent-caller", installer="alice"
        )
        await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="agent",
            release_id="agent-target",
            scope_id=world.network.id,
        )

        tenant_membership = (
            await db_session.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == world.tenant.id,
                    TenantMembership.user_id == world.users["alice"].id,
                )
            )
        ).scalar_one()
        tenant_membership.status = MEMBERSHIP_REMOVED
        await db_session.commit()

        decision = await sharing.authorize_agent_invocation(
            db_session,
            caller_installation_id=installation.id,
            network_id=world.network.id,
            target_resource_type="agent",
            target_release_id="agent-target",
            request_id=REQUEST_ID,
        )
        assert decision.allowed is False
        assert decision.deny_kind == "forbidden"
        assert "租户" in (decision.reason or "")

        denied = (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == sharing.ACTION_AGENT_INVOKE,
                    AuditEvent.result == RESULT_DENIED,
                    AuditEvent.network_id == world.network.id,
                )
            )
        ).scalars().all()
        assert denied, "拒绝调用必须写 denied 审计"

    async def test_invoke_denied_for_disabled_user(
        self, db_session: AsyncSession, world: World, entries
    ) -> None:
        """账号停用后，旧工作负载凭证同样在下一次调用被拒。"""
        _release(entries, "agent", "agent-caller2", world.users["alice"])
        _release(entries, "agent", "agent-target2", world.users["alice"])
        _, installation = await _install_caller(
            db_session, world, "agent-caller2", installer="alice"
        )
        await _publish(
            db_session,
            world.ctx("alice"),
            resource_type="agent",
            release_id="agent-target2",
            scope_id=world.network.id,
        )

        user = world.users["alice"]
        user.status = "disabled"
        await db_session.commit()

        decision = await sharing.authorize_agent_invocation(
            db_session,
            caller_installation_id=installation.id,
            network_id=world.network.id,
            target_resource_type="agent",
            target_release_id="agent-target2",
            request_id=REQUEST_ID,
        )
        assert decision.allowed is False
        assert decision.deny_kind == "forbidden"
        assert "停用" in (decision.reason or "")

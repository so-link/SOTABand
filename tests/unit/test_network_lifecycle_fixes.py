"""网络生命周期 / 审计语义修复回归 —— PR #5 独立终审 ⑩。

覆盖四件事（都是"看起来成功、实际没生效"的静默错误）：

1. **过期邀请的状态必须落库**：旧代码 ``flush()`` 后 ``raise GoneError``，
   路由/依赖层回滚 → ``expired`` 永远写不进去，邀请一直显示 pending；
2. **移除成员 / 退出网络要吊销未用的 pending 邀请**：否则被移除者能用旧令牌重新进来；
3. **``policy`` 局部更新**：只传 ``allowMemberPublish`` 不能把 ``allowViewerInvoke``
   顺手改回 false（网络策略是 PATCH 语义）；
4. **审计不谎报**：对已 accepted 的邀请点"撤销"是空操作，不能写 success revoke；
   另外 ``change_network_status`` 改成 CAS，避免两个管理员互相覆盖状态迁移。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.context import AuthContext
from core.auth.service import normalize_email
from core.auth.tokens import generate_session_token, hash_session_token
from core.db.base import utcnow
from core.db.models import (
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    AuditEvent,
    Network,
    NetworkInvitation,
    NetworkMembership,
    Tenant,
    User,
)
from core.db.session import get_sessionmaker
from core.errors import ConflictError, GoneError
from core.network.service import (
    ACTION_INVITATION_REVOKE,
    accept_invitation,
    change_network_status,
    leave_network,
    remove_member,
    revoke_invitation,
    update_network,
)

REQUEST_ID = "network-lifecycle"


# ── 脚手架 ────────────────────────────────────────────────────────────


@asynccontextmanager
async def independent_session() -> AsyncIterator[AsyncSession]:
    factory = get_sessionmaker()
    async with factory() as session:
        yield session


def ctx_for(user: User, tenant: Tenant, *, role: str = NETWORK_ROLE_MEMBER) -> AuthContext:
    return AuthContext(
        user_id=str(user.id),
        session_id=str(uuid.uuid4()),
        tenant_id=str(tenant.id),
        tenant_role="owner",
        request_id=REQUEST_ID,
        email=user.email,
        display_name=user.display_name,
    )


@pytest.fixture()
async def seeded(db_session: AsyncSession):
    """租户 + alice(owner)/bob(member)/carol(租户成员，未入网)。"""
    tag = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"生命周期-{tag}", slug=f"lifecycle-{tag}")
    db_session.add(tenant)
    await db_session.flush()

    users: dict[str, User] = {}
    for name in ("alice", "bob", "carol"):
        user = User(
            email=normalize_email(f"{name}-{tag}@lifecycle.test"),
            display_name=name,
            status="active",
        )
        db_session.add(user)
        users[name] = user
    await db_session.flush()

    network = Network(
        tenant_id=tenant.id,
        name=f"N-{tag}",
        owner_id=users["alice"].id,
        status="active",
        # 关键：viewer 开关先打开，验证"只改 member 开关"时它不会被覆盖
        policy={"allowMemberPublish": False, "allowViewerInvoke": True},
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
    return tenant, users, network


async def _add_invitation(
    session: AsyncSession,
    *,
    network_id: uuid.UUID,
    email: str,
    invited_by: uuid.UUID,
    status: str = INVITATION_PENDING,
    expires_in: timedelta = timedelta(days=1),
) -> tuple[NetworkInvitation, str]:
    token = generate_session_token()
    invitation = NetworkInvitation(
        network_id=network_id,
        email=email,
        token_hash=hash_session_token(token),
        status=status,
        expires_at=utcnow() + expires_in,
        invited_by=invited_by,
    )
    session.add(invitation)
    await session.commit()
    return invitation, token


async def _invitation_status(invitation_id: uuid.UUID) -> str:
    async with independent_session() as session:
        row = await session.get(NetworkInvitation, invitation_id)
        assert row is not None
        return row.status


async def _revoke_audits(
    session: AsyncSession, *, network_id: uuid.UUID, invitation_id: uuid.UUID
) -> list[AuditEvent]:
    return list(
        (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == ACTION_INVITATION_REVOKE,
                    AuditEvent.network_id == network_id,
                    AuditEvent.target_id == str(invitation_id),
                )
            )
        ).scalars()
    )


# ── 1) 过期邀请：状态必须提交后再抛 410 ──────────────────────────────


class TestExpiredInvitation:
    async def test_expired_status_is_persisted(self, db_session: AsyncSession, seeded) -> None:
        tenant, users, network = seeded
        invitation, token = await _add_invitation(
            db_session,
            network_id=network.id,
            email=users["carol"].email,
            invited_by=users["alice"].id,
            expires_in=timedelta(hours=-1),  # 已过期
        )

        with pytest.raises(GoneError):
            await accept_invitation(
                db_session,
                ctx_for(users["carol"], tenant),
                token=token,
                request_id=REQUEST_ID,
            )

        # 独立 session 读库：证明状态真的提交了，不是内存里的假象
        assert await _invitation_status(invitation.id) == INVITATION_EXPIRED

    async def test_second_accept_sees_expired_not_pending(
        self, db_session: AsyncSession, seeded
    ) -> None:
        """过期后再试：走的是"已被使用或已失效"分支，而不是每次都重新置 expired。"""
        tenant, users, network = seeded
        _invitation, token = await _add_invitation(
            db_session,
            network_id=network.id,
            email=users["carol"].email,
            invited_by=users["alice"].id,
            expires_in=timedelta(hours=-1),
        )

        with pytest.raises(GoneError):
            await accept_invitation(
                db_session, ctx_for(users["carol"], tenant), token=token, request_id=REQUEST_ID
            )

        with pytest.raises(GoneError) as exc_info:
            await accept_invitation(
                db_session, ctx_for(users["carol"], tenant), token=token, request_id=REQUEST_ID
            )
        assert "已被使用" in str(exc_info.value) or "已失效" in str(exc_info.value)


# ── 2) 移除成员 / 退出网络：吊销 pending 邀请 ────────────────────────


class TestPendingInvitationRevoked:
    async def test_remove_member_revokes_pending_invitation(
        self, db_session: AsyncSession, seeded
    ) -> None:
        tenant, users, network = seeded
        invitation, token = await _add_invitation(
            db_session,
            network_id=network.id,
            email=users["bob"].email,
            invited_by=users["alice"].id,
        )

        await remove_member(
            db_session,
            ctx_for(users["alice"], tenant),
            network_id=network.id,
            target_user_id=users["bob"].id,
            request_id=REQUEST_ID,
        )
        await db_session.commit()

        assert await _invitation_status(invitation.id) == INVITATION_REVOKED
        # 被移除者拿旧令牌不能重入
        with pytest.raises(GoneError):
            await accept_invitation(
                db_session, ctx_for(users["bob"], tenant), token=token, request_id=REQUEST_ID
            )

    async def test_leave_network_revokes_pending_invitation(
        self, db_session: AsyncSession, seeded
    ) -> None:
        tenant, users, network = seeded
        invitation, token = await _add_invitation(
            db_session,
            network_id=network.id,
            email=users["bob"].email,
            invited_by=users["alice"].id,
        )

        await leave_network(
            db_session,
            ctx_for(users["bob"], tenant),
            network_id=network.id,
            request_id=REQUEST_ID,
        )
        await db_session.commit()

        assert await _invitation_status(invitation.id) == INVITATION_REVOKED
        with pytest.raises(GoneError):
            await accept_invitation(
                db_session, ctx_for(users["bob"], tenant), token=token, request_id=REQUEST_ID
            )


# ── 3) NetworkPolicy 局部更新 ────────────────────────────────────────


class TestPolicyPartialUpdate:
    async def test_only_explicit_policy_field_is_merged(
        self, db_session: AsyncSession, seeded
    ) -> None:
        """只传 allowMemberPublish 时，allowViewerInvoke 必须保持 true（原值）。"""
        from app.api.schemas.network_schemas import UpdateNetworkRequest

        tenant, users, network = seeded
        ctx = ctx_for(users["alice"], tenant)

        payload = UpdateNetworkRequest.model_validate(
            {"policy": {"allowMemberPublish": True}}
        )
        assert payload.policy is not None
        updated = await update_network(
            db_session,
            ctx,
            network_id=network.id,
            policy=payload.policy.model_dump(by_alias=True),
            request_id=REQUEST_ID,
        )
        await db_session.commit()
        assert updated.policy["allowMemberPublish"] is True
        assert updated.policy["allowViewerInvoke"] is True, (
            "只改一个开关不能把另一个开关顺手改成 false（局部更新语义）"
        )

    async def test_can_turn_a_flag_off_explicitly(
        self, db_session: AsyncSession, seeded
    ) -> None:
        from app.api.schemas.network_schemas import UpdateNetworkRequest

        tenant, users, network = seeded
        ctx = ctx_for(users["alice"], tenant)

        payload = UpdateNetworkRequest.model_validate(
            {"policy": {"allowViewerInvoke": False}}
        )
        updated = await update_network(
            db_session,
            ctx,
            network_id=network.id,
            policy=payload.policy.model_dump(by_alias=True),
            request_id=REQUEST_ID,
        )
        assert updated.policy["allowViewerInvoke"] is False  # 显式 false 生效
        assert updated.policy["allowMemberPublish"] is False  # 未提供的项保持原值


# ── 4) 审计语义 ─────────────────────────────────────────────────────


class TestInvitationRevokeAudit:
    async def test_pending_invitation_revoke_writes_audit_once(
        self, db_session: AsyncSession, seeded
    ) -> None:
        tenant, users, network = seeded
        invitation, _token = await _add_invitation(
            db_session,
            network_id=network.id,
            email=users["carol"].email,
            invited_by=users["alice"].id,
        )
        ctx = ctx_for(users["alice"], tenant)

        await revoke_invitation(
            db_session,
            ctx,
            network_id=network.id,
            invitation_id=invitation.id,
            request_id=REQUEST_ID,
        )
        await db_session.commit()
        assert await _invitation_status(invitation.id) == INVITATION_REVOKED

        # 幂等再点一次：状态没有迁移，就不再写一条 success revoke
        await revoke_invitation(
            db_session,
            ctx,
            network_id=network.id,
            invitation_id=invitation.id,
            request_id=REQUEST_ID,
        )
        await db_session.commit()

        assert len(await _revoke_audits(db_session, network_id=network.id, invitation_id=invitation.id)) == 1

    async def test_accepted_invitation_revoke_does_not_claim_success(
        self, db_session: AsyncSession, seeded
    ) -> None:
        """对已接受（已用掉）的邀请点撤销：不改状态，也不写 success revoke。"""
        tenant, users, network = seeded
        invitation, _token = await _add_invitation(
            db_session,
            network_id=network.id,
            email=users["carol"].email,
            invited_by=users["alice"].id,
            status=INVITATION_ACCEPTED,
        )

        await revoke_invitation(
            db_session,
            ctx_for(users["alice"], tenant),
            network_id=network.id,
            invitation_id=invitation.id,
            request_id=REQUEST_ID,
        )
        await db_session.commit()

        assert await _invitation_status(invitation.id) == INVITATION_ACCEPTED
        assert (
            await _revoke_audits(
                db_session, network_id=network.id, invitation_id=invitation.id
            )
            == []
        )


# ── 5) change_network_status 的 CAS ─────────────────────────────────


class TestNetworkStatusCAS:
    async def test_status_change_success(self, db_session: AsyncSession, seeded) -> None:
        tenant, users, network = seeded
        updated = await change_network_status(
            db_session,
            ctx_for(users["alice"], tenant),
            network_id=network.id,
            target_status="suspended",
            request_id=REQUEST_ID,
        )
        await db_session.commit()
        assert updated.status == "suspended"

    async def test_concurrent_status_change_conflicts(
        self,
        db_session: AsyncSession,
        seeded,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """判定读到 active 后、写之前被别人改成 suspended → 409，不能覆盖。"""
        tenant, users, network = seeded
        # 先取出 id：下面的 rollback 会让 ORM 对象过期，之后再访问属性会触发
        # 同步 IO（MissingGreenlet）。
        network_id = network.id

        import core.network.service as network_service

        real_authorize = network_service.authorize

        async def racing_authorize(session, ctx, *, permission, network_id):
            target = await real_authorize(
                session, ctx, permission=permission, network_id=network_id
            )
            # 另一个管理员抢先暂停并提交（独立连接）
            async with independent_session() as other:
                await other.execute(
                    update(Network)
                    .where(Network.id == network_id)
                    .values(status="suspended")
                )
                await other.commit()
            return target  # 本地快照仍是 active

        monkeypatch.setattr(network_service, "authorize", racing_authorize)

        with pytest.raises(ConflictError):
            await change_network_status(
                db_session,
                ctx_for(users["alice"], tenant),
                network_id=network_id,
                target_status="archived",
                request_id=REQUEST_ID,
            )

        await db_session.rollback()
        async with independent_session() as fresh:
            row = await fresh.get(Network, network_id)
            assert row is not None
            assert row.status == "suspended", "并发冲突时不得覆盖别人已提交的状态"

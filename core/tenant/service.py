"""租户业务规则 —— 创建、邀请、加入、切换激活租户。

## 权限模型（为什么这里没有权限码）

网络那套 ``network.*`` 权限码是为「一个租户内可以有多个网络、每个网络角色不同」
设计的。租户层只有一个角色（``ctx.tenant_role``），规则简单到不值得再引入一套
权限码：

- **创建租户**：任何已登录用户都可以（PRD FR-A6「支持创建新租户」）；
- **邀请 / 查看邀请 / 撤销邀请**：Owner 或 Admin；
- **查看成员**：任何成员；
- **切换激活租户**：必须是自己已加入的租户（服务端校验，PRD FR-A6）。

## 事务边界

不提交事务，只 ``flush()``；提交由路由负责。
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.audit import add_audit_event
from core.auth.context import AuthContext
from core.auth.service import get_tenant_membership, normalize_email
from core.auth.tokens import generate_session_token, hash_session_token
from core.db.base import utcnow
from core.db.models import (
    MEMBERSHIP_ACTIVE,
    RESULT_SUCCESS,
    TENANT_INVITATION_ACCEPTED,
    TENANT_INVITATION_EXPIRED,
    TENANT_INVITATION_PENDING,
    TENANT_INVITATION_REVOKED,
    TENANT_INVITATION_TTL_DAYS,
    TENANT_ROLE_ADMIN,
    TENANT_ROLE_MEMBER,
    TENANT_ROLE_OWNER,
    Tenant,
    TenantInvitation,
    TenantMembership,
    User,
    UserSession,
)
from core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    UnauthorizedError,
)

# ── 审计动作 ──────────────────────────────────────────────────────────
ACTION_TENANT_CREATE = "tenant.create"
ACTION_TENANT_INVITE = "tenant.invitation.create"
ACTION_TENANT_INVITE_REVOKE = "tenant.invitation.revoke"
ACTION_TENANT_INVITE_ACCEPT = "tenant.invitation.accept"
ACTION_TENANT_SWITCH = "tenant.switch"

#: 可以管理邀请的租户角色
_TENANT_MANAGERS = (TENANT_ROLE_OWNER, TENANT_ROLE_ADMIN)

_NAME_MAX = 128
_INVITATION_TTL = timedelta(days=TENANT_INVITATION_TTL_DAYS)


def _uid(ctx: AuthContext) -> uuid.UUID:
    return uuid.UUID(ctx.user_id)


def _tid(ctx: AuthContext) -> uuid.UUID:
    return uuid.UUID(ctx.tenant_id)


def _make_slug(name: str) -> str:
    """生成租户 slug：``<名字的 ASCII 化>-<8位随机>``。

    中文名 ASCII 化后可能为空，此时回退成 ``team``。加随机后缀是为了避免重名
    冲突 —— 重试循环比直接加后缀更啰嗦。
    """
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:48]
    return f"{base or 'team'}-{uuid.uuid4().hex[:8]}"


def _audit(
    session: AsyncSession,
    *,
    action: str,
    ctx: AuthContext,
    request_id: str,
    tenant_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    add_audit_event(
        session,
        action=action,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=ctx.user_id,
        tenant_id=tenant_id or _tid(ctx),
        target_type=target_type,
        target_id=target_id,
    )


def _assert_manager(ctx: AuthContext) -> None:
    if ctx.tenant_role not in _TENANT_MANAGERS:
        raise ForbiddenError("权限不足")


def _assert_acting_in(ctx: AuthContext, tenant_id: uuid.UUID) -> None:
    """租户管理操作只能作用于**当前激活租户**。

    不做「我是 A 租户管理员，所以能管理 B 租户」这种推断 —— 那会让权限来源
    变模糊。要管理别的租户，先切过去（切换时服务端会校验成员关系）。
    """
    if tenant_id != _tid(ctx):
        raise NotFoundError("租户不存在")


# ── 创建 ──────────────────────────────────────────────────────────────


async def create_tenant(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    name: str,
    request_id: str,
) -> tuple[Tenant, str]:
    """创建新租户，创建者成为 Owner（PRD FR-A6）。返回 ``(租户, 角色)``。

    **不自动切换激活租户** —— 切换永远是显式动作（见 :func:`switch_active_tenant`）。
    前端如需"创建后立即进入"，自行再调一次 switch 接口即可；API 语义保持单一。
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise BadRequestError("租户名称不能为空")
    if len(cleaned) > _NAME_MAX:
        raise BadRequestError(f"租户名称最长 {_NAME_MAX} 个字符")

    tenant = Tenant(name=cleaned, slug=_make_slug(cleaned), status="active")
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:  # slug 碰撞（极小概率）
        await session.rollback()
        raise ConflictError("租户创建冲突，请重试") from exc

    session.add(
        TenantMembership(
            tenant_id=tenant.id,
            user_id=_uid(ctx),
            role=TENANT_ROLE_OWNER,
            status=MEMBERSHIP_ACTIVE,
        )
    )
    await session.flush()

    _audit(
        session,
        action=ACTION_TENANT_CREATE,
        ctx=ctx,
        request_id=request_id,
        tenant_id=tenant.id,
        target_type="tenant",
        target_id=str(tenant.id),
    )
    return tenant, TENANT_ROLE_OWNER


# ── 激活租户切换 ──────────────────────────────────────────────────────


async def switch_active_tenant(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    tenant_id: uuid.UUID,
    request_id: str,
) -> tuple[Tenant, str]:
    """切换当前会话的激活租户。

    **必须由服务端校验 active membership**（PRD FR-A6）：客户端提交的 tenant_id
    只是"意向"，不是权限。切换到非所属租户返回 403，且**保持原租户上下文**。
    """
    membership = await get_tenant_membership(
        session, tenant_id=tenant_id, user_id=_uid(ctx)
    )
    if membership is None or membership.status != MEMBERSHIP_ACTIVE:
        raise ForbiddenError("你不属于该租户")

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active":
        raise NotFoundError("租户不存在")

    user_session = await session.get(UserSession, uuid.UUID(ctx.session_id))
    if user_session is None:
        raise UnauthorizedError("会话不存在")

    user_session.active_tenant_id = tenant_id
    await session.flush()

    _audit(
        session,
        action=ACTION_TENANT_SWITCH,
        ctx=ctx,
        request_id=request_id,
        tenant_id=tenant_id,
        target_type="tenant",
        target_id=str(tenant_id),
    )
    return tenant, membership.role


# ── 成员 ──────────────────────────────────────────────────────────────


async def list_tenant_members(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[tuple[TenantMembership, User | None]]:
    result = await session.execute(
        select(TenantMembership, User)
        .outerjoin(User, User.id == TenantMembership.user_id)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.status == MEMBERSHIP_ACTIVE,
        )
        .order_by(TenantMembership.gmt_create.asc())
    )
    return [(membership, user) for membership, user in result.all()]


# ── 邀请 ──────────────────────────────────────────────────────────────


async def create_tenant_invitation(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    tenant_id: uuid.UUID,
    email: str,
    request_id: str,
) -> tuple[TenantInvitation, str]:
    """邀请邮箱加入租户（Owner/Admin）。返回 ``(邀请, 明文令牌)``。

    **明文令牌只在这一刻存在** —— 库里只有 SHA-256（与网络邀请、会话令牌一致）。

    幂等策略：同一邮箱已有 pending 邀请时撤销旧的、签发新的。不能返回旧的 ——
    明文没存库，无法重放（这是"只存 hash"的必然代价）。
    """
    _assert_acting_in(ctx, tenant_id)
    _assert_manager(ctx)

    email_norm = normalize_email(email)
    if not email_norm:
        raise BadRequestError("邀请邮箱不能为空")

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active":
        raise NotFoundError("租户不存在")

    # 已是成员 → 明确 409（一致结果，便于前端提示）
    existing_user = (
        await session.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()
    if existing_user is not None:
        membership = await get_tenant_membership(
            session, tenant_id=tenant_id, user_id=existing_user.id
        )
        if membership is not None and membership.status == MEMBERSHIP_ACTIVE:
            raise ConflictError("该用户已是租户成员")

    now = utcnow()
    await session.execute(
        update(TenantInvitation)
        .where(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.email == email_norm,
            TenantInvitation.status == TENANT_INVITATION_PENDING,
        )
        .values(status=TENANT_INVITATION_REVOKED, gmt_modified=now)
    )

    token = generate_session_token()
    invitation = TenantInvitation(
        tenant_id=tenant_id,
        email=email_norm,
        token_hash=hash_session_token(token),
        status=TENANT_INVITATION_PENDING,
        expires_at=now + _INVITATION_TTL,
        invited_by=_uid(ctx),
    )
    session.add(invitation)
    await session.flush()

    _audit(
        session,
        action=ACTION_TENANT_INVITE,
        ctx=ctx,
        request_id=request_id,
        tenant_id=tenant_id,
        target_type="invitation",
        target_id=str(invitation.id),
    )
    return invitation, token


async def list_tenant_invitations(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[TenantInvitation]:
    result = await session.execute(
        select(TenantInvitation)
        .where(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.status == TENANT_INVITATION_PENDING,
        )
        .order_by(TenantInvitation.gmt_create.desc())
    )
    return list(result.scalars())


async def revoke_tenant_invitation(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request_id: str,
) -> None:
    """撤销邀请（Owner/Admin）。幂等。"""
    _assert_acting_in(ctx, tenant_id)
    _assert_manager(ctx)

    invitation = await session.get(TenantInvitation, invitation_id)
    if invitation is None or invitation.tenant_id != tenant_id:
        raise NotFoundError("邀请不存在")

    if invitation.status == TENANT_INVITATION_PENDING:
        invitation.status = TENANT_INVITATION_REVOKED
        invitation.gmt_modified = utcnow()
        await session.flush()

    _audit(
        session,
        action=ACTION_TENANT_INVITE_REVOKE,
        ctx=ctx,
        request_id=request_id,
        tenant_id=tenant_id,
        target_type="invitation",
        target_id=str(invitation_id),
    )


async def accept_tenant_invitation(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    token: str,
    request_id: str,
) -> tuple[Tenant, str]:
    """接受租户邀请，成为该租户成员。返回 ``(租户, 角色)``。

    并发保障与网络邀请一致：状态 compare-and-set（同一令牌只能用一次）+
    ``UNIQUE(tenant_id, user_id)``（并发接受只产生一条成员关系）。

    **不自动切换激活租户** —— 保持「切换是显式动作」的单一规则。前端可在接受后
    立刻调 ``switch-tenant`` 实现「加入即进入」的体验。
    """
    if not token:
        raise BadRequestError("缺少邀请令牌")

    token_hash = hash_session_token(token)
    invitation = (
        await session.execute(
            select(TenantInvitation).where(TenantInvitation.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise GoneError("邀请不存在或已失效")

    if invitation.status != TENANT_INVITATION_PENDING:
        raise GoneError("邀请已被使用或已失效")

    if invitation.expires_at <= utcnow():
        invitation.status = TENANT_INVITATION_EXPIRED
        invitation.gmt_modified = utcnow()
        await session.flush()
        raise GoneError("邀请已过期")

    tenant = await session.get(Tenant, invitation.tenant_id)
    if tenant is None or tenant.status != "active":
        raise NotFoundError("租户不存在")

    # 邮箱必须与登录账号匹配（防止令牌被转发滥用）
    if not ctx.email or normalize_email(ctx.email) != invitation.email:
        raise ForbiddenError("该邀请不是发给当前账号的")

    result = await session.execute(
        update(TenantInvitation)
        .where(
            TenantInvitation.id == invitation.id,
            TenantInvitation.status == TENANT_INVITATION_PENDING,
        )
        .values(status=TENANT_INVITATION_ACCEPTED, gmt_modified=utcnow())
    )
    if result.rowcount == 0:
        raise GoneError("邀请已被使用或已失效")

    user_id = _uid(ctx)
    existing = await get_tenant_membership(
        session, tenant_id=tenant.id, user_id=user_id
    )
    if existing is None:
        session.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user_id,
                role=TENANT_ROLE_MEMBER,
                status=MEMBERSHIP_ACTIVE,
            )
        )
    else:
        # 曾被移除的老成员重新加入
        existing.status = MEMBERSHIP_ACTIVE
        existing.role = TENANT_ROLE_MEMBER
        existing.gmt_modified = utcnow()

    await session.flush()
    await session.refresh(invitation)

    _audit(
        session,
        action=ACTION_TENANT_INVITE_ACCEPT,
        ctx=ctx,
        request_id=request_id,
        tenant_id=tenant.id,
        target_type="invitation",
        target_id=str(invitation.id),
    )
    return tenant, TENANT_ROLE_MEMBER

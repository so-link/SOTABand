"""网络业务规则 —— 需求 8.2 的核心。

契约见 ``docs/contract-08-auth-network.md`` §3.2、§5、§6；
设计见 ``docs/design-08-02-network.md``。

## 事务边界

本模块**不提交事务**，只 ``flush()``。提交由路由负责 —— 这样"创建网络 + 写入
Owner 成员"这种多步操作才是原子的。

## 三条必须用代码强制的规则（design-08-02 §6.2）

1. **最后一位 Owner 不可被移除 / 降级 / 退出** —— 事务内重新统计 Owner 数量；
2. **普通成员不能改角色、不能移除他人** —— 由 ``authorize`` 拦在权限码层；
3. **Admin 不能动 Owner、不能授予/剥夺 Owner、不能移除自己** —— 本模块的额外约束
   （真值表只表达"有没有权限码"，表达不了"对谁不能用"）。

## 并发

角色变更用 ``version`` 做 compare-and-set（contract §3.9），并且所有成员变更前
都会调用 :func:`core.auth.authorize.lock_network`：

- PostgreSQL：``SELECT … FOR UPDATE`` 串行化；
- SQLite：pysqlite 默认在第一条 DML 前不发 BEGIN，``FOR UPDATE`` 又是空操作 ——
  所以在 ``lock_network`` 里先跑一条空写 UPDATE 提前取到库级写锁，等效于
  ``BEGIN IMMEDIATE`` 但只作用于成员变更路径（全局 ``BEGIN IMMEDIATE`` 会把
  每个已认证请求都变成写事务，见该函数的说明）。

``tests/unit/test_network_invariant_sqlite.py`` 用两个独立 session 复验
「最后一位 Owner」不变量在 SQLite 上也成立。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.audit import add_audit_event
from core.auth.authorize import (
    authorize,
    count_active_owners,
    get_active_membership,
    get_membership,
    get_network,
    lock_network,
)
from core.auth.context import AuthContext
from core.auth.permissions import (
    NETWORK_ARCHIVE,
    NETWORK_AUDIT_READ,
    NETWORK_INVITE_CREATE,
    NETWORK_INVITE_REVOKE,
    NETWORK_MEMBERS_MANAGE,
    NETWORK_UPDATE,
)
from core.auth.tokens import generate_session_token, hash_session_token
from core.db.base import utcnow
from core.db.models import (
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    INVITATION_TTL_DAYS,
    NETWORK_ACTIVE,
    NETWORK_ARCHIVED,
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_MEMBERSHIP_REMOVED,
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    NETWORK_ROLES,
    NETWORK_SUSPENDED,
    RESULT_SUCCESS,
    AuditEvent,
    Network,
    NetworkInvitation,
    NetworkMembership,
    User,
    default_policy,
)
from core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
)

# ── 审计动作 ──────────────────────────────────────────────────────────
ACTION_NETWORK_CREATE = "network.create"
ACTION_NETWORK_UPDATE = "network.update"
ACTION_NETWORK_STATUS = "network.status.change"
ACTION_MEMBER_ROLE_CHANGE = "network.member.role.change"
ACTION_MEMBER_REMOVE = "network.member.remove"
ACTION_MEMBER_LEAVE = "network.member.leave"
ACTION_INVITATION_CREATE = "network.invitation.create"
ACTION_INVITATION_REVOKE = "network.invitation.revoke"
ACTION_INVITATION_ACCEPT = "network.invitation.accept"

#: 允许的状态迁移（契约 §4.1）。**不允许删除网络** —— 删除会破坏审计追溯性。
_ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    NETWORK_ACTIVE: {NETWORK_SUSPENDED, NETWORK_ARCHIVED},
    NETWORK_SUSPENDED: {NETWORK_ACTIVE, NETWORK_ARCHIVED},
    NETWORK_ARCHIVED: {NETWORK_ACTIVE},
}

_NAME_MAX = 128
_INVITATION_TTL = timedelta(days=INVITATION_TTL_DAYS)


# ── 小工具 ────────────────────────────────────────────────────────────


def _uid(ctx: AuthContext) -> uuid.UUID:
    return uuid.UUID(ctx.user_id)


def _tid(ctx: AuthContext) -> uuid.UUID:
    return uuid.UUID(ctx.tenant_id)


def _validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise BadRequestError("网络名称不能为空")
    if len(cleaned) > _NAME_MAX:
        raise BadRequestError(f"网络名称最长 {_NAME_MAX} 个字符")
    return cleaned


def _normalize_email(email: str) -> str:
    from core.auth.service import normalize_email

    return normalize_email(email)


def _audit(
    session: AsyncSession,
    *,
    action: str,
    ctx: AuthContext,
    request_id: str,
    network_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    """写一条网络操作审计（跟随调用方事务提交）。"""
    add_audit_event(
        session,
        action=action,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=ctx.user_id,
        tenant_id=_tid(ctx),
        network_id=network_id,
        target_type=target_type,
        target_id=target_id,
    )


# ── 查询 ──────────────────────────────────────────────────────────────


async def list_user_networks(
    session: AsyncSession, *, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> list[tuple[Network, str]]:
    """我所属的、当前租户下的网络及我的角色（契约 §3.2 ``GET /networks``）。"""
    result = await session.execute(
        select(Network, NetworkMembership.role)
        .join(NetworkMembership, NetworkMembership.network_id == Network.id)
        .where(
            NetworkMembership.user_id == user_id,
            NetworkMembership.status == NETWORK_MEMBERSHIP_ACTIVE,
            Network.tenant_id == tenant_id,
        )
        .order_by(Network.gmt_create.desc())
    )
    return [(network, role) for network, role in result.all()]


async def get_members(
    session: AsyncSession, *, network_id: uuid.UUID
) -> list[tuple[NetworkMembership, User | None]]:
    """网络成员列表（含 User 详情，供前端展示名字/邮箱）。"""
    result = await session.execute(
        select(NetworkMembership, User)
        .outerjoin(User, User.id == NetworkMembership.user_id)
        .where(
            NetworkMembership.network_id == network_id,
            NetworkMembership.status == NETWORK_MEMBERSHIP_ACTIVE,
        )
        .order_by(NetworkMembership.joined_at.asc())
    )
    return [(membership, user) for membership, user in result.all()]


async def list_invitations(
    session: AsyncSession, *, network_id: uuid.UUID, only_pending: bool = True
) -> list[NetworkInvitation]:
    stmt = select(NetworkInvitation).where(NetworkInvitation.network_id == network_id)
    if only_pending:
        stmt = stmt.where(NetworkInvitation.status == INVITATION_PENDING)
    stmt = stmt.order_by(NetworkInvitation.gmt_create.desc())
    result = await session.execute(stmt)
    return list(result.scalars())


# ── 网络生命周期 ──────────────────────────────────────────────────────


async def create_network(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    name: str,
    policy: dict | None = None,
    request_id: str,
) -> Network:
    """创建网络，创建者自动成为唯一 Owner（PRD FR-N1）。

    "已认证 + 租户成员"由 ``get_auth_context`` 保证（它只在用户有 active 租户
    成员关系时才构造出 ``AuthContext``），所以这里不需要额外的权限码。
    """
    cleaned = _validate_name(name)
    merged_policy = default_policy()
    if policy:
        # None = 本次没有显式提供（NetworkPolicy 字段可空，见 schema 说明）
        merged_policy.update(
            {
                k: bool(v)
                for k, v in policy.items()
                if k in merged_policy and v is not None
            }
        )

    network = Network(
        tenant_id=_tid(ctx),
        name=cleaned,
        owner_id=_uid(ctx),
        status=NETWORK_ACTIVE,
        policy=merged_policy,
    )
    session.add(network)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("同一租户下已存在同名网络") from exc

    # 创建者同时写入 membership（role=owner 才是权限来源；owner_id 只是创建者记录）
    session.add(
        NetworkMembership(
            network_id=network.id,
            user_id=_uid(ctx),
            role=NETWORK_ROLE_OWNER,
            status=NETWORK_MEMBERSHIP_ACTIVE,
            joined_at=utcnow(),
        )
    )
    await session.flush()

    _audit(
        session,
        action=ACTION_NETWORK_CREATE,
        ctx=ctx,
        request_id=request_id,
        network_id=network.id,
        target_type="network",
        target_id=str(network.id),
    )
    return network


async def update_network(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    name: str | None = None,
    policy: dict | None = None,
    request_id: str,
) -> Network:
    """修改网络名称 / 策略（需要 ``network.update``）。"""
    network = await authorize(
        session, ctx, permission=NETWORK_UPDATE, network_id=network_id
    )

    if name is not None:
        cleaned = _validate_name(name)
        if cleaned != network.name:
            network.name = cleaned
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("同一租户下已存在同名网络") from exc

    if policy is not None:
        # **局部合并**：只更新本次显式提供的键。
        # 之前对两个键都 ``bool(policy.get(key))`` —— 只传 allowMemberPublish 时
        # allowViewerInvoke 取到 None → False，把一个开关悄悄关掉了。
        merged = dict(network.policy or {})
        for key in ("allowMemberPublish", "allowViewerInvoke"):
            value = policy.get(key)
            if value is not None:
                merged[key] = bool(value)
        network.policy = merged

    network.gmt_modified = utcnow()
    await session.flush()

    _audit(
        session,
        action=ACTION_NETWORK_UPDATE,
        ctx=ctx,
        request_id=request_id,
        network_id=network.id,
        target_type="network",
        target_id=str(network.id),
    )
    return network


async def change_network_status(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    target_status: str,
    request_id: str,
) -> Network:
    """暂停 / 归档 / 恢复（需要 ``network.archive`` —— **Owner 独占**）。

    状态机见 contract §4.1：``active ⇄ suspended → archived``；``archived`` 只能
    恢复到 ``active``。**不提供删除** —— 删除会破坏审计追溯性。
    """
    if target_status not in {NETWORK_ACTIVE, NETWORK_SUSPENDED, NETWORK_ARCHIVED}:
        raise BadRequestError(f"未知的网络状态：{target_status}")

    network = await authorize(
        session, ctx, permission=NETWORK_ARCHIVE, network_id=network_id
    )

    allowed = _ALLOWED_STATUS_TRANSITIONS.get(network.status, set())
    if target_status not in allowed:
        raise ConflictError(
            f"网络状态不能从「{network.status}」变为「{target_status}」"
        )

    # CAS：authorize() 读到的 status 只是"判定时"的快照。两个并发请求可能都读到
    # active，一个暂停、另一个归档 —— 后写者会覆盖前者的迁移，状态机就不再成立。
    # 把读到的状态放进 UPDATE 的 WHERE，影响 0 行即"状态已被别人改过" → 409。
    # （这同时让两个请求的"迁移表校验"与"写入"成为同一个判定，不存在中间窗口。）
    expected_status = network.status
    result = await session.execute(
        update(Network)
        .where(Network.id == network_id, Network.status == expected_status)
        .values(status=target_status, gmt_modified=utcnow())
    )
    if result.rowcount == 0:
        raise ConflictError("网络状态已被其他操作修改，请刷新后重试")
    await session.flush()
    await session.refresh(network)

    _audit(
        session,
        action=ACTION_NETWORK_STATUS,
        ctx=ctx,
        request_id=request_id,
        network_id=network.id,
        target_type="network",
        target_id=str(network.id),
    )
    return network


# ── 成员管理 ──────────────────────────────────────────────────────────


def _assert_admin_may_act_on(actor_role: str, target_role: str, *, actor_id: uuid.UUID, target_id: uuid.UUID) -> None:
    """Admin 的额外限制（真值表表达不了"对谁不能用"）。

    - Admin 不可对 Owner 行使任何成员管理动作 → 对 Admin 来说该成员"不存在"（404）；
    - Admin 不可移除自己 → 必须走 ``leave``（409）。
    """
    if actor_role != NETWORK_ROLE_ADMIN:
        return
    if target_role == NETWORK_ROLE_OWNER:
        # 404 而不是 403：不向 Admin 确认"这里有个我动不了的 Owner"
        raise NotFoundError("成员不存在")
    if actor_id == target_id:
        raise ConflictError("不能移除或修改自己，请使用「退出网络」")


def _assert_can_assign_role(actor_role: str, new_role: str) -> None:
    """Admin 不可授予 Owner（真值表表达不了"授予权限"）。"""
    if actor_role == NETWORK_ROLE_ADMIN and new_role == NETWORK_ROLE_OWNER:
        raise ForbiddenError("管理员不能授予 Owner 角色")


async def change_member_role(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_role: str,
    expected_version: int | None = None,
    request_id: str,
) -> NetworkMembership:
    """调整成员角色（需要 ``network.members.manage``）。

    并发守卫：``version`` compare-and-set。``expected_version`` 由前端从成员列表
    拿到后回传；不传则用服务端读到的当前值（退化为"最后写入者胜"，但仍会推进版本）。
    """
    if new_role not in NETWORK_ROLES:
        raise BadRequestError(f"未知角色：{new_role}")

    await authorize(
        session, ctx, permission=NETWORK_MEMBERS_MANAGE, network_id=network_id
    )

    # 排他锁：把成员变更串行化。
    # 「最后一位 Owner」守卫是 check-then-act，不加锁在 PostgreSQL 上会两个请求
    # 都读到 count=2 然后都删掉 → 0 位 Owner（已由并发测试实测复现）。
    await lock_network(session, network_id)

    actor_membership = await get_active_membership(
        session, network_id=network_id, user_id=_uid(ctx)
    )
    if actor_membership is None:  # authorize 已保证，这里是类型收窄
        raise NotFoundError("网络不存在")

    target = await get_active_membership(
        session, network_id=network_id, user_id=target_user_id
    )
    if target is None:
        raise NotFoundError("成员不存在")

    _assert_admin_may_act_on(
        actor_membership.role, target.role, actor_id=_uid(ctx), target_id=target_user_id
    )
    _assert_can_assign_role(actor_membership.role, new_role)

    # 最后一位 Owner 不可被降级
    if (
        target.role == NETWORK_ROLE_OWNER
        and new_role != NETWORK_ROLE_OWNER
        and await count_active_owners(session, network_id=network_id) <= 1
    ):
        raise ConflictError("不能降级最后一位 Owner")

    if target.role == new_role:
        return target  # 幂等

    expected = expected_version if expected_version is not None else target.version
    result = await session.execute(
        update(NetworkMembership)
        .where(
            NetworkMembership.id == target.id,
            NetworkMembership.version == expected,
        )
        .values(
            role=new_role,
            version=NetworkMembership.version + 1,
            gmt_modified=utcnow(),
        )
    )
    if result.rowcount == 0:
        raise ConflictError("成员角色已被其他操作修改，请刷新后重试")

    await session.flush()
    await session.refresh(target)

    _audit(
        session,
        action=ACTION_MEMBER_ROLE_CHANGE,
        ctx=ctx,
        request_id=request_id,
        network_id=network_id,
        target_type="user",
        target_id=str(target_user_id),
    )
    return target


async def remove_member(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    target_user_id: uuid.UUID,
    request_id: str,
) -> None:
    """移除成员（需要 ``network.members.manage``）。软删除：``status='removed'``。"""
    await authorize(
        session, ctx, permission=NETWORK_MEMBERS_MANAGE, network_id=network_id
    )

    # 排他锁：串行化成员变更（理由见 change_member_role 与 lock_network 的文档）
    await lock_network(session, network_id)

    actor_membership = await get_active_membership(
        session, network_id=network_id, user_id=_uid(ctx)
    )
    if actor_membership is None:
        raise NotFoundError("网络不存在")

    target = await get_active_membership(
        session, network_id=network_id, user_id=target_user_id
    )
    if target is None:
        raise NotFoundError("成员不存在")

    _assert_admin_may_act_on(
        actor_membership.role, target.role, actor_id=_uid(ctx), target_id=target_user_id
    )

    if (
        target.role == NETWORK_ROLE_OWNER
        and await count_active_owners(session, network_id=network_id) <= 1
    ):
        raise ConflictError("不能移除最后一位 Owner")

    target.status = NETWORK_MEMBERSHIP_REMOVED
    target.version += 1
    target.gmt_modified = utcnow()
    await session.flush()

    # 未使用的 pending 邀请必须一起吊销 —— 否则被移除者能用旧令牌重新进来
    target_user = await session.get(User, target_user_id)
    await _revoke_pending_invitations_for_email(
        session,
        network_id=network_id,
        email=target_user.email if target_user else None,
        ctx=ctx,
        request_id=request_id,
    )

    _audit(
        session,
        action=ACTION_MEMBER_REMOVE,
        ctx=ctx,
        request_id=request_id,
        network_id=network_id,
        target_type="user",
        target_id=str(target_user_id),
    )


async def leave_network(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    request_id: str,
) -> None:
    """主动退出网络。**不需要** ``members.manage``（谁都能退出自己）。

    但若是最后一位 Owner，拒绝退出（409）—— 否则网络会变成无主状态。
    """
    network = await get_network(session, network_id)
    if network is None or network.tenant_id != _tid(ctx):
        raise NotFoundError("网络不存在")

    # 排他锁：退出同样受"最后一位 Owner"守卫约束，必须与其它成员变更串行化
    locked = await lock_network(session, network_id)
    if locked is None:
        raise NotFoundError("网络不存在")
    network = locked

    # 退出是**写操作**：暂停/归档状态下应拒绝（契约 §4.2）。
    # 这里没走 authorize()（退出不需要任何权限码），所以状态门禁要手动补上 ——
    # 这是测试 test_suspended_allows_read_but_blocks_write 抓到的遗漏。
    if network.status != NETWORK_ACTIVE:
        raise ConflictError(f"网络当前状态为「{network.status}」，无法执行该操作")

    membership = await get_active_membership(
        session, network_id=network_id, user_id=_uid(ctx)
    )
    if membership is None:
        raise NotFoundError("你不是该网络的成员")

    if (
        membership.role == NETWORK_ROLE_OWNER
        and await count_active_owners(session, network_id=network_id) <= 1
    ):
        raise ConflictError("你是最后一位 Owner，不能退出。请先转移所有权或归档网络")

    membership.status = NETWORK_MEMBERSHIP_REMOVED
    membership.version += 1
    membership.gmt_modified = utcnow()
    await session.flush()

    # 退出后旧邀请同样不能让他再进来（邮箱优先取会话上下文，缺失再查库）
    email = ctx.email
    if not email:
        user = await session.get(User, _uid(ctx))
        email = user.email if user else None
    await _revoke_pending_invitations_for_email(
        session,
        network_id=network_id,
        email=email,
        ctx=ctx,
        request_id=request_id,
    )

    _audit(
        session,
        action=ACTION_MEMBER_LEAVE,
        ctx=ctx,
        request_id=request_id,
        network_id=network_id,
        target_type="network",
        target_id=str(network_id),
    )


# ── 邀请 ──────────────────────────────────────────────────────────────


async def _revoke_pending_invitations_for_email(
    session: AsyncSession,
    *,
    network_id: uuid.UUID,
    email: str | None,
    ctx: AuthContext,
    request_id: str,
) -> int:
    """把某个邮箱在该网络的 pending 邀请全部置为 revoked，返回撤销条数。

    ## 为什么「移除成员 / 退出网络」时必须做

    邀请令牌是**一次性**的，但 pending 邀请在成员被移除后仍然有效：管理员把人踢了，
    被邀者却能拿之前收到的旧令牌重新加入 —— 移除操作因此没有真正生效。
    同理，成员自己退出后，旧邀请也能让他再次进来。

    邀请只存 token hash，无法"按人"关联，只能按**邮箱**收口（创建邀请时邮箱已规范化）。
    """
    if not email:
        return 0
    normalized = _normalize_email(email)
    if not normalized:
        return 0

    result = await session.execute(
        select(NetworkInvitation.id).where(
            NetworkInvitation.network_id == network_id,
            NetworkInvitation.email == normalized,
            NetworkInvitation.status == INVITATION_PENDING,
        )
    )
    invitation_ids = list(result.scalars())
    if not invitation_ids:
        return 0

    now = utcnow()
    update_result = await session.execute(
        update(NetworkInvitation)
        .where(NetworkInvitation.id.in_(invitation_ids))
        .values(status=INVITATION_REVOKED, gmt_modified=now)
    )
    # 逐条审计：事后要能查到"这条邀请是被成员移除操作连带撤销的"
    for invitation_id in invitation_ids:
        _audit(
            session,
            action=ACTION_INVITATION_REVOKE,
            ctx=ctx,
            request_id=request_id,
            network_id=network_id,
            target_type="invitation",
            target_id=str(invitation_id),
        )
    await session.flush()
    return int(update_result.rowcount or len(invitation_ids))


async def create_invitation(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    email: str,
    request_id: str,
) -> tuple[NetworkInvitation, str]:
    """发起邀请（需要 ``network.invite.create``）。返回 ``(邀请, 明文令牌)``。

    **明文令牌只在这一刻存在** —— 库里只有 SHA-256。前端需要它拼邀请链接。

    幂等策略：同一邮箱已有 pending 邀请时，**撤销旧的、签发新的**。
    不能直接返回旧的 —— 因为明文令牌没有存库，无法重放（这是"只存 hash"的必然
    代价，换来的是数据库泄露也不能直接接受邀请）。
    """
    email_norm = _normalize_email(email)
    if not email_norm:
        raise BadRequestError("邀请邮箱不能为空")

    network = await authorize(
        session, ctx, permission=NETWORK_INVITE_CREATE, network_id=network_id
    )

    # 已是成员 → 明确 409（一致结果，便于前端提示）
    existing_user = (
        await session.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()
    if existing_user is not None:
        existing_membership = await get_active_membership(
            session, network_id=network_id, user_id=existing_user.id
        )
        if existing_membership is not None:
            raise ConflictError("该用户已是网络成员")

    # 撤销旧的 pending 邀请，保证 (network, email) 同时只有一张有效邀请
    now = utcnow()
    await session.execute(
        update(NetworkInvitation)
        .where(
            NetworkInvitation.network_id == network_id,
            NetworkInvitation.email == email_norm,
            NetworkInvitation.status == INVITATION_PENDING,
        )
        .values(status=INVITATION_REVOKED, gmt_modified=now)
    )

    token = generate_session_token()
    invitation = NetworkInvitation(
        network_id=network_id,
        email=email_norm,
        token_hash=hash_session_token(token),
        status=INVITATION_PENDING,
        expires_at=now + _INVITATION_TTL,
        invited_by=_uid(ctx),
    )
    session.add(invitation)
    await session.flush()

    _audit(
        session,
        action=ACTION_INVITATION_CREATE,
        ctx=ctx,
        request_id=request_id,
        network_id=network.id,
        target_type="invitation",
        target_id=str(invitation.id),
    )
    return invitation, token


async def revoke_invitation(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request_id: str,
) -> None:
    """撤销邀请（需要 ``network.invite.revoke``）。幂等。"""
    await authorize(
        session, ctx, permission=NETWORK_INVITE_REVOKE, network_id=network_id
    )

    invitation = await session.get(NetworkInvitation, invitation_id)
    if invitation is None or invitation.network_id != network_id:
        raise NotFoundError("邀请不存在")

    if invitation.status == INVITATION_PENDING:
        invitation.status = INVITATION_REVOKED
        invitation.gmt_modified = utcnow()
        await session.flush()
        # **只在状态真的迁移时写审计**：对已 accepted / expired / revoked 的邀请
        # 再点一次"撤销"是空操作，写 success revoke 会让审计与事实不一致
        # （审计是事后追责的唯一依据，谎报比缺失更糟）。
        _audit(
            session,
            action=ACTION_INVITATION_REVOKE,
            ctx=ctx,
            request_id=request_id,
            network_id=network_id,
            target_type="invitation",
            target_id=str(invitation_id),
        )


async def accept_invitation(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    token: str,
    request_id: str,
) -> Network:
    """接受邀请并加入网络。

    校验链（design-08-02 §5.2）：令牌有效 → pending → 未过期 → 网络 active →
    邮箱匹配 → 条件更新状态 → 写入/恢复成员关系。

    **三处并发保障**：

    - 状态用 compare-and-set（``WHERE status='pending'``），同一令牌只能用一次；
    - 成员关系靠 ``UNIQUE(network_id, user_id)``，并发接受只产生一条；
    - 重复点击返回成功（幂等），不报错打扰用户。
    """
    if not token:
        raise BadRequestError("缺少邀请令牌")

    token_hash = hash_session_token(token)
    invitation = (
        await session.execute(
            select(NetworkInvitation).where(NetworkInvitation.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise GoneError("邀请不存在或已失效")

    if invitation.status != INVITATION_PENDING:
        raise GoneError("邀请已被使用或已失效")

    if invitation.expires_at <= utcnow():
        invitation.status = INVITATION_EXPIRED
        invitation.gmt_modified = utcnow()
        await session.flush()
        # ⚠️ 必须**先提交**再抛 GoneError：
        # 路由层/依赖层会在异常时回滚整个事务，不提交的话"置为 expired"永远
        # 落不了库 —— 邀请会一直显示 pending，每次都走一遍过期分支。
        # 这里是一次独立的状态迁移（没有其它业务写入），提交是安全的。
        await session.commit()
        raise GoneError("邀请已过期")

    network = await get_network(session, invitation.network_id)
    if network is None or network.tenant_id != _tid(ctx):
        # 跨租户邀请：令牌有效也不允许（网络不在你的租户世界里）
        raise NotFoundError("网络不存在")

    if network.status != NETWORK_ACTIVE:
        raise ConflictError(f"网络当前状态为「{network.status}」，无法加入")

    # 邮箱必须与登录账号匹配（PRD FR-N3）
    if not ctx.email or _normalize_email(ctx.email) != invitation.email:
        raise ForbiddenError("该邀请不是发给当前账号的")

    # 条件更新：只有仍是 pending 才置为 accepted —— 同一令牌二次使用会在这里失败
    result = await session.execute(
        update(NetworkInvitation)
        .where(
            NetworkInvitation.id == invitation.id,
            NetworkInvitation.status == INVITATION_PENDING,
        )
        .values(status=INVITATION_ACCEPTED, gmt_modified=utcnow())
    )
    if result.rowcount == 0:
        raise GoneError("邀请已被使用或已失效")

    user_id = _uid(ctx)
    existing = await get_membership(
        session, network_id=network.id, user_id=user_id
    )
    if existing is None:
        session.add(
            NetworkMembership(
                network_id=network.id,
                user_id=user_id,
                role=NETWORK_ROLE_MEMBER,
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            )
        )
    elif existing.status != NETWORK_MEMBERSHIP_ACTIVE:
        # 曾被移除的老成员重新加入
        existing.status = NETWORK_MEMBERSHIP_ACTIVE
        existing.role = NETWORK_ROLE_MEMBER
        existing.version += 1
        existing.joined_at = utcnow()

    await session.flush()
    await session.refresh(invitation)

    _audit(
        session,
        action=ACTION_INVITATION_ACCEPT,
        ctx=ctx,
        request_id=request_id,
        network_id=network.id,
        target_type="invitation",
        target_id=str(invitation.id),
    )
    return network


# ── 审计查询（FR-N9）─────────────────────────────────────────────────


async def list_network_audit(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
) -> list[AuditEvent]:
    """查询某个网络的审计事件（需要 ``network.audit.read`` = Owner/Admin）。

    ## 为什么先授权再查

    审计日志里有 ``actor_id`` / ``target_id`` / ``request_id`` —— 是**最敏感**的一类
    数据（能还原谁在什么时候做了什么）。所以必须先过 ``authorize()``，不能用
    "查出来再按租户过滤"的写法。

    ## 为什么不做分页总数

    审计表是**只追加**的，行数只会增长。`COUNT(*)` 在大表上成本高且价值低 ——
    前端用「还有更多就继续拉」的无限滚动即可。``limit`` 上限 200，防止一次拉爆。

    ## 为什么不需要 tenant_id 过滤

    ``network_id`` 已经唯一确定了网络，而网络属且仅属一个租户
    （PRD 开放问题 2）—— 授权链第 5 步已经保证调用者的激活租户就是这个网络的租户。
    """
    await authorize(session, ctx, permission=NETWORK_AUDIT_READ, network_id=network_id)

    stmt = select(AuditEvent).where(AuditEvent.network_id == network_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    # 新的在前 —— 排查问题总是先看最近的
    stmt = stmt.order_by(AuditEvent.gmt_create.desc()).limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars())

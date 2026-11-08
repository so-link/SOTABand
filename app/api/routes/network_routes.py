"""网络路由 —— ``/api/v1/networks/*``（需求 8.2）

契约见 ``docs/contract-08-auth-network.md`` §3.2。

## 这里的每个接口都先过 ``authorize()``

包括**列表、详情、成员、邀请**。只在列表过滤、详情接口能猜 ID 直取，就是典型
IDOR（契约 §8.3 的警告）。所有判定集中在 ``core.auth.authorize``，本文件只做
HTTP 适配。

## 404 / 403 / 409 的分工

- **404** —— 网络不存在，或存在但调用者无权知晓（同租户非成员、跨租户）；
- **403** —— 已认证、是成员，但角色不允许这个动作；
- **409** —— 状态冲突（网络已暂停/归档、并发改角色、移除最后 Owner、重名）。

领域异常由 ``core.errors.AppError`` 子类抛出，``app/main.py`` 的处理器统一
转成对应的 HTTP 状态码 —— 这样 ``core`` 层不用 import FastAPI。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context, get_request_id
from app.api.schemas.network_schemas import (
    AuditEventSummary,
    ChangeMemberRoleRequest,
    CreateInvitationRequest,
    CreateNetworkRequest,
    InvitationCreated,
    InvitationSummary,
    MemberSummary,
    NetworkDetail,
    NetworkSummary,
    UpdateNetworkRequest,
)
from core.auth.authorize import authorize, resolve_network_role
from core.auth.context import AuthContext
from core.auth.permissions import (
    NETWORK_INVITE_CREATE,
    NETWORK_MEMBERS_READ,
    NETWORK_READ,
)
from core.db.session import get_db_session
from core.errors import NotFoundError
from core.network.service import (
    accept_invitation,
    change_member_role,
    change_network_status,
    create_invitation,
    create_network,
    get_members,
    leave_network,
    list_invitations,
    list_network_audit,
    list_user_networks,
    remove_member,
    revoke_invitation,
    update_network,
)

router = APIRouter()


# ── 工具 ──────────────────────────────────────────────────────────────


def _parse_uuid(raw: str, *, what: str = "资源") -> uuid.UUID:
    """解析路径里的 UUID。

    格式非法按 **404** 处理而不是 400 —— 与"不存在"保持同形，避免成为存在性探针
    （契约 §4：不可区分）。
    """
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise NotFoundError(f"{what}不存在") from exc


def _to_summary(network, role: str) -> NetworkSummary:
    return NetworkSummary(
        id=str(network.id),
        name=network.name,
        status=network.status,
        role=role,
        policy=dict(network.policy or {}),
        tenant_id=str(network.tenant_id),
        owner_id=str(network.owner_id),
        created_at=network.gmt_create.isoformat(),
    )


def _to_invitation(inv) -> InvitationSummary:
    return InvitationSummary(
        id=str(inv.id),
        email=inv.email,
        status=inv.status,
        expires_at=inv.expires_at.isoformat(),
        created_at=inv.gmt_create.isoformat(),
    )


# ── 网络 CRUD ─────────────────────────────────────────────────────────


@router.post("/networks", status_code=status.HTTP_201_CREATED)
async def create_network_endpoint(
    payload: CreateNetworkRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> NetworkSummary:
    """创建网络。创建者自动成为唯一 Owner，网络归属当前激活租户。"""
    network = await create_network(
        session,
        ctx,
        name=payload.name,
        policy=payload.policy.model_dump(by_alias=True) if payload.policy else None,
        request_id=request_id,
    )
    await session.commit()
    return _to_summary(network, "owner")


@router.get("/networks", response_model=list[NetworkSummary])
async def list_networks_endpoint(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[NetworkSummary]:
    """我所属的网络列表。

    只返回"我是 active 成员的、且属于当前租户的" —— 不做隐式放行。
    """
    rows = await list_user_networks(
        session, user_id=uuid.UUID(ctx.user_id), tenant_id=uuid.UUID(ctx.tenant_id)
    )
    return [_to_summary(network, role) for network, role in rows]


@router.get("/networks/{network_id}", response_model=NetworkDetail)
async def get_network_endpoint(
    network_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> NetworkDetail:
    """网络详情（需要 ``network.read``）。"""
    nid = _parse_uuid(network_id, what="网络")
    network = await authorize(session, ctx, permission=NETWORK_READ, network_id=nid)
    role = await resolve_network_role(
        session, network_id=nid, user_id=uuid.UUID(ctx.user_id)
    )
    members = await get_members(session, network_id=nid)
    summary = _to_summary(network, role or "member")
    return NetworkDetail(**summary.model_dump(), member_count=len(members))


@router.patch("/networks/{network_id}", response_model=NetworkSummary)
async def patch_network_endpoint(
    network_id: str,
    payload: UpdateNetworkRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> NetworkSummary:
    """修改信息 / 暂停 / 归档 / 恢复。

    契约 §3.2 把这几种操作合进一个 ``PATCH``：

    - ``name`` / ``policy`` → 需要 ``network.update``（Owner/Admin）；
    - ``status`` → 需要 ``network.archive``（**Owner 独占**）。

    同时传两者时两个权限都要有，顺序执行（先更新后改状态）。
    """
    nid = _parse_uuid(network_id, what="网络")

    network = None
    if payload.name is not None or payload.policy is not None:
        network = await update_network(
            session,
            ctx,
            network_id=nid,
            name=payload.name,
            policy=payload.policy.model_dump(by_alias=True) if payload.policy else None,
            request_id=request_id,
        )

    if payload.status is not None:
        network = await change_network_status(
            session, ctx, network_id=nid, target_status=payload.status, request_id=request_id
        )

    if network is None:
        # 什么都没传 —— 仍然走一次授权，保证行为一致（不暴露网络是否存在）
        network = await authorize(session, ctx, permission=NETWORK_READ, network_id=nid)

    await session.commit()
    role = await resolve_network_role(
        session, network_id=nid, user_id=uuid.UUID(ctx.user_id)
    )
    return _to_summary(network, role or "member")


# ── 成员 ──────────────────────────────────────────────────────────────


@router.get("/networks/{network_id}/members", response_model=list[MemberSummary])
async def list_members_endpoint(
    network_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[MemberSummary]:
    """成员列表（需要 ``network.members.read``）。"""
    nid = _parse_uuid(network_id, what="网络")
    await authorize(session, ctx, permission=NETWORK_MEMBERS_READ, network_id=nid)

    rows = await get_members(session, network_id=nid)
    return [
        MemberSummary(
            user_id=str(membership.user_id),
            display_name=user.display_name if user else None,
            email=user.email if user else None,
            role=membership.role,
            joined_at=membership.joined_at.isoformat(),
            version=membership.version,
        )
        for membership, user in rows
    ]


@router.patch(
    "/networks/{network_id}/members/{user_id}", response_model=MemberSummary
)
async def change_member_role_endpoint(
    network_id: str,
    user_id: str,
    payload: ChangeMemberRoleRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> MemberSummary:
    """调整成员角色（需要 ``network.members.manage``）。

    额外约束：Admin 不可动 Owner、不可授予 Owner；最后一位 Owner 不可降级。
    """
    nid = _parse_uuid(network_id, what="网络")
    target = _parse_uuid(user_id, what="成员")

    membership = await change_member_role(
        session,
        ctx,
        network_id=nid,
        target_user_id=target,
        new_role=payload.role,
        expected_version=payload.expected_version,
        request_id=request_id,
    )
    await session.commit()

    from core.db.models import User

    user = await session.get(User, target)
    return MemberSummary(
        user_id=str(membership.user_id),
        display_name=user.display_name if user else None,
        email=user.email if user else None,
        role=membership.role,
        joined_at=membership.joined_at.isoformat(),
        version=membership.version,
    )


@router.delete(
    "/networks/{network_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member_endpoint(
    network_id: str,
    user_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """移除成员（需要 ``network.members.manage``）。软删除，保留审计线索。"""
    nid = _parse_uuid(network_id, what="网络")
    target = _parse_uuid(user_id, what="成员")
    await remove_member(
        session, ctx, network_id=nid, target_user_id=target, request_id=request_id
    )
    await session.commit()


@router.post("/networks/{network_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_network_endpoint(
    network_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """主动退出网络。最后一位 Owner 会被拒（409）。"""
    nid = _parse_uuid(network_id, what="网络")
    await leave_network(session, ctx, network_id=nid, request_id=request_id)
    await session.commit()


# ── 邀请 ──────────────────────────────────────────────────────────────


@router.post(
    "/networks/{network_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationCreated,
)
async def create_invitation_endpoint(
    network_id: str,
    payload: CreateInvitationRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> InvitationCreated:
    """发起邀请（需要 ``network.invite.create``）。

    **响应里的 ``token`` 是唯一一次能看到明文的地方** —— 库里只有 hash。
    前端必须立刻把它拼成邀请链接展示/复制。
    """
    nid = _parse_uuid(network_id, what="网络")
    invitation, token = await create_invitation(
        session, ctx, network_id=nid, email=payload.email, request_id=request_id
    )
    await session.commit()
    return InvitationCreated(
        invitation=_to_invitation(invitation),
        token=token,
        accept_path=f"/api/v1/network-invitations/{token}/accept",
    )


@router.get(
    "/networks/{network_id}/invitations",
    response_model=list[InvitationSummary],
)
async def list_invitations_endpoint(
    network_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[InvitationSummary]:
    """待接受的邀请列表。

    权限码沿用契约 §3.2 的规定（``network.invite.create``）—— 能邀请的人才能看
    邀请列表；普通成员看不到。
    """
    nid = _parse_uuid(network_id, what="网络")
    await authorize(session, ctx, permission=NETWORK_INVITE_CREATE, network_id=nid)
    rows = await list_invitations(session, network_id=nid)
    return [_to_invitation(inv) for inv in rows]


@router.delete(
    "/networks/{network_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation_endpoint(
    network_id: str,
    invitation_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """撤销邀请（需要 ``network.invite.revoke``）。幂等。"""
    nid = _parse_uuid(network_id, what="网络")
    iid = _parse_uuid(invitation_id, what="邀请")
    await revoke_invitation(
        session, ctx, network_id=nid, invitation_id=iid, request_id=request_id
    )
    await session.commit()


@router.post("/network-invitations/{token}/accept", response_model=NetworkSummary)
async def accept_invitation_endpoint(
    token: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> NetworkSummary:
    """接受邀请（需要登录，且**邀请邮箱必须与登录账号一致**）。

    失败语义：无权限/不存在 → 404/403；已用/过期 → **410**；网络非 active → 409。
    """
    network = await accept_invitation(
        session, ctx, token=token, request_id=request_id
    )
    await session.commit()
    role = await resolve_network_role(
        session, network_id=network.id, user_id=uuid.UUID(ctx.user_id)
    )
    return _to_summary(network, role or "member")


# ── 审计（FR-N9）──────────────────────────────────────────────────────


@router.get(
    "/networks/{network_id}/audit",
    response_model=list[AuditEventSummary],
)
async def list_audit_endpoint(
    network_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[AuditEventSummary]:
    """网络审计日志（需要 ``network.audit.read`` = **Owner/Admin**）。

    新的在前；``limit`` 上限 200（审计表只追加，不做 COUNT(*) 总数）。

    可选 ``action`` 精确过滤，例如 ``network.agent.invoke`` 可以看这个网络的
    Agent 调用记录（含被拒绝的）。
    """
    nid = _parse_uuid(network_id, what="网络")
    rows = await list_network_audit(
        session,
        ctx,
        network_id=nid,
        limit=limit,
        offset=offset,
        action=action,
    )
    return [
        AuditEventSummary(
            id=str(event.id),
            action=event.action,
            result=event.result,
            actor_id=event.actor_id,
            actor_kind=event.actor_kind,
            target_type=event.target_type,
            target_id=event.target_id,
            request_id=event.request_id,
            created_at=event.gmt_create.isoformat(),
        )
        for event in rows
    ]

"""租户路由 —— ``/api/v1/tenants/*`` 与租户邀请接受（需求 8.1 / FR-A6）

契约 §3.1；Step 3 补充的邀请接口与网络邀请保持一致的形状。

## 为什么需要这组接口

注册时每个用户自动获得**自己的个人租户**，而网络**属且仅属一个租户**。所以
两人要共享网络，必须先同租户 —— 「加入他人租户」正是这里提供的能力。
**没有它，8.2 的多用户流程走不通。**

## 租户邀请的四个接口

```text
POST   /api/v1/tenants/{tenant_id}/invitations     发起邀请（Owner/Admin）
GET    /api/v1/tenants/{tenant_id}/invitations     待接受邀请列表
DELETE /api/v1/tenants/{tenant_id}/invitations/{id} 撤销邀请
POST   /api/v1/tenant-invitations/{token}/accept   接受邀请（需登录 + 邮箱匹配）
```

激活租户的切换在 ``me_routes.py`` 的 ``POST /api/v1/me/switch-tenant``。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context, get_request_id
from app.api.schemas.tenant_schemas import (
    CreateTenantInvitationRequest,
    CreateTenantRequest,
    TenantDetail,
    TenantInvitationCreated,
    TenantInvitationSummary,
    TenantMemberSummary,
)
from core.auth.context import AuthContext
from core.db.session import get_db_session
from core.errors import NotFoundError
from core.tenant.service import (
    accept_tenant_invitation,
    create_tenant,
    create_tenant_invitation,
    list_tenant_invitations,
    list_tenant_members,
    revoke_tenant_invitation,
)

router = APIRouter()


def _parse_uuid(raw: str, *, what: str = "资源") -> uuid.UUID:
    """解析路径 UUID；非法按 404 处理（与"不存在"同形，不做存在性探针）。"""
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise NotFoundError(f"{what}不存在") from exc


def _to_detail(tenant, role: str) -> TenantDetail:
    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        role=role,
        created_at=tenant.gmt_create.isoformat(),
    )


def _to_invitation(inv) -> TenantInvitationSummary:
    return TenantInvitationSummary(
        id=str(inv.id),
        email=inv.email,
        status=inv.status,
        expires_at=inv.expires_at.isoformat(),
        created_at=inv.gmt_create.isoformat(),
    )


# ── 创建 ──────────────────────────────────────────────────────────────


@router.post("/tenants", status_code=status.HTTP_201_CREATED, response_model=TenantDetail)
async def create_tenant_endpoint(
    payload: CreateTenantRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> TenantDetail:
    """创建租户，创建者成为 Owner。

    **不会自动切换激活租户** —— 想"创建后立即进入"，前端再调一次
    ``POST /api/v1/me/switch-tenant`` 即可。API 语义保持单一：切换永远是显式动作。
    """
    tenant, role = await create_tenant(
        session, ctx, name=payload.name, request_id=request_id
    )
    await session.commit()
    return _to_detail(tenant, role)


# ── 成员 ──────────────────────────────────────────────────────────────


@router.get("/tenants/{tenant_id}/members", response_model=list[TenantMemberSummary])
async def list_tenant_members_endpoint(
    tenant_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[TenantMemberSummary]:
    """租户成员列表。**只能看自己当前激活租户**的成员（不跨租户窥探）。"""
    tid = _parse_uuid(tenant_id, what="租户")
    if tid != uuid.UUID(ctx.tenant_id):
        raise NotFoundError("租户不存在")

    rows = await list_tenant_members(session, tenant_id=tid)
    return [
        TenantMemberSummary(
            user_id=str(membership.user_id),
            display_name=user.display_name if user else None,
            email=user.email if user else None,
            role=membership.role,
            joined_at=membership.gmt_create.isoformat(),
        )
        for membership, user in rows
    ]


# ── 邀请 ──────────────────────────────────────────────────────────────


@router.post(
    "/tenants/{tenant_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=TenantInvitationCreated,
)
async def create_tenant_invitation_endpoint(
    tenant_id: str,
    payload: CreateTenantInvitationRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> TenantInvitationCreated:
    """邀请邮箱加入租户（Owner/Admin）。

    响应里的 ``token`` 是**唯一一次**能看到明文的地方。
    """
    tid = _parse_uuid(tenant_id, what="租户")
    invitation, token = await create_tenant_invitation(
        session, ctx, tenant_id=tid, email=payload.email, request_id=request_id
    )
    await session.commit()
    return TenantInvitationCreated(
        invitation=_to_invitation(invitation),
        token=token,
        accept_path=f"/api/v1/tenant-invitations/{token}/accept",
    )


@router.get(
    "/tenants/{tenant_id}/invitations",
    response_model=list[TenantInvitationSummary],
)
async def list_tenant_invitations_endpoint(
    tenant_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[TenantInvitationSummary]:
    """待接受的租户邀请列表（Owner/Admin）。"""
    from core.errors import ForbiddenError

    tid = _parse_uuid(tenant_id, what="租户")
    if tid != uuid.UUID(ctx.tenant_id):
        raise NotFoundError("租户不存在")
    if ctx.tenant_role not in ("owner", "admin"):
        raise ForbiddenError("权限不足")

    rows = await list_tenant_invitations(session, tenant_id=tid)
    return [_to_invitation(inv) for inv in rows]


@router.delete(
    "/tenants/{tenant_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_tenant_invitation_endpoint(
    tenant_id: str,
    invitation_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """撤销租户邀请（Owner/Admin）。幂等。"""
    tid = _parse_uuid(tenant_id, what="租户")
    iid = _parse_uuid(invitation_id, what="邀请")
    await revoke_tenant_invitation(
        session, ctx, tenant_id=tid, invitation_id=iid, request_id=request_id
    )
    await session.commit()


@router.post(
    "/tenant-invitations/{token}/accept",
    response_model=TenantDetail,
)
async def accept_tenant_invitation_endpoint(
    token: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> TenantDetail:
    """接受租户邀请（需登录，且**邀请邮箱必须与登录账号一致**）。

    失败语义：已用/过期 → **410**；邮箱不匹配 → 403；租户不存在 → 404。

    接受后**不自动切换激活租户**。前端如需"加入即进入"，紧接着调用
    ``POST /api/v1/me/switch-tenant``。
    """
    tenant, role = await accept_tenant_invitation(
        session, ctx, token=token, request_id=request_id
    )
    await session.commit()
    return _to_detail(tenant, role)

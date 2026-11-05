"""当前用户路由 —— ``/api/v1/me``（需求 8.1）

契约见 ``docs/contract-08-auth-network.md`` §3.1。

## 这里的每个接口都只能看到"自己"

``/me`` 系列天然按 ``ctx.user_id`` 过滤，**绝不接受客户端传入的 user_id**。
最容易出错的是会话删除接口：必须校验目标会话属于当前用户，否则就是典型的
IDOR（越权删除他人会话）—— 见 ``design-08-isolation.md`` §7.2 的防护清单。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context, get_request_id
from app.api.schemas.auth_schemas import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    CurrentUserResponse,
    SessionSummary,
    TenantSummary,
)
from app.api.schemas.tenant_schemas import SwitchTenantRequest, TenantDetail
from core.auth.audit import (
    ACTION_PASSWORD_CHANGED,
    RESULT_DENIED,
    RESULT_SUCCESS,
    add_audit_event,
)
from core.auth.context import AuthContext
from core.auth.cookies import clear_session_cookie
from core.auth.passwords import PasswordValidationError
from core.auth.platform_admin import is_platform_admin
from core.auth.ratelimit import password_change_limiter
from core.auth.service import (
    CurrentPasswordMismatch,
    change_password,
    find_user_by_email,
    list_sessions,
    list_user_tenants,
    revoke_session,
)
from core.db.session import get_db_session
from core.tenant.service import switch_active_tenant

router = APIRouter()


@router.post("/me/password", response_model=ChangePasswordResponse)
async def change_password_endpoint(
    payload: ChangePasswordRequest,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> ChangePasswordResponse:
    """修改密码（PRD FR-A5）—— 成功后**吊销该用户全部会话**。

    ## 为什么失败返回 400 而不是 401

    前端对 401 注册了全局处理器（切到 `expired` 并踢回登录页）。「当前密码打错」
    不该把用户登出，所以这里用 400。

    ## 失败也要限流 + 审计

    改密接口同样校验当前密码，持有一个有效会话的攻击者可以在这里反复猜测，
    而且**完全绕过登录接口的限流**。所以失败按账号计数，超限返回 429；
    每次失败都写审计（`result='denied'`）。
    """
    limiter = password_change_limiter()
    # peek 而不是 hit：成功改密不该消耗失败配额，只有当前密码错才计数
    window = limiter.peek(ctx.user_id)
    if not window.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="当前密码错误次数过多，请稍后再试",
            headers={"Retry-After": str(window.retry_after)},
        )

    user = await find_user_by_email(session, ctx.email or "")
    if user is None:
        # 会话有效但用户查不到（历史脏数据）—— 按未认证处理
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证或会话已失效"
        )

    try:
        revoked = await change_password(
            session,
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except CurrentPasswordMismatch as exc:
        limiter.hit(ctx.user_id)
        add_audit_event(
            session,
            action=ACTION_PASSWORD_CHANGED,
            result=RESULT_DENIED,
            request_id=request_id,
            actor_id=ctx.user_id,
            tenant_id=uuid.UUID(ctx.tenant_id),
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except PasswordValidationError as exc:
        # 新密码不合策略：不是"猜密码"，不计入失败次数
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    add_audit_event(
        session,
        action=ACTION_PASSWORD_CHANGED,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=ctx.user_id,
        tenant_id=uuid.UUID(ctx.tenant_id),
    )
    await session.commit()

    # 成功后清零失败计数，避免历史失败把正常用户卡住
    limiter.reset(ctx.user_id)

    # 全部会话已吊销（含当前这条），顺手清掉 Cookie —— 否则浏览器会继续带着
    # 一个必然 401 的令牌，前端日志里满是噪音。
    clear_session_cookie(response)

    return ChangePasswordResponse(
        revoked_sessions=revoked,
        message="密码已修改，所有设备需要重新登录",
    )


@router.post("/me/switch-tenant", response_model=TenantDetail)
async def switch_tenant(
    payload: SwitchTenantRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> TenantDetail:
    """切换当前会话的激活租户（契约 §3.1，PRD FR-A6）。

    **服务端校验 active membership** —— 客户端提交的 ``tenantId`` 只是意向。
    不属于该租户时返回 403 且**保持原租户上下文**（不能只改前端状态）。

    激活租户存在服务端的 ``session.active_tenant_id``，所以切换是**服务端事实**，
    客户端无法通过改请求头伪造。
    """
    tenant, role = await switch_active_tenant(
        session, ctx, tenant_id=uuid.UUID(payload.tenant_id), request_id=request_id
    )
    await session.commit()
    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        role=role,
        created_at=tenant.gmt_create.isoformat(),
    )


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> CurrentUserResponse:
    """当前用户 + 激活租户 + 所属租户列表（PRD FR-A3 / FR-A6）。"""
    tenants = await list_user_tenants(session, user_id=uuid.UUID(ctx.user_id))
    return CurrentUserResponse(
        user_id=ctx.user_id,
        email=ctx.email,
        display_name=ctx.display_name,
        active_tenant_id=ctx.tenant_id,
        tenant_role=ctx.tenant_role,
        # 平台管理员是**部署级**角色（SOTABAND_PLATFORM_ADMINS），与 tenant_role
        # 无关；前端用它门控系统配置/全局 LLM/查看他人数据集等入口。
        is_platform_admin=is_platform_admin(ctx),
        tenants=[
            TenantSummary(id=str(t.id), name=t.name, slug=t.slug, role=role)
            for t, role in tenants
        ],
    )


@router.get("/me/tenants", response_model=list[TenantSummary])
async def get_my_tenants(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[TenantSummary]:
    """我所属的租户列表（PRD FR-A6）。"""
    tenants = await list_user_tenants(session, user_id=uuid.UUID(ctx.user_id))
    return [
        TenantSummary(id=str(t.id), name=t.name, slug=t.slug, role=role)
        for t, role in tenants
    ]


@router.get("/me/sessions", response_model=list[SessionSummary])
async def get_my_sessions(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[SessionSummary]:
    """当前用户的活跃会话列表（**不含令牌**，只暴露可识别的元信息）。"""
    rows = await list_sessions(session, user_id=uuid.UUID(ctx.user_id))
    return [
        SessionSummary(
            id=str(s.id),
            created_at=s.gmt_create.isoformat(),
            expires_at=s.expires_at.isoformat(),
            user_agent=s.user_agent,
            ip=s.ip,
            current=str(s.id) == ctx.session_id,
        )
        for s in rows
    ]


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_session(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """吊销指定会话（单设备退出）。

    ⚠️ **必须校验归属**：``session_id`` 来自 URL，是可猜的。若不校验 owner，
    任何登录用户都能吊销他人的会话 —— 这是 IDOR（隔离设计 §7.2）。

    归属不匹配时返回 **404 而不是 403**：403 等于确认"这个会话确实存在"，
    会泄露信息（契约 §4 的 404 语义）。
    """
    try:
        target_id = uuid.UUID(session_id)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在"
        ) from exc

    from core.db.models import UserSession

    target = await session.get(UserSession, target_id)
    if target is None or str(target.user_id) != ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在"
        )

    await revoke_session(session, session_id=target_id)
    await session.commit()

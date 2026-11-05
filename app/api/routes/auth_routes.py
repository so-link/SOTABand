"""认证路由 —— ``/api/auth/*``（需求 8.1）

契约见 ``docs/contract-08-auth-network.md`` §3.1、§3.4、§4、§5。

## 为什么挂在 ``/api/auth/*`` 而不是 PRD 写的 ``/auth/*``

``frontend/vite.config.ts`` 只代理 ``/api``。PRD 的 ``/auth/*`` 不在代理前缀内，
开发期会变成真正的跨域请求，配合 ``allow_credentials`` 与 CORS 通配符会让
Cookie 会话**静默失败**。收敛到 ``/api`` 下享受同源代理，是这个决定的原因
（ADR-08-05）。

## 统一失败响应

登录失败对外一律 ``401`` + 同一句 detail，**不区分**「邮箱不存在」「密码错误」
「账号禁用」——三者差异只写审计日志。用户不存在时还会跑一次假 bcrypt，
抹平响应耗时（威胁模型 T3）。

**注册重复邮箱例外**：会明确返回 409「该邮箱已被注册」。原因是 MVP 没有邮箱
验证 —— 若不告诉用户，注册功能就不可用。补偿措施是 IP 维度限流（默认 5 次/小时）。
这是在 PRD FR-A1 的严格性与可用性之间做的取舍，已记录在此。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context, get_optional_auth, get_request_id
from app.api.schemas.auth_schemas import (
    LoginRequest,
    RegisterRequest,
    SessionProbeResponse,
    UserSummary,
)
from core.auth.audit import (
    ACTION_LOGIN,
    ACTION_LOGOUT,
    ACTION_LOGOUT_ALL,
    ACTION_REGISTER,
    RESULT_DENIED,
    RESULT_FAILURE,
    RESULT_SUCCESS,
    add_audit_event,
)
from core.auth.context import AuthContext
from core.auth.cookies import clear_session_cookie, set_session_cookie
from core.auth.passwords import PasswordValidationError, validate_password
from core.auth.ratelimit import login_ip_limiter, login_throttle, register_limiter
from core.auth.service import (
    AccountDisabled,
    EmailAlreadyExists,
    InvalidCredentials,
    authenticate,
    issue_session,
    plaintext_token_of,
    primary_tenant,
    register_user,
    revoke_all_sessions,
    revoke_session,
)
from core.db.session import get_db_session

router = APIRouter()


def _client_ip(request: Request) -> str:
    """取客户端 IP。反向代理后需读 X-Forwarded-For —— MVP 无代理，先不处理。"""
    return request.client.host if request.client else "unknown"


def _too_many_requests(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="请求过于频繁，请稍后再试",
        headers={"Retry-After": str(retry_after)},
    )


def _generic_login_failure() -> HTTPException:
    """登录失败的唯一对外形态。

    ⚠️ 不要为不同原因返回不同 detail —— 那会变成用户枚举信道。
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确"
    )


def _user_summary(user) -> UserSummary:
    return UserSummary(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
    )


# ── 注册 ────────────────────────────────────────────────────────────


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """注册并自动登录（201 + Set-Cookie）。

    创建**五条记录**：user / user_credential / user_identity / tenant /
    tenant_membership。它们在同一事务内，任何一步失败整体回滚。
    """
    ip = _client_ip(request)
    request_id = getattr(request.state, "request_id", None) or request_id

    allowed = register_limiter().hit(f"register:{ip}")
    if not allowed.allowed:
        add_audit_event(
            session,
            action=ACTION_REGISTER,
            result=RESULT_DENIED,
            request_id=request_id,
            actor_id=ip[:64],
            target_type="register",
        )
        await session.commit()
        raise _too_many_requests(allowed.retry_after)

    # 密码策略必须在任何写操作之前 —— 超长密码会让 bcrypt 抛 ValueError
    try:
        validate_password(payload.password)
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    try:
        principal = await register_user(
            session,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except EmailAlreadyExists as exc:
        # register_user 内部已 rollback，这里可以安全地写审计
        add_audit_event(
            session,
            action=ACTION_REGISTER,
            result=RESULT_FAILURE,
            request_id=request_id,
            actor_id=str(exc)[:64],
            target_type="register",
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该邮箱已被注册"
        ) from exc

    add_audit_event(
        session,
        action=ACTION_REGISTER,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=str(principal.user.id),
        tenant_id=principal.tenant.id,
        target_type="user",
        target_id=str(principal.user.id),
    )
    await session.commit()

    set_session_cookie(response, plaintext_token_of(principal.session))
    return {
        "user": _user_summary(principal.user).model_dump(),
        "tenant": {
            "id": str(principal.tenant.id),
            "name": principal.tenant.name,
            "slug": principal.tenant.slug,
            "role": principal.tenant_role,
        },
    }


# ── 登录 ────────────────────────────────────────────────────────────


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """账号密码登录。成功后签发**全新**会话令牌（防会话固定 T5）。"""
    ip = _client_ip(request)
    request_id = getattr(request.state, "request_id", None) or request_id
    account_key = payload.email.strip().lower()

    # IP 维度：防单机扫很多账号（与下面的账号维度互补）
    ip_state = login_ip_limiter().hit(f"login:{ip}")
    if not ip_state.allowed:
        add_audit_event(
            session,
            action=ACTION_LOGIN,
            result=RESULT_DENIED,
            request_id=request_id,
            actor_id=ip[:64],
            target_type="login",
        )
        await session.commit()
        raise _too_many_requests(ip_state.retry_after)

    # 账号维度：防盯住一个账号猛试
    throttle = login_throttle()
    state = throttle.check(account_key)
    if not state.allowed:
        add_audit_event(
            session,
            action=ACTION_LOGIN,
            result=RESULT_DENIED,
            request_id=request_id,
            actor_id=account_key[:64],
            target_type="login",
        )
        await session.commit()
        raise _too_many_requests(state.retry_after)

    try:
        user = await authenticate(session, email=payload.email, password=payload.password)
    except (InvalidCredentials, AccountDisabled):
        throttle.record_failure(account_key)
        add_audit_event(
            session,
            action=ACTION_LOGIN,
            result=RESULT_FAILURE,
            request_id=request_id,
            actor_id=account_key[:64],
            target_type="login",
        )
        await session.commit()
        raise _generic_login_failure() from None

    throttle.reset(account_key)

    tenant_row = await primary_tenant(session, user.id)
    if tenant_row is None:
        # 注册时必然创建个人租户；走到这里说明数据被破坏。不静默兜底，明确报错。
        add_audit_event(
            session,
            action=ACTION_LOGIN,
            result=RESULT_FAILURE,
            request_id=request_id,
            actor_id=str(user.id),
            target_type="login",
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号缺少可用的租户空间，请联系管理员",
        )

    tenant, role = tenant_row
    issued = await issue_session(
        session, user=user, user_agent=request.headers.get("user-agent"), ip=ip
    )

    add_audit_event(
        session,
        action=ACTION_LOGIN,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=str(user.id),
        tenant_id=tenant.id,
        target_type="user",
        target_id=str(user.id),
    )
    await session.commit()

    set_session_cookie(response, plaintext_token_of(issued))
    return {
        "user": _user_summary(user).model_dump(),
        "activeTenantId": str(tenant.id),
        "tenantRole": role,
    }


# ── 会话探测 ────────────────────────────────────────────────────────


@router.get("/session", response_model=SessionProbeResponse)
async def session_probe(
    ctx: AuthContext | None = Depends(get_optional_auth),
) -> SessionProbeResponse:
    """会话探测：**未登录返回 200 + ``authenticated: false``**。

    契约 §3.1 特意要求如此：这个接口是给前端 Auth 状态机探测用的，返回 401 会让
    浏览器控制台刷错误、也会误触发拦截器逻辑。
    """
    if ctx is None:
        return SessionProbeResponse(authenticated=False)
    return SessionProbeResponse(
        authenticated=True,
        user=UserSummary(
            id=ctx.user_id, email=ctx.email, display_name=ctx.display_name or ""
        ),
        active_tenant_id=ctx.tenant_id,
        tenant_role=ctx.tenant_role,
    )


# ── 登出 ────────────────────────────────────────────────────────────


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """退出当前设备。**幂等** —— 重复调用仍返回 204。"""
    request_id = getattr(request.state, "request_id", None) or request_id
    await revoke_session(session, session_id=uuid.UUID(ctx.session_id))
    add_audit_event(
        session,
        action=ACTION_LOGOUT,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=ctx.user_id,
        tenant_id=uuid.UUID(ctx.tenant_id),
        target_type="session",
        target_id=ctx.session_id,
    )
    await session.commit()

    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """退出全部设备（PRD FR-A3）。

    注意：这会同时吊销**当前**会话，前端应清空认证状态并跳登录页。
    """
    request_id = getattr(request.state, "request_id", None) or request_id
    count = await revoke_all_sessions(session, user_id=uuid.UUID(ctx.user_id))
    add_audit_event(
        session,
        action=ACTION_LOGOUT_ALL,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=ctx.user_id,
        tenant_id=uuid.UUID(ctx.tenant_id),
        target_type="user",
        target_id=ctx.user_id,
    )
    await session.commit()

    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    response.headers["X-Revoked-Count"] = str(count)
    return response

"""FastAPI 依赖 —— 认证上下文与权限校验。

冻结契约见 ``docs/contract-08-auth-network.md`` §1.2。

## 下游唯一正确的用法

```python
@router.get("/api/tool/shared")
async def list_shared(ctx: AuthContext = Depends(get_auth_context)):
    tenant_id = ctx.tenant_id          # ✅ 唯一可信来源
    # request.query_params["user_id"]  # ❌ 禁止：客户端可伪造
```

## 401 与 403 的分工

- **401**：未认证 / 会话过期 / 会话已吊销 → 前端应尝试恢复会话或跳登录；
- **403**：已认证但无权限 → 前端只提示"权限不足"，**不重试、不刷新**。

在这个模块里，``get_auth_context`` 只产生 401；403 由 ``require_role`` 等
授权依赖产生。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from core.auth.context import AuthContext
from core.auth.service import Principal, SessionInvalid, load_principal
from core.auth.workload import WorkloadClaims, WorkloadTokenError, verify_workload_token
from core.db.session import get_db_session

# ── 类型别名（让依赖签名更短、下游更好读）────────────────────────────

DbSession = AsyncSession


# ── 请求 id ─────────────────────────────────────────────────────────


async def get_request_id(request: Request) -> str:
    """取 ``RequestContextMiddleware`` 分配的 request id；缺失时兜底生成。"""
    rid = getattr(request.state, "request_id", None)
    return rid or uuid.uuid4().hex


# ── 主体构造 ────────────────────────────────────────────────────────


def build_auth_context(principal: Principal, request_id: str) -> AuthContext:
    """把 ``Principal`` 转成冻结契约里的 ``AuthContext``。

    注意：``tenant_id`` 与 ``tenant_role`` 来自**当前激活租户**（Step 2 是用户的
    个人租户；Step 3 起支持切换）。
    """
    return AuthContext(
        user_id=str(principal.user.id),
        session_id=str(principal.session.id),
        tenant_id=str(principal.tenant.id),
        tenant_role=principal.tenant_role,
        request_id=request_id,
        email=principal.user.email,
        display_name=principal.user.display_name,
    )


# ── 会话令牌读取 ────────────────────────────────────────────────────


def read_session_token(request: Request) -> str:
    """从 Cookie 读会话令牌。明文令牌**只存在于 Cookie 与内存**，不落库。"""
    return request.cookies.get(settings.auth.cookie_name, "") or ""


# ── 依赖 ────────────────────────────────────────────────────────────


async def get_optional_auth(
    request: Request,
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext | None:
    """可选认证：未登录/会话失效时返回 ``None`` 而不是抛 401。

    用于 ``GET /api/auth/session`` 这类"探测"接口 —— 契约 §3.1 要求它未登录时
    返回 200，否则前端状态机会把正常探测当成错误。
    """
    token = read_session_token(request)
    if not token:
        return None
    try:
        principal = await load_principal(session, token=token)
    except SessionInvalid:
        return None
    return build_auth_context(principal, request_id)


async def get_auth_context(
    request: Request,
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    """必需认证。任何不满足条件的情况一律 401。

    失败原因（令牌缺失 / 不存在 / 已吊销 / 已过期 / 用户禁用 / 无可用租户）
    **不对外区分** —— 统一 ``detail`` 避免泄露会话状态细节，差异留给服务端日志。
    """
    token = read_session_token(request)
    try:
        principal = await load_principal(session, token=token)
    except SessionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证或会话已失效",
            headers={"WWW-Authenticate": "Cookie"},
        ) from exc
    return build_auth_context(principal, request_id)


def require_role(*allowed_roles: str) -> Callable[..., Awaitable[AuthContext]]:
    """要求当前租户角色在允许集合内。不满足抛 403。

    第 6 步以后的网络级授权（``network.*`` 权限码）是 Step 4 的事，见
    ``core/auth/authorize.py``（尚未实现）。
    """

    async def _dependency(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if ctx.tenant_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="权限不足"
            )
        return ctx

    return _dependency


# ── 工作负载身份（Agent 相互通信，PRD FR-N7）─────────────────────────


def read_workload_token(request: Request) -> str:
    """从 ``Authorization: Bearer <token>`` 读工作负载令牌。

    **只认 Authorization 头，绝不回退到 Cookie** —— 这是 PRD FR-N7 的硬性要求：
    Agent 必须使用独立的工作负载身份，不能复用用户的会话 Cookie。
    回退到 Cookie 会让"用用户身份冒充 Agent"变得可能，且审计里的主体会错。
    """
    header = request.headers.get("Authorization", "") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


async def get_workload_claims(request: Request) -> WorkloadClaims:
    """必需的工作负载身份。任何不满足条件一律 401。

    ⚠️ 这里**不调用** ``get_auth_context`` —— 工作负载请求不带 Cookie，
    也**不应该**带。带上 Cookie 的请求走的是用户身份路径，与本依赖无关。
    """
    token = read_workload_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少工作负载令牌（Agent 调用必须使用安装凭证，不能用用户会话）",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_workload_token(token)
    except WorkloadTokenError as exc:
        # 不区分"格式错/签名错/过期" —— 对外统一，细节留给日志
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="工作负载令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


__all__ = [
    "DbSession",
    "build_auth_context",
    "get_auth_context",
    "get_optional_auth",
    "get_request_id",
    "get_workload_claims",
    "read_session_token",
    "read_workload_token",
    "require_role",
]

"""认证业务逻辑 —— 注册 / 登录 / 会话校验与吊销。

这是 Step 2 的核心。路由层只做 HTTP 适配（解析请求、映射错误码、下发 Cookie），
**所有规则都在这里**，便于单独测试、也便于非 HTTP 场景（CLI、后续的 Phase 5
Bearer Token）复用。

设计见 ``docs/design-08-01-auth.md``；契约见 ``docs/contract-08-auth-network.md``。

## 事务边界

本模块的函数**不提交事务**，只 ``flush()`` 拿到 id 并在需要时触发唯一约束。
提交由调用方（路由/``session_scope``）负责 —— 这样"注册"这种需要写入
用户 + 凭证 + 身份 + 租户 + 成员五条记录的操作才能是一个原子事务。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from core.auth.passwords import (
    PasswordValidationError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from core.auth.tokens import generate_session_token, hash_session_token
from core.db.base import utcnow
from core.db.models import (
    MEMBERSHIP_ACTIVE,
    TENANT_ROLE_OWNER,
    Tenant,
    TenantMembership,
    User,
    UserCredential,
    UserIdentity,
    UserSession,
)

# ── 领域异常（路由层负责映射成 HTTP 状态码）─────────────────────────


class AuthError(Exception):
    """认证领域错误基类。"""


class EmailAlreadyExists(AuthError):
    """邮箱已被注册。

    ⚠️ 对外**不得**直接暴露为"该邮箱已注册" —— 那会让攻击者批量探测哪些邮箱
    有账号（威胁模型 T3 用户枚举）。路由层必须把它和"密码错误"映射成同一个响应。
    """


class InvalidCredentials(AuthError):
    """邮箱或密码不正确（不区分是哪个错了，防枚举）。"""


class AccountDisabled(AuthError):
    """账号已被禁用（PRD FR-A5：禁用后新请求立即被拒）。"""


class SessionInvalid(AuthError):
    """会话不存在 / 已吊销 / 已过期 / 所属用户被禁用。"""


class CurrentPasswordMismatch(AuthError):
    """改密码时「当前密码」不正确。

    ⚠️ 路由层必须把它映射成 **400 而不是 401**：
    前端对 401 注册了全局处理器（切到 `expired` 状态并踢回登录页），
    用 401 会让用户只是打错当前密码就被登出，体验与语义都错。
    """


# ── 返回结构 ────────────────────────────────────────────────────────


@dataclass
class Principal:
    """一次已认证请求的主体素材（``AuthContext`` 由 deps 层组装）。"""

    user: User
    session: UserSession
    tenant: Tenant
    tenant_role: str


# ── 邮箱与租户名 ────────────────────────────────────────────────────


def normalize_email(email: str) -> str:
    """邮箱规范化：trim + 整体小写。

    RFC 5321 规定本地部分理论上区分大小写，但没有任何主流邮件服务商实际这样做。
    整体小写可避免 ``Alice@x.com`` 与 ``alice@x.com`` 被当成两个账号 —— 这是
    实际运维里更常见的问题。
    """
    return (email or "").strip().lower()


def _make_tenant_slug(email: str) -> str:
    """生成租户 slug。

    用 ``<邮箱本地部分>-<8位随机>`` 而不是纯邮箱本地部分：后者会冲突（两个
    ``alice@``），而重试循环比直接加随机后缀更啰嗦。
    """
    local = (email or "user").split("@")[0] or "user"
    base = re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-")[:48] or "user"
    return f"{base}-{uuid.uuid4().hex[:8]}"


# ── 查询 ────────────────────────────────────────────────────────────


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(
        select(User).where(User.email == normalize_email(email))
    )
    return result.scalar_one_or_none()


async def get_credential(
    session: AsyncSession, user_id: uuid.UUID
) -> UserCredential | None:
    """按 ``user_id`` 查密码凭证。

    ⚠️ **不能用 ``session.get(UserCredential, user_id)``** —— 按阿里手册每张表都有
    独立的 ``id`` 主键后，``user_id`` 已退为普通唯一列，``session.get`` 是按**主键**
    查的，会永远查不到。这是"加 id 主键"那次重构最容易踩的陷阱，集成测试
    ``test_login_success`` 就是它的回归守卫。
    """
    result = await session.execute(
        select(UserCredential).where(UserCredential.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def primary_tenant(session: AsyncSession, user_id: uuid.UUID) -> tuple[Tenant, str] | None:
    """返回用户的"主租户"及角色。

    Step 2 阶段每个用户只有一个个人租户（注册时创建），所以取最早加入的那个即可。
    真正能在多租户间切换是 Step 3 的事（契约 §3.1 ``/me/switch-tenant``）。

    **不做隐式兜底**：查不到 active 成员关系就返回 ``None``，由调用方判定为
    异常状态 —— 悄悄造一个租户出来会让数据问题被掩盖。
    """
    result = await session.execute(
        select(Tenant, TenantMembership.role)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.status == MEMBERSHIP_ACTIVE,
            Tenant.status == "active",
        )
        .order_by(TenantMembership.gmt_create.asc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    tenant, role = row
    return tenant, role


async def list_user_tenants(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[tuple[Tenant, str]]:
    """列出用户所属的全部 active 租户及角色（PRD FR-A6「查看所属租户」）。"""
    result = await session.execute(
        select(Tenant, TenantMembership.role)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.status == MEMBERSHIP_ACTIVE,
        )
        .order_by(TenantMembership.gmt_create.asc())
    )
    return [(tenant, role) for tenant, role in result.all()]


async def get_tenant_membership(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> TenantMembership | None:
    """取用户在指定租户的成员关系（不论状态）。"""
    result = await session.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def resolve_active_tenant(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    preferred_tenant_id: uuid.UUID | None,
) -> tuple[Tenant, str] | None:
    """解析用户的**激活租户**（PRD FR-A6）。

    优先用会话上记录的 ``active_tenant_id``；若它已失效（成员被移除、租户被停用/归档、
    租户被删），**回退到主租户**而不是直接 401 —— 否则用户会被一个过期偏好锁在门外。

    注意：回退是"容错"，不是"静默改偏好"。``active_tenant_id`` 的清理由
    ``switch_tenant`` 负责；这里只是不让失效值造成登录失败。
    """
    if preferred_tenant_id is not None:
        membership = await get_tenant_membership(
            session, tenant_id=preferred_tenant_id, user_id=user_id
        )
        if membership is not None and membership.status == MEMBERSHIP_ACTIVE:
            tenant = await session.get(Tenant, preferred_tenant_id)
            if tenant is not None and tenant.status == "active":
                return tenant, membership.role

    return await primary_tenant(session, user_id)


# ── 注册 ────────────────────────────────────────────────────────────


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> Principal:
    """创建用户 + 个人租户，并返回可用于登录的主体。

    调用方**必须先** :func:`core.auth.passwords.validate_password`（超长密码会让
    bcrypt 抛 ``ValueError``，且要在做任何写操作之前就拒绝）。

    五条记录在同一事务内写入：``user`` / ``user_credential`` / ``user_identity`` /
    ``tenant`` / ``tenant_membership``。任何一步失败都整体回滚 —— 否则会出现
    "有账号但无租户"的半成品，用户登录后无处可去。
    """
    normalized = normalize_email(email)
    name = (display_name or "").strip() or normalized.split("@")[0]

    # bcrypt 是慢操作（~250ms），放在任何事务/锁之前
    password_hash = hash_password(password)

    user = User(email=normalized, display_name=name[:128], status="active")
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # 并发注册同一邮箱只有一个能过 —— 靠的是数据库唯一约束，不是"先查后插"
        raise EmailAlreadyExists(normalized) from exc

    session.add_all(
        [
            UserCredential(user_id=user.id, password_hash=password_hash, algorithm="bcrypt"),
            UserIdentity(user_id=user.id, issuer="local", subject=str(user.id)),
        ]
    )

    tenant = Tenant(name=f"{name[:100]} 的空间", slug=_make_tenant_slug(normalized))
    session.add(tenant)
    await session.flush()

    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=TENANT_ROLE_OWNER,  # 个人租户的创建者即 Owner（PRD FR-A1）
        status=MEMBERSHIP_ACTIVE,
    )
    session.add(membership)
    await session.flush()

    issued = await issue_session(session, user=user)

    return Principal(user=user, session=issued, tenant=tenant, tenant_role=TENANT_ROLE_OWNER)


# ── 登录 ────────────────────────────────────────────────────────────


async def authenticate(
    session: AsyncSession, *, email: str, password: str
) -> User:
    """校验邮箱密码，返回用户。

    失败一律抛 :class:`InvalidCredentials`，**不区分"邮箱不存在"与"密码错误"**
    —— 差异会变成用户枚举信道（威胁模型 T3）。

    用户不存在时也会跑一次 bcrypt（在 :func:`verify_password` 内部），抹平耗时。
    """
    user = await find_user_by_email(session, email)
    credential = await get_credential(session, user.id) if user else None

    ok = verify_password(password, credential.password_hash if credential else None)

    if user is None or credential is None or not ok:
        raise InvalidCredentials()

    if user.status != "active":
        # 密码正确但账号禁用：对外与密码错误同响应，差异只进审计
        raise AccountDisabled()

    # 登录成功时按需升级哈希（提高 work factor 或换算法后用户无感迁移）
    if needs_rehash(credential.password_hash):
        credential.password_hash = hash_password(password)
        credential.algorithm = "bcrypt"
        credential.changed_at = utcnow()

    return user


# ── 会话 ────────────────────────────────────────────────────────────


async def issue_session(
    session: AsyncSession, *, user: User, user_agent: str | None = None, ip: str | None = None
) -> UserSession:
    """签发新会话（**不返回明文令牌**，见 :func:`issue_session_token`）。

    登录成功后**必须签发全新令牌**，绝不复用登录请求里携带的任何会话标识
    —— 这是会话固定（威胁模型 T5）的标准对策。
    """
    token = generate_session_token()
    user_session = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=utcnow() + timedelta(days=settings.auth.session_ttl_days),
        user_agent=(user_agent or "")[:1000] or None,
        ip=ip,
    )
    session.add(user_session)
    await session.flush()

    # 明文令牌只在本次调用的内存里存在一瞬：调用方拿去 Set-Cookie。数据库只存 hash。
    user_session._plaintext_token = token  # type: ignore[attr-defined]
    return user_session


def plaintext_token_of(user_session: UserSession) -> str:
    """取出 :func:`issue_session` 暂存的明文令牌。"""
    token = getattr(user_session, "_plaintext_token", None)
    if not token:
        raise RuntimeError("该会话没有刚签发的明文令牌；它只能用于 Set-Cookie 当次")
    return token


async def load_principal(session: AsyncSession, *, token: str) -> Principal:
    """用明文令牌解析出主体。任何不满足条件的情况都抛 :class:`SessionInvalid`。

    校验顺序（对应契约 §6 的前几步）：

    1. 令牌非空；
    2. ``session.token_hash`` 命中；
    3. 未吊销；
    4. 未过期；
    5. 所属用户存在且 ``status='active'``（PRD FR-A5：禁用后立即失效）；
    6. 用户有 active 租户成员关系。

    第 5 步是无外键方案的直接代价：以前可以靠外键保证 ``user_id`` 有效，
    现在必须自己 join 校验。
    """
    if not token:
        raise SessionInvalid("缺少会话令牌")

    result = await session.execute(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    user_session = result.scalar_one_or_none()
    if user_session is None:
        raise SessionInvalid("会话不存在")
    if user_session.revoked_at is not None:
        raise SessionInvalid("会话已吊销")
    if user_session.expires_at <= utcnow():
        raise SessionInvalid("会话已过期")

    user = await session.get(User, user_session.user_id)
    if user is None:
        # 无外键方案下可能出现孤儿会话（user 被直接删掉）。抛 401 而不是 500。
        raise SessionInvalid("会话所属用户不存在")
    if user.status != "active":
        raise SessionInvalid("用户已被禁用")

    # 激活租户：优先会话上记录的选择，失效则回退到主租户（见 resolve_active_tenant）
    tenant_row = await resolve_active_tenant(
        session, user_id=user.id, preferred_tenant_id=user_session.active_tenant_id
    )
    if tenant_row is None:
        raise SessionInvalid("用户没有可用的租户")

    tenant, role = tenant_row
    return Principal(user=user, session=user_session, tenant=tenant, tenant_role=role)


async def revoke_session(session: AsyncSession, *, session_id: uuid.UUID) -> bool:
    """吊销单个会话。返回是否真的改动了（已吊销的返回 False，但调用方仍可视为成功）。"""
    user_session = await session.get(UserSession, session_id)
    if user_session is None:
        return False
    if user_session.revoked_at is None:
        user_session.revoked_at = utcnow()
        await session.flush()
        return True
    return False


async def revoke_all_sessions(
    session: AsyncSession, *, user_id: uuid.UUID, except_session_id: uuid.UUID | None = None
) -> int:
    """吊销某用户的全部未吊销会话，返回受影响条数。

    触发时机（PRD FR-A3/A5）：退出全部设备、改密码、禁用用户。
    """
    result = await session.execute(
        select(UserSession).where(
            UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
        )
    )
    count = 0
    now = utcnow()
    for user_session in result.scalars():
        if except_session_id is not None and user_session.id == except_session_id:
            continue
        user_session.revoked_at = now
        count += 1
    if count:
        await session.flush()
    return count


async def list_sessions(session: AsyncSession, *, user_id: uuid.UUID) -> list[UserSession]:
    """列出该用户未吊销且未过期的会话（供"会话列表"接口）。"""
    result = await session.execute(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .order_by(UserSession.gmt_create.desc())
    )
    return list(result.scalars())


async def change_password(
    session: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
) -> int:
    """修改密码并**吊销该用户全部会话**（PRD FR-A5）。返回被吊销的会话数。

    ## 为什么吊销「全部」而不是保留当前会话

    PRD FR-A5 的原文是「修改密码/禁用用户/退出全部设备后，**既有会话全部吊销**」。
    按字面执行。

    代价是用户改完密码要重新登录一次；收益是**如果密码是因为怀疑泄露而改的，
    那些可能已经落到攻击者手里的会话会立刻失效** —— 而这正是改密码最常见的动机。
    保留当前会话会让"改了密码但攻击者还能用旧会话"成为可能。

    ## 校验顺序

    1. 当前密码正确？（错 → :class:`CurrentPasswordMismatch`，路由映射 400）
    2. 新密码符合策略？（复用 :func:`core.auth.passwords.validate_password`）
    3. 新旧密码不同？（相同则拒绝 —— 否则"改密码"毫无意义却会踢掉所有设备）
    4. 写库 → 吊销会话

    调用方（路由）负责失败时的限流计数与审计；本函数只做业务判定与写入。
    """
    credential = await get_credential(session, user.id)
    if credential is None:
        # 极端情况：用户存在但凭证缺失（历史脏数据 / 迁移中断）
        raise CurrentPasswordMismatch("当前密码不正确")

    if not verify_password(current_password, credential.password_hash):
        raise CurrentPasswordMismatch("当前密码不正确")

    # 密码策略：长度 8-20 且需含字母和数字（core.auth.passwords）
    validate_password(new_password)

    if verify_password(new_password, credential.password_hash):
        raise PasswordValidationError("新密码不能与当前密码相同")

    credential.password_hash = hash_password(new_password)
    credential.changed_at = utcnow()
    credential.algorithm = "bcrypt"
    await session.flush()

    # 吊销全部会话（含当前这条）—— 见上方 docstring 的理由
    revoked = await revoke_all_sessions(session, user_id=user.id)
    return revoked


__all__ = [
    "AccountDisabled",
    "AuthError",
    "CurrentPasswordMismatch",
    "EmailAlreadyExists",
    "InvalidCredentials",
    "Principal",
    "SessionInvalid",
    "authenticate",
    "change_password",
    "find_user_by_email",
    "get_credential",
    "get_tenant_membership",
    "issue_session",
    "list_sessions",
    "list_user_tenants",
    "load_principal",
    "normalize_email",
    "plaintext_token_of",
    "primary_tenant",
    "register_user",
    "resolve_active_tenant",
    "revoke_all_sessions",
    "revoke_session",
]

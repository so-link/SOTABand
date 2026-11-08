"""租户表（批次 2）—— ``tenant`` / ``tenant_membership`` / ``tenant_invitation``

设计见 ``docs/design-08-schema.md`` §3.6–§3.7；契约见
``docs/contract-08-auth-network.md`` §2.2、§3.1。

## 为什么批次 2 提前到 Step 2 一起做

ADR-08-10 原本把租户放在 Step 3。但**冻结契约里 ``AuthContext.tenant_id`` 是必填
字段**，而注册时就要自动创建个人租户（PRD FR-A1）。若 Step 2 不做租户，
``AuthContext`` 就只能给出 ``tenant_id=None`` —— 那是破坏性变更，下游五个模块
一旦按契约写好代码就会返工。

**宁可把两张表提前，也不让已冻结的契约打折。**

## 为什么用邀请制（而不是管理员直接添加）

| 维度 | 邀请制（采用） | 直接添加 |
|---|---|---|
| 同意 | 对方明确接受 | 可在对方不知情时拉入 |
| 邮箱正确性 | 接受时校验匹配，防令牌转发 | 打错就产生悬空成员 |
| PRD | FR-A6 原文是「**接受**租户邀请」 | 与原文不符 |

复用网络邀请的令牌机制（只存 hash、状态机、条件更新）。

## 命名与规范

- 表名单数、公共字段由 mixin 提供（阿里开发手册，ADR-08-11）；
- 无外键：``tenant_id`` / ``user_id`` 都是逻辑引用，存在性由应用层保证。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from core.db.types import UTCDateTime

#: 租户角色
TENANT_ROLE_OWNER = "owner"
TENANT_ROLE_ADMIN = "admin"
TENANT_ROLE_MEMBER = "member"
TENANT_ROLE_VIEWER = "viewer"
TENANT_ROLES = (TENANT_ROLE_OWNER, TENANT_ROLE_ADMIN, TENANT_ROLE_MEMBER, TENANT_ROLE_VIEWER)

#: 成员状态
MEMBERSHIP_ACTIVE = "active"
MEMBERSHIP_INVITED = "invited"
MEMBERSHIP_REMOVED = "removed"
MEMBERSHIP_STATUSES = (MEMBERSHIP_ACTIVE, MEMBERSHIP_INVITED, MEMBERSHIP_REMOVED)

# ── 租户邀请状态机 ──
# 刻意加 TENANT_ 前缀：network.py 里也有一套 INVITATION_*，两者都要在
# models/__init__.py re-export，同名会互相覆盖（那个坑踩过一次了）。
TENANT_INVITATION_PENDING = "pending"
TENANT_INVITATION_ACCEPTED = "accepted"
TENANT_INVITATION_REVOKED = "revoked"
TENANT_INVITATION_EXPIRED = "expired"
TENANT_INVITATION_STATUSES = (
    TENANT_INVITATION_PENDING,
    TENANT_INVITATION_ACCEPTED,
    TENANT_INVITATION_REVOKED,
    TENANT_INVITATION_EXPIRED,
)

#: 租户邀请默认有效期（天）
TENANT_INVITATION_TTL_DAYS = 7


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """租户 —— 资源与成员的归属组织边界。

    ``slug`` 唯一而非 ``name`` 唯一：租户显示名可能重名（"测试组"），slug 才是身份。
    """

    __tablename__ = "tenant"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'archived')", name="status_valid"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Tenant id={self.id} slug={self.slug!r}>"


class TenantMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户与租户的关系 —— ``(tenant_id, user_id)`` 唯一。

    注册时自动写入一条 ``role='owner'`` 的记录（个人租户，PRD FR-A1）
    —— 这让每个用户天然有归属，"切换租户"才有意义（PRD FR-A6）。

    无外键：两个 id 都是逻辑引用。唯一约束同时承担"防重复加入"的职责
    （PRD FR-N3 同源问题）。
    """

    __tablename__ = "tenant_membership"

    #: 逻辑引用 tenant.id（无外键）
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: 逻辑引用 user.id（无外键）
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=TENANT_ROLE_MEMBER)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MEMBERSHIP_ACTIVE, server_default=MEMBERSHIP_ACTIVE
    )

    __table_args__ = (
        Index(
            "uk_tenant_membership_tenant_id_user_id",
            "tenant_id",
            "user_id",
            unique=True,
        ),
        Index("idx_tenant_membership_user_id", "user_id"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name="role_valid"
        ),
        CheckConstraint(
            "status IN ('active', 'invited', 'removed')", name="status_valid"
        ),
    )


class TenantInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """租户邀请 —— 短期、一次性、**库里只存 token hash**。

    为什么用邀请制而不是管理员直接添加，见模块文档的对比表。核心是**同意**：
    别人不能在你不知情的情况下把你拉进他的组织。

    ## 与 ``network_invitation`` 的关系

    两张表结构相似但**刻意不合并**：

    - 领域不同（加入组织 vs 加入协作网络）；
    - 接受后的副作用不同 —— 网络邀请还要求调用者**已经是该租户成员**，
      租户邀请则正是用来建立这层成员关系的；
    - 合并成一个 ``invitation`` 表要引入 ``scope_type``/``scope_id``，
      条件逻辑会散落各处，反而更容易错。

    代价是一点结构重复（约 30 行模型 + 一段相似的 service），可接受。
    """

    __tablename__ = "tenant_invitation"

    #: 逻辑引用 tenant.id（无外键）
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: 规范化后的邮箱（小写）
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    #: SHA-256 十六进制。明文令牌只出现在邀请链接里
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TENANT_INVITATION_PENDING,
        server_default=TENANT_INVITATION_PENDING,
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    #: 逻辑引用 user.id（无外键）
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    __table_args__ = (
        Index("idx_tenant_invitation_tenant_id_status", "tenant_id", "status"),
        Index("idx_tenant_invitation_email", "email"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="status_valid",
        ),
    )

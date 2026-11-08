"""网络表（批次 3）—— ``network`` / ``network_membership`` / ``network_invitation``

设计见 ``docs/design-08-schema.md`` §3.8–§3.10；契约见
``docs/contract-08-auth-network.md`` §2.2、§5。

## 命名与规范

表名单数、公共字段（``id``/``gmt_create``/``gmt_modified``）由 mixin 提供、
**无外键**（阿里开发手册，ADR-08-11）。所有 ``*_id`` 都是逻辑引用，存在性由
应用层保证 —— 具体来说是 ``core/network/service.py`` 在写入前校验。

## 关于 ``owner_id`` 与 ``role='owner'`` 的双字段

``network.owner_id`` 是**创建者记录**（不可变，审计用"谁建的"）；
``network_membership.role='owner'`` 才是**权限来源**（可变、可多人、可转移）。

**``authorize()`` 只读 membership.role，永远不读 owner_id。** 二者可以不一致
（转移所有权后 owner_id 仍是创建者），这是设计意图而非 bug。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from core.db.types import UTCDateTime

# ── 网络状态机（契约 §6 第 6 步）──
NETWORK_ACTIVE = "active"
NETWORK_SUSPENDED = "suspended"
NETWORK_ARCHIVED = "archived"
NETWORK_STATUSES = (NETWORK_ACTIVE, NETWORK_SUSPENDED, NETWORK_ARCHIVED)

# ── 网络角色（与租户角色同词表，契约 §6）──
NETWORK_ROLE_OWNER = "owner"
NETWORK_ROLE_ADMIN = "admin"
NETWORK_ROLE_MEMBER = "member"
NETWORK_ROLE_VIEWER = "viewer"
NETWORK_ROLES = (
    NETWORK_ROLE_OWNER,
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_VIEWER,
)

# ── 成员状态 ──
# 刻意加 NETWORK_ 前缀：tenant.py 里也有 MEMBERSHIP_ACTIVE，两者都在
# models/__init__.py 里 re-export，同名会互相覆盖 —— 那种 bug 极难排查。
NETWORK_MEMBERSHIP_ACTIVE = "active"
NETWORK_MEMBERSHIP_REMOVED = "removed"
NETWORK_MEMBERSHIP_STATUSES = (NETWORK_MEMBERSHIP_ACTIVE, NETWORK_MEMBERSHIP_REMOVED)

# ── 邀请状态机（契约 §5.1）──
INVITATION_PENDING = "pending"
INVITATION_ACCEPTED = "accepted"
INVITATION_REJECTED = "rejected"
INVITATION_EXPIRED = "expired"
INVITATION_REVOKED = "revoked"
INVITATION_STATUSES = (
    INVITATION_PENDING,
    INVITATION_ACCEPTED,
    INVITATION_REJECTED,
    INVITATION_EXPIRED,
    INVITATION_REVOKED,
)

#: 邀请默认有效期（天）
INVITATION_TTL_DAYS = 7

#: 网络默认策略（契约 §2.3）
DEFAULT_POLICY: dict = {
    "allowMemberPublish": False,
    "allowViewerInvoke": False,
}


def default_policy() -> dict:
    """返回一份新的默认策略副本（避免多个网络共享同一个可变 dict）。"""
    return dict(DEFAULT_POLICY)


class Network(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """虚拟协作网络 —— 属且仅属一个租户（PRD 开放问题 2 的答案：是）。

    ``tenant_id`` 唯一性约束在 ``(tenant_id, name)`` 上：网络名称**租户内唯一**
    （PRD FR-N1）。跨租户重名是允许的 —— 它们本来就是不同组织的资产。
    """

    __tablename__ = "network"

    #: 逻辑引用 tenant.id（无外键）
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: **创建者记录，不参与授权判定**（见模块文档）
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NETWORK_ACTIVE, server_default=NETWORK_ACTIVE
    )
    #: ``{"allowMemberPublish": bool, "allowViewerInvoke": bool}``
    policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=default_policy)

    __table_args__ = (
        Index("uk_network_tenant_id_name", "tenant_id", "name", unique=True),
        CheckConstraint(
            "status IN ('active', 'suspended', 'archived')", name="status_valid"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Network id={self.id} name={self.name!r} status={self.status!r}>"


class NetworkMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户与网络的关系 —— ``(network_id, user_id)`` 唯一。

    ## ``version`` 列是并发守卫

    PRD FR-N4 要求「最后一位 Owner 不可被移除（事务内锁定检查）」。但 **SQLite
    没有行级锁，``SELECT ... FOR UPDATE`` 是空操作**，所以改用 compare-and-set：

        UPDATE network_membership SET role=:new, version=version+1
         WHERE id=:id AND version=:expected
        -- 影响 0 行 → 409 并发冲突

    并配合"事务内重新统计 Owner 数量"做最后 Owner 守卫。
    **这两个守卫必须在 PostgreSQL 上复验**（ADR-08-02）—— SQLite 的串行写会
    让竞态恰好被掩盖。
    """

    __tablename__ = "network_membership"

    #: 逻辑引用 network.id（无外键）
    network_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: 逻辑引用 user.id（无外键）
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NETWORK_ROLE_MEMBER
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=NETWORK_MEMBERSHIP_ACTIVE,
        server_default=NETWORK_MEMBERSHIP_ACTIVE,
    )
    joined_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    #: 并发守卫（compare-and-set）
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index(
            "uk_network_membership_network_id_user_id",
            "network_id",
            "user_id",
            unique=True,
        ),
        Index("idx_network_membership_user_id", "user_id"),
        # 统计 Owner 数量（最后 Owner 守卫）走这个索引
        Index("idx_network_membership_network_id_role_status", "network_id", "role", "status"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name="role_valid"
        ),
        CheckConstraint("status IN ('active', 'removed')", name="status_valid"),
    )


class NetworkInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """入网邀请 —— 短期、一次性、**库里只存 token hash**。

    ## 为什么邀请令牌也只存 hash

    与密码重置令牌同理：数据库泄露不应让攻击者能直接接受邀请。
    PRD §10 原文要求「Session、重置 Token、邀请 Token 数据库只存 hash」。

    ## 有效邀请去重在应用层

    「仅当 status=pending 时 (network_id, email) 唯一」是**部分唯一索引**，
    SQLite 与 PostgreSQL 语法不同、不好统一。因此 MVP 在应用层做幂等：
    已有 pending 邀请则返回原邀请，不新建。
    """

    __tablename__ = "network_invitation"

    #: 逻辑引用 network.id（无外键）
    network_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: 规范化后的邮箱（小写）
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    #: SHA-256 十六进制。明文令牌只出现在邀请链接里，永不复现
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=INVITATION_PENDING,
        server_default=INVITATION_PENDING,
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    #: 逻辑引用 user.id（无外键）
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    __table_args__ = (
        Index("idx_network_invitation_network_id_status", "network_id", "status"),
        Index("idx_network_invitation_email", "email"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'expired', 'revoked')",
            name="status_valid",
        ),
    )

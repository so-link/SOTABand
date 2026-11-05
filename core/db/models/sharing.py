"""共享与安装表（批次 4）—— ``installation``

设计见 ``docs/design-08-02-network.md`` §7；契约 §3.3。

## 为什么需要这张表（用户 2026-10-26 拍板新增，第 12 张）

PRD §3 的术语表把「**安装**（Installation）」定义成一个面向用户的动作：

> 成员将网络共享的 Agent/Tool/Data 安装到自己的工作空间。

不建表的替代方案是「极短 TTL 的工作负载凭证，不持久化」。这在 PRD 自己的验收
标准下**是合规的** —— FR-N8 原文写的是「吊销**或**等待极短有效期结束」。

但 **FR-N7 要求「每次通信记录调用主体、目标、权限与结果」**。没有安装记录就
没有稳定的**工作负载主体 id**，审计日志只能写匿名标识，追溯链断掉。
审计是 P0 且不可退让，所以**新增第 12 张表**。

## 与 ``publication`` 的关系

PRD 计划的第 11 张表 ``publication``（资源 → 网络范围的发布授权）属于 Step 5，
本批次尚未落地。``installation.publication_id`` 是**逻辑引用**，等 Step 5 加表后
一起构成完整的共享链路：

```text
资源 ──publication──→ 网络（谁能在哪个网络里用）
          │
          └──installation──→ 成员的工作空间（谁把它装进了自己的工作区）
```

**没有外键**（阿里开发手册，ADR-08-11），所以 ``publication_id`` 指向一张还不存在
的表不会让迁移失败，也不会在运行时校验 —— 存在性由应用层保证。这是无外键方案
的既定代价，不是疏漏。

## 状态机

``active`` ⇄ ``revoked``。吊销保留行（不物理删除），因为：

- 审计要能回答「谁什么时候装过、什么时候被吊销」；
- 重新安装时复用同一行（``UNIQUE(tenant_id, installed_by, publication_id)``），
  ``status`` 改回 ``active``，避免同一用户的安装记录越积越多。
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, CheckConstraint, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# ── 资源类型 ──────────────────────────────────────────────────────────
RESOURCE_TYPE_AGENT = "agent"
RESOURCE_TYPE_TOOL = "tool"
RESOURCE_TYPE_DATA = "data"
RESOURCE_TYPES = (RESOURCE_TYPE_AGENT, RESOURCE_TYPE_TOOL, RESOURCE_TYPE_DATA)

# ── 发布范围 ──────────────────────────────────────────────────────────
#: 仅发布者自己可见（scope_id 为空，归属由 published_by 隐含）
PUBLICATION_SCOPE_PRIVATE = "private"
#: 整个租户可见（scope_id = tenant_id）
PUBLICATION_SCOPE_TENANT = "tenant"
#: 指定协作网络可见（scope_id = network_id）—— PRD FR-N2 的主场景
PUBLICATION_SCOPE_NETWORK = "network"
#: 所有租户可见（scope_id 为空）
PUBLICATION_SCOPE_PUBLIC = "public"
PUBLICATION_SCOPES = (
    PUBLICATION_SCOPE_PRIVATE,
    PUBLICATION_SCOPE_TENANT,
    PUBLICATION_SCOPE_NETWORK,
    PUBLICATION_SCOPE_PUBLIC,
)
#: 需要 scope_id 的范围 —— 其余范围 scope_id 必须为空
SCOPES_REQUIRING_SCOPE_ID = (PUBLICATION_SCOPE_TENANT, PUBLICATION_SCOPE_NETWORK)

# ── 状态 ──────────────────────────────────────────────────────────────
PUBLICATION_ACTIVE = "active"
PUBLICATION_REVOKED = "revoked"
PUBLICATION_STATUSES = (PUBLICATION_ACTIVE, PUBLICATION_REVOKED)

# ── 调用方白名单策略（FR-N7 按目标粒度授权）────────────────────────────
#
#: 网络内所有有 `network.agent.invoke` 权限的成员都能调（默认，向后兼容）
INVOKE_POLICY_NETWORK = "network"
#: 只有 `invoke_allowlist` 里的成员能调
INVOKE_POLICY_ALLOWLIST = "allowlist"
INVOKE_POLICIES = (INVOKE_POLICY_NETWORK, INVOKE_POLICY_ALLOWLIST)

#: 安装状态
INSTALLATION_ACTIVE = "active"
INSTALLATION_REVOKED = "revoked"
INSTALLATION_STATUSES = (INSTALLATION_ACTIVE, INSTALLATION_REVOKED)


class Publication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """资源发布 —— "某个版本的工具/Agent/数据集，对哪个范围开放使用"。

    契约 §2.2 第 10 张表；设计见 ``docs/design-08-schema.md`` §3.11。

    ## ``release_id`` 是"跨系统"逻辑引用

    其他所有 ``*_id`` 都是表间逻辑引用（无外键，ADR-08-11）。``release_id`` 还多
    一层：**它的目标根本不在数据库里** —— 资源本体存在
    ``resources/*/registry.json`` 与文件系统中（ADR-08-08）。

    后果：数据库层无法保证它存在（**连外键也无能为力**，因为目标表不存在）；
    资源被删后 publication 会悬空，必须由应用层清理；查询时要优雅降级。

    ## 为什么额外加一条**部分唯一索引**

    契约给的约束是 ``UNIQUE(resource_type, release_id, scope, scope_id)``。
    它在 ``scope ∈ {tenant, network}``（scope_id 非空）时工作良好，但对
    ``scope ∈ {private, public}``（scope_id 为 NULL）**失效** —— SQL 标准里
    NULL 互不相等，同一版本可以被重复发布出多行。

    所以这里补一条 ``WHERE scope_id IS NULL`` 的部分唯一索引。
    SQLite（3.8+）与 PostgreSQL 都支持部分唯一索引，两边行为一致。
    """

    __tablename__ = "publication"

    #: agent / tool / data
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: **跨系统**逻辑引用：resources/*/registry.json 里的版本 id（无外键，且无法有）
    release_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: private / tenant / network / public
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    #: scope='tenant' → tenant_id；scope='network' → network_id；其余 NULL
    scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PUBLICATION_ACTIVE,
        server_default=PUBLICATION_ACTIVE,
    )
    #: 逻辑引用 user.id（无外键）
    published_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    # ── 调用方白名单（FR-N7「按目标粒度授权」，批次 7 新增）──
    #
    # 批次 5 的白名单是**粗粒度**的：只要目标发布到了本网络，网络里任何有
    # `network.agent.invoke` 权限的成员都能调。设计文档 §8.3 如实记录了这一点
    # 作为已知简化。这里是补上"按目标"的那一半：
    #
    # - `network`：网络内所有有权限的成员都能调（默认，保持向后兼容）
    # - `allowlist`：**只有 `invoke_allowlist` 里的成员**能调
    #
    # 为什么用成员 user_id 而不是 installation_id 作白名单项：发布者面向的是
    # "同事"，不是"某个具体安装实例"。一个成员可能有多个 install，让他逐个维护
    # 安装 id 既不可发现也不可维护。
    #: network / allowlist
    invoke_policy: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=INVOKE_POLICY_NETWORK,
        server_default=INVOKE_POLICY_NETWORK,
    )
    #: invoke_policy='allowlist' 时生效：允许调用的成员 user_id 列表（JSON 数组）
    invoke_allowlist: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        # 契约约束：同一版本对同一范围只发布一次（幂等）
        Index(
            "uk_publication_resource_type_release_id_scope_scope_id",
            "resource_type",
            "release_id",
            "scope",
            "scope_id",
            unique=True,
        ),
        # 补口子：scope_id 为 NULL 时上面的唯一约束失效，这里兜住（见类文档）
        Index(
            "uk_publication_global_scope",
            "resource_type",
            "release_id",
            "scope",
            unique=True,
            sqlite_where=text("scope_id IS NULL"),
            postgresql_where=text("scope_id IS NULL"),
        ),
        # 网络资源目录的主查询路径：某网络下、某类资源、仍在发布中
        Index("idx_publication_scope_scope_id_status", "scope", "scope_id", "status"),
        # 「我发布过什么」
        Index("idx_publication_published_by_status", "published_by", "status"),
        CheckConstraint(
            "resource_type IN ('agent', 'tool', 'data')", name="resource_type_valid"
        ),
        CheckConstraint(
            "scope IN ('private', 'tenant', 'network', 'public')", name="scope_valid"
        ),
        CheckConstraint("status IN ('active', 'revoked')", name="status_valid"),
        # PRD §7.2 原文要求：发布到网络范围时必须指明是哪个网络
        CheckConstraint(
            "scope NOT IN ('tenant', 'network') OR scope_id IS NOT NULL",
            name="scope_id_required",
        ),
        # 注意：`invoke_policy` / `invoke_allowlist` 的合法性**刻意不加 CHECK**。
        # 原因是 SQLite 的 ALTER TABLE 不支持新增表级 CHECK，加进模型会让
        # "create_all 建出来的库" 与 "迁移建出来的库" 结构不一致，而 alembic
        # autogenerate 又检测不到 CHECK，漂移会长期潜伏。
        # 这两个字段只有 `set_publication_invoke_policy()` 一个写入口，校验放那里。
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<Publication id={self.id} {self.resource_type}:{self.release_id} "
            f"scope={self.scope} status={self.status!r}>"
        )


class Installation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """某个成员把某个「已发布的资源」装进自己工作空间的记录。

    ## 为什么主键是 UUID 而不是自增 bigint

    安装 id 会出现在「吊销这个安装」「用这个安装换取工作负载凭证」等接口的路径/参数里。
    自增整数会被枚举，从而暴露「某租户一共装了多少东西」这类信息（PRD §7.3）。
    """

    __tablename__ = "installation"

    #: 逻辑引用 tenant.id（无外键）。隔离边界，所有查询必须带它
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: 逻辑引用 publication.id（无外键，且该表在 Step 5 才落地）
    publication_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: 逻辑引用 user.id（无外键）—— 装进**谁**的工作空间
    installed_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=INSTALLATION_ACTIVE,
        server_default=INSTALLATION_ACTIVE,
    )

    __table_args__ = (
        # 一个用户对同一发布项只有一条记录：重复安装是"重新激活"，不是新增行
        Index(
            "uk_installation_tenant_id_installed_by_publication_id",
            "tenant_id",
            "installed_by",
            "publication_id",
            unique=True,
        ),
        # 「我的工作空间里有哪些已安装项」的主查询路径
        Index(
            "idx_installation_tenant_id_installed_by_status",
            "tenant_id",
            "installed_by",
            "status",
        ),
        # 「这个发布项被谁装了」—— 发布者下架/吊销时需要反查
        Index("idx_installation_publication_id", "publication_id"),
        CheckConstraint("status IN ('active', 'revoked')", name="status_valid"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<Installation id={self.id} publication={self.publication_id} "
            f"status={self.status!r}>"
        )

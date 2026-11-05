"""认证核心表（批次 1）—— user / user_identity / user_credential / session

设计见 ``docs/design-08-schema.md`` §3.1–§3.4；契约见
``docs/contract-08-auth-network.md`` §2.2。

## 表名一律单数（阿里开发手册【强制】）

``user`` / ``user_identity`` / ``user_credential`` / ``session`` —— 不使用复数名词。

⚠️ **``user`` 是 SQL 保留字**（PostgreSQL 中 ``USER`` 等价于 ``CURRENT_USER``）。
SQLAlchemy 会自动加引号生成 ``"user"``，ORM 用法不受影响；但**裸 SQL 必须写
``"user"``**。详见 ``core/db/base.py`` 模块文档。

## 公共字段由 mixin 提供（手册三字段）

每个表都继承 :class:`UUIDPrimaryKeyMixin` 与 :class:`TimestampMixin`，
即自动具备 ``id`` / ``gmt_create`` / ``gmt_modified``。不要在各表里重复声明。

## 不使用外键（阿里开发手册【强制】）

> 不得使用外键与级联，一切外键概念必须在应用层解决。

因此本层：

- 列上**没有** ``ForeignKey``，也**没有** ``ondelete`` 级联；
- 引用完整性（"这个 user_id 必须真的存在"）**由应用层负责**；
- 删除必须**按依赖顺序显式执行**，不能指望数据库级联。

**代价必须清楚**：数据库不会替你拦住"指向不存在用户的凭证行"。测试刻意固化了
这一行为（``test_referential_integrity_is_not_enforced_by_db``），这样任何人加回
外键、或误以为有外键保护时，都会立刻看到。

## 关于 ORM 关系

没有外键之后，SQLAlchemy **无法再推断连接条件**，所以每个 ``relationship``
都显式写出 ``primaryjoin``，并用 ``foreign()`` 标注"哪一列是子侧"。
这是无外键方案的直接代价，但也让连接关系从"数据库元数据"变成"代码里看得见"。

``relationship`` 一律 ``lazy="raise"``：在 async 上下文里触发隐式懒加载会抛
``MissingGreenlet``，而且报错位置往往离真正的原因很远。设为 ``raise`` 后必须
显式用 ``selectinload()`` / ``joinedload()`` 预加载，问题在定义处就暴露。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from core.db.types import UTCDateTime

# ── 无外键方案下的连接条件（在 relationship 之间复用，避免写出不一致的表达式）──
_JOIN_USER_IDENTITY = "User.id == foreign(UserIdentity.user_id)"
_JOIN_USER_CREDENTIAL = "User.id == foreign(UserCredential.user_id)"
_JOIN_USER_SESSION = "User.id == foreign(UserSession.user_id)"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户主体（表 ``user``）。

    - ``email`` **可空**：GitHub 用户未公开邮箱时为空（ADR-08-01 / PRD FR-A1）；
      ``UNIQUE`` 约束保证并发注册同一邮箱时只有一个成功 —— 不能靠"先查后插"。
    - **不设 ``email_verified``**：MVP 不做邮箱验证（ADR-08-01b），留一个永远为
      false 的字段只会误导使用者。
    """

    __tablename__ = "user"

    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )

    identities: Mapped[list[UserIdentity]] = relationship(
        back_populates="user", lazy="raise", primaryjoin=_JOIN_USER_IDENTITY
    )
    credential: Mapped[UserCredential | None] = relationship(
        back_populates="user",
        lazy="raise",
        uselist=False,
        primaryjoin=_JOIN_USER_CREDENTIAL,
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", lazy="raise", primaryjoin=_JOIN_USER_SESSION
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<User id={self.id} email={self.email!r} status={self.status!r}>"


class UserIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """外部身份映射（表 ``user_identity``）—— ``(issuer, subject)`` 唯一。

    设计意图：从第一天就支持**任意多种登录方式并存**，而不是在 ``user`` 上开
    ``github_id`` 这样的列。

    - ``issuer='local'``：自建账号密码路径，``subject`` = 本地 ``user_id``；
    - ``issuer='github'``：Phase 2 接入，``subject`` = GitHub 用户数字 ID。

    Phase 2 为已有用户追加一行 GitHub 身份即可，**表结构不用改** —— 这正是
    这个设计存在的理由。用邮箱做外部主键是错的：用户在 GitHub 改邮箱后，
    账号会变成两个人（PRD FR-A1）。
    """

    __tablename__ = "user_identity"

    #: 逻辑引用 user.id。无外键（阿里手册），存在性由应用层保证。
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    issuer: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    user: Mapped[User] = relationship(
        back_populates="identities", lazy="raise", primaryjoin=_JOIN_USER_IDENTITY
    )

    __table_args__ = (
        # PRD FR-A1 明确要求：以 (issuer, subject) 作为外部身份主键
        Index("uk_user_identity_issuer_subject", "issuer", "subject", unique=True),
    )


class UserCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """密码凭证（表 ``user_credential``）—— 仅自建账号密码路径使用。

    与 ``user`` 分表的理由：① GitHub 登录的用户没有密码，字段必须可空会让
    语义混乱；② 密码哈希是最高敏感字段，独立成表便于限制访问范围与单独审计。

    ## 为什么 ``user_id`` 是唯一列而不是主键

    按手册要求每个表都要有独立的 ``id`` 主键，所以 ``user_id`` 退为普通列 +
    ``UNIQUE``。**"一人一条凭证"的保证没有减弱** —— ``uk_user_credential_user_id``
    唯一约束与主键在这一点上效力相同。

    ## 为什么保留 ``changed_at`` 而不是复用 ``gmt_modified``

    PRD §7.2 明确列出了 ``changed_at``，且"改密后吊销全部会话"的逻辑按它判断。
    它是**业务字段**（密码最后变更时间），``gmt_modified`` 是**技术字段**；本表
    二者通常同步变化，保留前者是为了让业务语义显式。

    ## ⚠️ 两个必须遵守的实现约束（ADR-08-01a）

    1. **bcrypt 的 72 字节上限**：官方 ``bcrypt`` 对超长密码**直接抛
       ``ValueError``**（不是静默截断）。入口必须按 **UTF-8 字节长度**校验
       （``len(pw.encode("utf-8")) <= 72``），且**不得静默截断** —— 截断会让
       「同一密码不同后缀」都能登录，违背用户的安全预期。
    2. **不要用 ``passlib``**：其 1.7.4 与 ``bcrypt>=4.1`` 不兼容（读取已被移除的
       ``bcrypt.__about__`` 会报错）。
    """

    __tablename__ = "user_credential"

    #: 逻辑引用 user.id（无外键）。唯一约束保证"一人一条凭证"。
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    #: 记录算法以便将来平滑升级（如 Argon2id）；登录成功时可按需重哈希迁移
    algorithm: Mapped[str] = mapped_column(
        String(32), nullable=False, default="bcrypt", server_default="bcrypt"
    )
    #: 业务字段：密码最后一次变更时间（改密后吊销该用户全部会话的依据）
    changed_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    user: Mapped[User] = relationship(
        back_populates="credential", lazy="raise", primaryjoin=_JOIN_USER_CREDENTIAL
    )


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """会话（表 ``session``）—— opaque 令牌 + 服务端可吊销。

    类名刻意不叫 ``Session``，避免与 ``sqlalchemy.orm.Session`` 混淆。

    ## 为什么是 opaque 令牌而不是 JWT

    JWT 无法即时吊销：改密码、禁用用户、成员退出后旧 token 仍然有效直到过期。
    而 PRD FR-N8 的核心要求就是**即时撤权**。每次请求查一次数据库的代价在本
    项目规模下可忽略。

    ## 为什么 ``token_hash`` 用 SHA-256 而不是 bcrypt

    令牌本身是 256 bit 高熵随机串，不存在字典攻击空间，不需要慢哈希；而每次
    请求都要查会话，必须快。这与密码的处理逻辑不同，不要混为一谈。

    ## 认证时必须同时校验用户状态

    只查会话不够 —— 会话可能仍在有效期内，但用户已被禁用（PRD FR-A5）。
    因此认证链路要 join 到 ``user`` 确认 ``status='active'``。

    这也正是**无外键方案要求应用层多做事**的典型例子：以前可以靠外键保证
    ``user_id`` 有效，现在必须自己校验，而且还得额外校验用户状态。
    """

    __tablename__ = "session"

    #: 逻辑引用 user.id（无外键），存在性由应用层保证。
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: SHA-256 十六进制（64 字符）。**明文令牌永不落库**。
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    #: 非空即已吊销（登出 / 改密 / 禁用用户 / 退出全部设备）
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 45 = IPv6 最大文本长度
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    #: 当前**激活租户**（Step 3）。为空时回退到"主租户"（最早的 active 成员关系）。
    #:
    #: 存在服务端而不是让客户端传 header，因为"我在哪个租户下操作"必须是服务端
    #: 断言的事实 —— 否则改一个请求头就能切换上下文（PRD FR-A6「切换失败时保持
    #: 原租户上下文，不允许仅改前端状态」）。
    active_tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    user: Mapped[User] = relationship(
        back_populates="sessions", lazy="raise", primaryjoin=_JOIN_USER_SESSION
    )

    __table_args__ = (
        # 「退出全部设备」「会话列表」的查询路径
        Index("idx_session_user_id_revoked_at", "user_id", "revoked_at"),
        # 过期清理任务
        Index("idx_session_expires_at", "expires_at"),
    )

    @property
    def is_active(self) -> bool:
        """未吊销且未过期。注意：**不含**用户禁用检查 —— 那需要 join user。

        因为 ``expires_at`` 用的是 ``UTCDateTime``，无论从哪个方言读出来都带
        tzinfo，可以直接与 ``utcnow()`` 比较，不需要手工补 UTC。
        """
        if self.revoked_at is not None:
            return False
        return self.expires_at > utcnow()

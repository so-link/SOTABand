"""审计表（批次 1）—— ``audit_event``

设计见 ``docs/design-08-schema.md`` §3.5；PRD FR-A5 / FR-N9。

## 为什么放在批次 1（而不是最后一批）

认证事件（注册、登录、失败、限流）从 Step 2 起就要写审计 —— PRD FR-A5 把
「认证事件审计」当作登录功能的一部分。若等最后才建表，Step 2 的审计只能事后
补，必然漏记。详见 ``docs/design-08-schema.md`` §1。

## 表名与公共字段

表名 ``audit_event``（单数，阿里手册）。``id`` / ``gmt_create`` / ``gmt_modified``
由 mixin 提供。

⚠️ 本表**只追加**，所以 ``gmt_modified`` 恒等于 ``gmt_create``。手册要求每个表都有
这两个字段，故保留；"只追加"是**应用层约定**，不是数据库约束。
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: ``actor_kind`` 取值
ACTOR_USER = "user"
ACTOR_WORKLOAD = "workload"
ACTOR_SYSTEM = "system"

#: ``result`` 取值
RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"
RESULT_DENIED = "denied"


class AuditEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """审计事件（表 ``audit_event``）—— **只追加，不修改不删除**。

    ## 关键设计：全部为逻辑引用，不设外键

    ``actor_id`` / ``tenant_id`` / ``network_id`` 都**刻意不建外键**（本项目本就不
    使用外键，ADR-08-11），而且这里还有额外理由：

    - ``actor_id`` 是 ``String(64)``，因为主体既可能是用户也可能是 Agent 工作
      负载（Phase 5），两者 id 空间不同；
    - 审计必须在**被引用对象消失后依然完整**（PRD §11「审计保留期独立于成员
      删除」）。若建了外键并级联删除，移除一个成员就会抹掉他的操作痕迹，
      追溯链就断了。

    ## ``tenant_id`` 为什么可空

    登录失败时还不知道（或不该记录）是哪个租户 —— 认证发生在租户上下文确定
    之前。

    ## 禁止记录的内容

    密码、密码哈希、会话令牌、Cookie 全文、验证码。写入前可复用
    ``core.security.secrets.scrub_text()`` 做兜底脱敏。
    """

    __tablename__ = "audit_event"

    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    network_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: 形如 ``auth.login`` / ``network.member.remove`` / ``resource.publish``
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 贯穿链路，便于把一次请求涉及的多个事件串起来（PRD §11）
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "actor_kind IN ('user', 'workload', 'system')", name="actor_kind_valid"
        ),
        CheckConstraint("result IN ('success', 'failure', 'denied')", name="result_valid"),
        Index("idx_audit_event_tenant_id_gmt_create", "tenant_id", "gmt_create"),
        Index("idx_audit_event_network_id_gmt_create", "network_id", "gmt_create"),
        Index("idx_audit_event_actor_id_gmt_create", "actor_id", "gmt_create"),
        Index("idx_audit_event_request_id", "request_id"),
    )

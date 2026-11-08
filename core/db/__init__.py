"""数据库层 —— SQLAlchemy 2.0 async + Alembic

归属需求 **8.1 用户注册与登录 / 8.2 用户组网**。

- 设计依据：``docs/design-08-schema.md``
- 架构决策：``docs/adr-08-user-space-and-network.md``（ADR-08-02）
- 冻结契约：``docs/contract-08-auth-network.md`` §2

## 为什么需要数据库

在 8.1/8.2 之前，全项目持久化靠 4 个 JSON registry + 文件系统，没有数据库层。
JSON registry 无法表达成员关系、唯一约束、并发邀请、权限查询与审计 ——
而这四件事恰好是 8.1/8.2 的全部内容。

## 本层不做什么

**不接管既有资源本体。** 现有 48 个工具、30 个模型、数据集的注册记录仍在
``resources/*/registry.json`` + 文件系统里，本模块只新增**关系**表
（身份、租户、网络、发布、审计）。因此这次改造**没有数据迁移任务，只有建表**
（ADR-08-08）。
"""

from core.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utcnow,
)
from core.db.session import (
    dispose_engine,
    get_db_session,
    get_engine,
    get_sessionmaker,
    session_scope,
)
from core.db.types import UTCDateTime

__all__ = [
    "Base",
    "TimestampMixin",
    "UTCDateTime",
    "UUIDPrimaryKeyMixin",
    "dispose_engine",
    "get_db_session",
    "get_engine",
    "get_sessionmaker",
    "session_scope",
    "utcnow",
]

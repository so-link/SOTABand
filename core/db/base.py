"""声明式基类、手册规定的公共字段、约束命名约定与统一时间源。

## 阿里巴巴 Java 开发手册相关条款（本项目强制遵循）

| 条款 | 落地方式 |
|---|---|
| 【强制】不得使用外键与级联，一切外键概念在应用层解决 | 见 ``docs/adr-08-user-space-and-network.md`` ADR-08-11 |
| 【强制】表名不使用复数名词 | 表名一律单数：``user`` / ``session`` / ``network`` … |
| 【强制】表必备三字段 ``id``、``gmt_create``、``gmt_modified`` | :class:`UUIDPrimaryKeyMixin` + :class:`TimestampMixin` |
| 【强制】主键索引 ``pk_``、唯一索引 ``uk_``、普通索引 ``idx_`` | :data:`NAMING_CONVENTION` |

> 唯一**有意不采纳**的手册条款是「``id`` 为 bigint unsigned 自增」——PRD §7.3 要求
> 使用**不可猜测 ID（UUID）** 以防遍历/枚举，这是安全要求，优先级高于该条规范。
> 详见 ADR-08-11 的「手册中其余相关条款的处理」。

## 为什么把三字段做成 mixin

"每个表都要有 id / gmt_create / gmt_modified"如果只写在规范里，迟早会有人漏。
做成 mixin 之后，新表继承即可，**结构和规范合一**，漏写会立刻被
``test_every_table_has_manual_standard_columns`` 抓到。

## 为什么需要显式命名约定

Alembic 在 SQLite 上改表必须走 batch 模式 —— SQLite 的 ``ALTER TABLE`` 能力极弱，
只能"建新表 → 拷数据 → 换名"。batch 模式需要**按名字**引用既有约束；
匿名约束没有稳定名字，会导致同一次迁移在 SQLite 与 PostgreSQL 上生成不同的
DDL，schema 就此漂移。因此在 metadata 上统一约定命名规则。

## 时间约定（重要）

全库统一用**带 UTC 时区**的 ``datetime``，并且**通过 :class:`~core.db.types.UTCDateTime`
列类型强制保证**：

- **写入**：一律用 :func:`utcnow`，不要用 ``datetime.now()``（那是本地时间）；
- **读取**：SQLite 没有时区类型会丢弃 tzinfo，``UTCDateTime`` 在读取时自动补回 UTC。

**不要在模型里直接写 ``DateTime(timezone=True)``** —— 那样读出来是 naive，与
``utcnow()`` 比较会抛 ``TypeError: can't compare offset-naive and offset-aware``。
这类 bug 只在"从库里读出来的对象"上出现，同一段代码有时报错有时不报，极难定位。
用 ``UTCDateTime`` 让问题从根上消失。

详见 ``docs/design-08-schema.md`` §6.4。

## 常见陷阱：Uuid 列在 SQLite 上的存储格式

``sqlalchemy.Uuid`` 在无原生 UUID 类型的后端（SQLite）上以 ``CHAR(32)`` 存储
``uuid.hex`` —— **32 位、无连字符**；PostgreSQL 上则是原生 ``uuid`` 类型。

因此**裸 SQL 里传 ``str(uuid)``（36 位带连字符）会一行都查不到**，而且不报错，
只是静默返回空结果。正确做法：

- 优先用 ORM 条件（``Model.user_id == some_uuid``），绑定处理器会正确转换；
- 必须用裸 SQL 时传 ``value.hex``。

回归测试：``tests/unit/test_db_foundation.py::test_uuid_storage_format_on_sqlite``。

## 常见陷阱：``user`` 表在 PostgreSQL 上是保留字

``user`` 是 SQL 保留字（PostgreSQL 中 ``USER`` 等价于 ``CURRENT_USER``）。SQLAlchemy
会**自动加引号**生成 ``"user"``，所以 ORM 用法不受影响；但：

- **裸 SQL 必须写 ``"user"``**，``SELECT * FROM user`` 在 PostgreSQL 上会报语法错误；
- 数据库 GUI 工具通常会自动处理，命令行客户端不会。

若将来觉得这个引号成本太高，可改名为 ``user_account`` / ``sys_user``。
回归测试：``tests/unit/test_db_foundation.py::test_reserved_word_table_names``。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import MetaData, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.db.types import UTCDateTime

#: 约束命名约定。Alembic batch 模式依赖它生成稳定的 DDL。
#:
#: 索引名前缀遵循**阿里巴巴 Java 开发手册**（八、ORM 映射 / 索引命名）：
#: 主键 ``pk_``、唯一索引 ``uk_``、普通索引 ``idx_``。
#: 校验约束手册未规定，沿用 ``ck_``。
NAMING_CONVENTION = {
    "ix": "idx_%(column_0_label)s",
    "uq": "uk_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
    # 本项目不使用外键（ADR-08-11）。保留该模式纯粹是为了命名一致：万一有人
    # 加了 ForeignKey，至少约束名是确定的，Alembic batch 模式才不会因匿名约束出错。
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """统一时间源：带 UTC 时区的当前时间。

    不要用 ``datetime.now()`` —— 那产生 naive 的本地时间，写入后两种数据库
    的行为会分叉（SQLite 直接存，PostgreSQL 按会话时区转换）。
    """
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    """手册三字段之一：``id`` 主键。

    **有意偏离手册的类型规定**：手册要求 ``bigint unsigned`` 自增，本项目用
    UUID —— PRD §7.3 要求"不可猜测 ID"以防遍历/枚举（见 ADR-08-11）。
    ``sqlalchemy.Uuid`` 跨方言：SQLite 存 ``CHAR(32)``，PostgreSQL 存原生 ``uuid``。
    """

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """手册三字段之二、之三：``gmt_create`` 与 ``gmt_modified``。

    命名取自手册：``gmt_create`` 用**现在时**（主动创建），``gmt_modified`` 用
    **过去分词**（被动更新）——这是手册明确区分的语义，不只是命名风格。

    ⚠️ 只追加的表（如 ``audit_event``）也会带上 ``gmt_modified``：手册要求每个表
    都有这两个字段，且"只追加"是**应用层约定**而非数据库约束 —— 一个永远不会被
    更新的表，其 ``gmt_modified`` 恒等于 ``gmt_create``，这不影响正确性。
    """

    gmt_create: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    gmt_modified: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

"""跨方言的自定义列类型。

## 为什么需要 :class:`UTCDateTime`

SQLite **没有时区类型**：``DateTime(timezone=True)`` 写进去是 aware，读出来是
naive。这导致一个非常隐蔽的 bug 类：

```python
session.expires_at > utcnow()          # TypeError: can't compare offset-naive
                                       # and offset-aware datetimes
```

而它只在"从数据库读出来的对象"上出现 —— 刚 ``add()`` 还没 commit/refresh 的对象
是 aware 的，比较正常。于是同一段代码有时报错有时不报，极难定位。

项目的约定是「读到的 naive datetime 一律视作 UTC」（见 ``core/db/base.py``），
但**靠约定意味着每个开发者都要记得**。本类型把这条约定变成结构：

- 写入（``process_bind_param``）：naive 视作 UTC，统一转成 aware UTC；
- 读取（``process_result_value``）：naive 补上 UTC tzinfo。

于是业务代码**永远拿到 aware datetime**，naive/aware 混用的问题从根上消失。

## 方言行为

| 方言 | DDL 类型 | 读取时 |
|---|---|---|
| SQLite | ``DATETIME`` | 补 UTC tzinfo |
| PostgreSQL | ``TIMESTAMP WITH TIME ZONE`` | 已是 aware，仅做归一 |

**DDL 与直接用 ``DateTime(timezone=True)`` 完全一致** —— 这是有意的，让已有迁移
继续有效（``alembic check`` 不应产生新操作）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """始终以 **aware UTC** 往返的 datetime 列。

    用法与 ``DateTime(timezone=True)`` 完全相同，但读出来的值保证带 ``tzinfo``。
    不要直接在模型里写 ``DateTime(timezone=True)`` —— 用这个类型（见
    ``tests/unit/test_db_foundation.py::test_utc_datetime_round_trip``）。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        """写入前：naive 视作 UTC，统一成 aware UTC。"""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        """读取后：naive 补上 UTC tzinfo，保证调用方永远拿到 aware。"""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

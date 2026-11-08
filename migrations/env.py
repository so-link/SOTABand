"""Alembic 运行环境。

## 连接串来源

从 ``config.settings.storage.database_url`` 读取（可用 ``DATABASE_URL`` 环境变量
覆盖），**不读 alembic.ini** —— 避免"应用连 A 库、迁移改 B 库"的双真值源问题。

## 为什么 render_as_batch=True

SQLite 的 ``ALTER TABLE`` 能力极弱（不能改列类型、不能加约束），Alembic 只能走
batch 模式：建新表 → 拷数据 → 换名。batch 模式需要按名字引用既有约束，因此
``core/db/base.py`` 在 metadata 上定义了命名约定。

对 PostgreSQL 无副作用，所以无条件开启，让两种数据库走同一条迁移路径。

## 为什么用 async 引擎

应用侧是 SQLAlchemy 2.0 async（``aiosqlite`` / ``psycopg`` async）。迁移使用同一
驱动可以保证连接参数、PRAGMA、类型转换行为完全一致 —— 用同步驱动跑迁移、
异步驱动跑应用，是类型不一致的常见来源。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import core.db.models  # noqa: F401
from config.settings import settings

# ⚠️ 必须导入 models 包，否则 Base.metadata 里没有表，autogenerate 会生成空迁移。
from core.db.base import Base

config = context.config

# 把真实的连接串注入 config，供 async_engine_from_config 使用
config.set_main_option("sqlalchemy.url", settings.storage.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """自定义类型的渲染规则。

    **迁移文件不应依赖应用代码** —— 应用里的类型将来可能改名或改实现，而历史
    迁移必须永远能跑。所以把自定义的 ``UTCDateTime`` 渲染成等价的底层类型
    ``sa.DateTime(timezone=True)``（DDL 完全一致）。

    若不处理，Alembic 会渲染成 ``core.db.types.UTCDateTime()``，而迁移文件并没有
    ``import core``，升级时报 ``NameError: name 'core' is not defined``。
    """
    if type_ == "type" and obj.__class__.__name__ == "UTCDateTime":
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连数据库（alembic upgrade --sql）。"""
    context.configure(
        url=settings.storage.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

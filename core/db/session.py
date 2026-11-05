"""异步引擎与会话工厂。

引擎按 ``config.settings.storage.database_url`` **惰性创建、进程内单例**。
导入本模块不会连接数据库 —— 只有真正取引擎时才建连接池。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_SQLITE_ASYNC_PREFIX = "sqlite+aiosqlite:///"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def resolve_database_url(url: str) -> str:
    """把 SQLite 数据库的相对路径锚定到项目根。

    ``sqlite+aiosqlite:///./storage/sotaband.db`` 里的 ``./`` 是相对**当前工作目录**
    的。后端通常从项目根启动，但测试、脚本、Alembic 未必 —— 那样就会在别处
    悄悄建出一个空库，然后表现为"表明明建了却查不到"。这里统一锚定，消除
    对进程工作目录的隐式依赖。
    """
    if not url.startswith(_SQLITE_ASYNC_PREFIX):
        return url  # PostgreSQL 等：连接串自带主机信息，无需处理

    raw = url[len(_SQLITE_ASYNC_PREFIX):]

    # 空串 / 内存库 / file: URI 都不是「项目根下的相对路径」，原样返回。
    # 注意 :memory: 若被拼成 E:/.../:memory: 会变成一个真实文件，静默丢掉内存库语义。
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        return url

    if Path(raw).is_absolute():
        return url

    resolved = (_PROJECT_ROOT / raw).resolve()
    return f"{_SQLITE_ASYNC_PREFIX}{resolved.as_posix()}"


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """为 SQLite 连接设置必要的 PRAGMA。

    这三条都不是可选优化，缺任何一条都会造成真实的行为差异：

    - ``foreign_keys=ON`` —— **本项目按阿里开发手册不使用外键**，因此这条在当前
      schema 下是空操作，属**纵深防御**：SQLite 默认不强制外键，万一将来有人加了
      ``ForeignKey``，开着它才能保证约束真的生效，而不是"写了等于没写"。
    - ``journal_mode=WAL`` —— 默认的 rollback journal 下读会阻塞写。
      WAL 允许读写并发。
    - ``busy_timeout`` —— SQLite 是**单写者**。没有超时设置时，并发写会立即
      抛 "database is locked" 而不是等待。5 秒足够覆盖本项目的事务长度。

    ## 为什么这里**不**安装全局 ``BEGIN IMMEDIATE``

    SQLAlchemy 官方给 SQLite 的配方（connect 事件 ``isolation_level=None`` +
    begin 事件 ``BEGIN IMMEDIATE``）能让"读-判断-写"原子化，代价是**每条连接
    从第一条 SQL 起就持写锁**。本项目每个已认证请求都会在 ``get_auth_context``
    里开事务并持有到响应结束（含 LLM / SSE 长请求）—— 实测第二个并发请求的
    普通 SELECT 会在 busy_timeout 后被 ``OperationalError: database is locked``
    打死，等于把整个服务串行化。

    因此写锁只加在真正需要临界区的路径上：``core.auth.authorize.lock_network``
    在成员变更前用一条空写 UPDATE 提前取得库级写锁（等效 BEGIN IMMEDIATE，
    但读请求保持并发）。新增"读-判断-写"型并发守卫时，请复用该函数的设计。
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def _build_engine() -> AsyncEngine:
    url = resolve_database_url(settings.storage.database_url)

    if url.startswith("sqlite"):
        # 确保库文件所在目录存在
        db_path = url[len(_SQLITE_ASYNC_PREFIX):]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        future=True,
    )

    if url.startswith("sqlite"):
        _install_sqlite_pragmas(engine)

    return engine


def get_engine() -> AsyncEngine:
    """返回进程内单例引擎。"""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """返回进程内单例会话工厂。"""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # 提交后仍可读属性，避免异步下的隐式 IO
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """事务作用域：正常提交、异常回滚、最终关闭。

    供脚本与后台任务使用；请求内请用 :func:`get_db_session` 依赖。
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个会话。

    **不自动提交** —— 由业务代码显式 ``commit()``，让事务边界清晰可见。
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """释放连接池。用于应用关闭与测试清理。"""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None

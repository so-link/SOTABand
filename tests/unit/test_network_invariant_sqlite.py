"""SQLite 上「最后一位 Owner」不变量的并发回归 —— PR #5 独立终审 ⑨。

## 背景（实测复现）

SQLite 上 ``SELECT … FOR UPDATE`` 是空操作，而 **pysqlite 默认在第一条 DML 之前
不发 BEGIN**（``isolation_level=""``）—— "先读 count、再判断、最后写"的两个事务
之间因此没有任何互斥：

```text
T1: SELECT count(owner) -> 2   ✓
T2: SELECT count(owner) -> 2   ✓     ← 两边都在 autocommit 里读，互不阻塞
T1: UPDATE 降级 Alice -> 1
T2: UPDATE 降级 Bob   -> 0           ← 网络变成无主孤儿，且不可恢复
```

而 SQLAlchemy 官方推荐的全局 ``BEGIN IMMEDIATE`` 会让**每条连接**从第一条 SQL 起
就持写锁：本项目每个已认证请求都会在 ``get_auth_context`` 里开事务并持有到响应
结束（含 LLM / SSE 长请求），第二个并发请求的普通 SELECT 就会在 busy_timeout 后
被 ``database is locked`` 打死。

所以修复落在 :func:`core.auth.authorize.lock_network`：成员变更前先跑一条**空写**
UPDATE 取得库级写锁（等效 BEGIN IMMEDIATE，但只作用于临界区，读请求保持并发）。
本文件把这两条性质都锁成断言：

1. 只读事务**不**持写锁（防止有人改回全局 BEGIN IMMEDIATE 把服务串行化）；
2. ``lock_network`` **确实**取得了写锁，并在回滚后释放；
3. 两个独立 session 并发"自我降级 / 退出"后，最终 owners ≥ 1，败者拿到 409。

## 为什么用 barrier

``asyncio.gather`` 两个操作不保证交错：一个可能完整跑完，另一个再读到 count=1
就"正确地"拒绝了 —— 测试通过但什么都没验证。这里在 ``count_active_owners`` 上
装一个 barrier：两个事务都读到数字后才继续。修复前它们会同时读到 2 → 双双降级
→ 0 位 Owner（断言失败）；修复后第二个事务被写锁挡在 barrier 之前，超时后继续，
读到的已经是 1 → 409。
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from core.auth.authorize import lock_network
from core.auth.context import AuthContext
from core.auth.service import normalize_email
from core.db.base import utcnow
from core.db.models import (
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    Network,
    NetworkMembership,
    Tenant,
    User,
)
from core.db.session import get_sessionmaker, resolve_database_url
from core.errors import ConflictError
from core.network.service import change_member_role, leave_network

REQUEST_ID = "sqlite-invariant"

pytestmark = pytest.mark.skipif(
    "sqlite" not in resolve_database_url(settings.storage.database_url),
    reason=(
        "本文件验证 SQLite 的写锁方案（空写 UPDATE 提前取锁）；"
        "PostgreSQL 的行锁路径由 tests/integration/test_network_concurrency.py 覆盖"
    ),
)


# ── 脚手架 ────────────────────────────────────────────────────────────


@asynccontextmanager
async def independent_session() -> AsyncIterator[AsyncSession]:
    """独立连接 —— 并发操作各用各的会话，才能产生真实事务并发。"""
    factory = get_sessionmaker()
    async with factory() as session:
        yield session


def ctx_for(user: User, tenant: Tenant) -> AuthContext:
    return AuthContext(
        user_id=str(user.id),
        session_id=str(uuid.uuid4()),
        tenant_id=str(tenant.id),
        tenant_role="owner",
        request_id=REQUEST_ID,
        email=user.email,
        display_name=user.display_name,
    )


def db_file_path() -> str:
    url = resolve_database_url(settings.storage.database_url)
    return url.split("///", 1)[-1]


@pytest.fixture()
async def seeded(db_session: AsyncSession):
    """租户 + 两个都是 Owner 的用户（"互相降级/退出"才可能把 Owner 数打到 0）。"""
    tag = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"SQLite不变量-{tag}", slug=f"sqlite-inv-{tag}")
    db_session.add(tenant)
    await db_session.flush()

    users: dict[str, User] = {}
    for name in ("alice", "bob"):
        user = User(
            email=normalize_email(f"{name}-{tag}@sqlite-invariant.test"),
            display_name=name,
            status="active",
        )
        db_session.add(user)
        users[name] = user
    await db_session.flush()

    network = Network(
        tenant_id=tenant.id,
        name=f"N-{tag}",
        owner_id=users["alice"].id,
        status="active",
        policy={},
    )
    db_session.add(network)
    await db_session.flush()

    db_session.add_all(
        [
            NetworkMembership(
                network_id=network.id,
                user_id=user.id,
                role=NETWORK_ROLE_OWNER,
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            )
            for user in users.values()
        ]
    )
    await db_session.commit()
    return tenant, users, network


async def _active_owners(session: AsyncSession, network_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(NetworkMembership)
                .where(
                    NetworkMembership.network_id == network_id,
                    NetworkMembership.role == NETWORK_ROLE_OWNER,
                    NetworkMembership.status == NETWORK_MEMBERSHIP_ACTIVE,
                )
            )
        ).scalar_one()
    )


# ── 1) 读事务不得持写锁（防止全局 BEGIN IMMEDIATE 回归）──────────────


async def test_read_transaction_does_not_hold_write_lock(seeded) -> None:
    """只读 session（模拟普通请求的 auth / 列表查询）不应阻塞写者。"""
    _tenant, _users, _network = seeded
    reader = get_sessionmaker()()
    try:
        await reader.execute(select(Network))  # 开事务 + 一条 SELECT
        raw = await aiosqlite.connect(db_file_path(), isolation_level=None)
        try:
            await raw.execute("PRAGMA busy_timeout=300")
            # 若读事务持写锁（全局 BEGIN IMMEDIATE），这里会 busy 超时
            await raw.execute("BEGIN IMMEDIATE")
            await raw.rollback()
        finally:
            await raw.close()
    finally:
        await reader.rollback()
        await reader.close()


# ── 2) lock_network 取得写锁，回滚后释放 ────────────────────────────


async def test_lock_network_acquires_and_releases_sqlite_write_lock(seeded) -> None:
    _tenant, _users, network = seeded
    session = get_sessionmaker()()
    try:
        locked = await lock_network(session, network.id)
        assert locked is not None

        raw = await aiosqlite.connect(db_file_path(), isolation_level=None)
        try:
            await raw.execute("PRAGMA busy_timeout=150")
            with pytest.raises(sqlite3.OperationalError):
                await raw.execute("BEGIN IMMEDIATE")
        finally:
            await raw.close()

        await session.rollback()  # 释放写锁

        raw = await aiosqlite.connect(db_file_path(), isolation_level=None)
        try:
            await raw.execute("PRAGMA busy_timeout=150")
            await raw.execute("BEGIN IMMEDIATE")
            await raw.rollback()
        finally:
            await raw.close()
    finally:
        await session.close()


# ── 3) 并发自我降级：最终 owners >= 1，败者 409 ─────────────────────


async def test_concurrent_owner_demotion_keeps_at_least_one_owner(
    seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, users, network = seeded

    import core.network.service as network_service

    real_count = network_service.count_active_owners
    barrier = asyncio.Barrier(2)
    observed: list[int] = []

    async def synchronized_count(session, *, network_id):
        count = await real_count(session, network_id=network_id)
        observed.append(count)
        try:
            # 修复前：两边都会到达这里 → 同时读到 2 → 双双降级 → 0 位 Owner
            await asyncio.wait_for(barrier.wait(), timeout=1.5)
        except TimeoutError:
            pass  # 修复后：后到者被写锁挡在到达这里之前，先到者超时后继续
        return count

    monkeypatch.setattr(network_service, "count_active_owners", synchronized_count)

    async def demote_self(user: User) -> tuple[str, str | None]:
        async with independent_session() as session:
            try:
                await change_member_role(
                    session,
                    ctx_for(user, tenant),
                    network_id=network.id,
                    target_user_id=user.id,
                    new_role=NETWORK_ROLE_MEMBER,
                    request_id=REQUEST_ID,
                )
                await session.commit()
                return ("ok", None)
            except ConflictError as exc:
                await session.rollback()
                return ("conflict", str(exc))

    results = await asyncio.wait_for(
        asyncio.gather(demote_self(users["alice"]), demote_self(users["bob"])),
        timeout=30,
    )

    async with independent_session() as session:
        owners = await _active_owners(session, network.id)

    assert owners >= 1, (
        f"网络变成了 0 位 Owner：并发降级结果={results}。"
        "「最后一位 Owner」守卫不是原子的 —— 成员变更前必须先取得写锁。"
    )
    assert owners == 1, f"两位 Owner 都被降级了？结果={results}"
    statuses = sorted(status for status, _ in results)
    assert statuses == ["conflict", "ok"], f"应恰好一个成功、一个 409：{results}"
    assert sorted(observed) == [1, 2] or sorted(observed) == [2, 2], observed


# ── 4) 并发退出：最终 owners >= 1 ───────────────────────────────────


async def test_concurrent_owner_leave_keeps_at_least_one_owner(
    seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, users, network = seeded

    import core.network.service as network_service

    real_count = network_service.count_active_owners
    barrier = asyncio.Barrier(2)

    async def synchronized_count(session, *, network_id):
        count = await real_count(session, network_id=network_id)
        try:
            await asyncio.wait_for(barrier.wait(), timeout=1.5)
        except TimeoutError:
            pass
        return count

    monkeypatch.setattr(network_service, "count_active_owners", synchronized_count)

    async def leave(user: User) -> tuple[str, str | None]:
        async with independent_session() as session:
            try:
                await leave_network(
                    session,
                    ctx_for(user, tenant),
                    network_id=network.id,
                    request_id=REQUEST_ID,
                )
                await session.commit()
                return ("ok", None)
            except ConflictError as exc:
                await session.rollback()
                return ("conflict", str(exc))

    results = await asyncio.wait_for(
        asyncio.gather(leave(users["alice"]), leave(users["bob"])),
        timeout=30,
    )

    async with independent_session() as session:
        owners = await _active_owners(session, network.id)

    assert owners >= 1, (
        f"两位 Owner 同时退出后网络无主：结果={results}。"
        "退出路径同样必须与其它成员变更串行化。"
    )
    assert owners == 1, f"两位 Owner 都退出了？结果={results}"
    statuses = sorted(status for status, _ in results)
    assert statuses == ["conflict", "ok"], f"应恰好一个成功、一个 409：{results}"

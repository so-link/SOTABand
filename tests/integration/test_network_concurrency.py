"""并发一致性测试 —— **必须在 PostgreSQL 上跑**。

## 为什么必须 PG

SQLite 是**单写者**：我们设了 ``busy_timeout=5000``，并发写会排队串行执行。
于是「最后一位 Owner 守卫」这类检查在 SQLite 上**永远不会真的竞争** ——
测试通过只说明"串行时逻辑对"，不说明"并发时不出错"。

所以本文件在非 PostgreSQL 上**显式跳过**，而不是给一个误导性的绿灯。

跑法：

```powershell
# 1. 起容器（一次性）
docker run -d --name sotaband-pg -e POSTGRES_USER=sotaband -e POSTGRES_PASSWORD=sotaband `
  -e POSTGRES_DB=sotaband -p 55432:5432 postgres:16-alpine
docker exec sotaband-pg psql -U sotaband -d sotaband -c "CREATE DATABASE sotaband_test;"

# 2. 跑本文件
$env:SOTABAND_TEST_DATABASE_URL='postgresql+asyncpg://sotaband:sotaband@localhost:55432/sotaband_test'
python -m pytest tests/integration/test_network_concurrency.py -v
```

## 测试方式

**不走 HTTP** —— API 层共享同一个 session（测试夹具覆盖了 ``get_db_session``），
天然没有并发。这里直接调服务层函数，**每个操作一个独立 session（独立连接）**，
这才产生真实的事务并发。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from core.auth.context import AuthContext
from core.auth.service import normalize_email
from core.auth.tokens import generate_session_token, hash_session_token
from core.db.base import utcnow
from core.db.models import (
    INVITATION_PENDING,
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_OWNER,
    Network,
    NetworkInvitation,
    NetworkMembership,
    Tenant,
    User,
)
from core.db.session import get_sessionmaker
from core.errors import ConflictError
from core.network.service import accept_invitation as accept_network_invitation
from core.network.service import change_member_role, remove_member

pytestmark = pytest.mark.skipif(
    "postgresql" not in settings.storage.database_url,
    reason=(
        "并发用例必须在 PostgreSQL 上跑：SQLite 单写者会把并发串行化，"
        "测出来的'通过'是假象（见 docs/adr-08-user-space-and-network.md ADR-08-02）"
    ),
)

REQUEST_ID = "concurrency-test"


# ── 脚手架 ────────────────────────────────────────────────────────────


@asynccontextmanager
async def independent_session() -> AsyncIterator[AsyncSession]:
    """一个**独立连接**的会话 —— 并发操作各用各的。"""
    factory = get_sessionmaker()
    async with factory() as session:
        yield session


def ctx_for(user: User, tenant: Tenant, *, role: str = NETWORK_ROLE_OWNER) -> AuthContext:
    return AuthContext(
        user_id=str(user.id),
        session_id=str(uuid.uuid4()),
        tenant_id=str(tenant.id),
        tenant_role=role,
        request_id=REQUEST_ID,
        email=user.email,
        display_name=user.display_name,
    )


async def seed_basic(session: AsyncSession) -> tuple[Tenant, dict[str, User], Network]:
    """建一个租户、三个用户、一个网络（alice=owner, bob=member）。

    ⚠️ 用独立的邮箱后缀，避免与其它测试数据冲突。
    """
    tag = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"并发测试-{tag}", slug=f"conc-{tag}")
    session.add(tenant)
    await session.flush()

    users: dict[str, User] = {}
    for name in ("alice", "bob", "carol"):
        user = User(
            email=normalize_email(f"{name}-{tag}@concurrency.test"),
            display_name=name,
            status="active",
        )
        session.add(user)
        users[name] = user
    await session.flush()

    network = Network(
        tenant_id=tenant.id,
        name=f"N-{tag}",
        owner_id=users["alice"].id,
        status="active",
        policy={},
    )
    session.add(network)
    await session.flush()

    session.add_all(
        [
            NetworkMembership(
                network_id=network.id,
                user_id=users["alice"].id,
                role=NETWORK_ROLE_OWNER,
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            ),
            NetworkMembership(
                network_id=network.id,
                user_id=users["bob"].id,
                role="member",
                status=NETWORK_MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
            ),
        ]
    )
    await session.commit()
    return tenant, users, network


async def count_active_owners(session: AsyncSession, network_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(NetworkMembership)
        .where(
            NetworkMembership.network_id == network_id,
            NetworkMembership.role == NETWORK_ROLE_OWNER,
            NetworkMembership.status == NETWORK_MEMBERSHIP_ACTIVE,
        )
    )
    return int(result.scalar_one())


# ── 用例 ──────────────────────────────────────────────────────────────


async def test_concurrent_role_change_only_one_wins(db_session: AsyncSession) -> None:
    """同一 ``expected_version`` 的并发改角色：**只能有一个成功**。

    这是 ``version`` compare-and-set 的核心保证（契约 §4 的 409 语义）。
    如果 CAS 失效，两个请求都会"成功"，最终角色取决于写入顺序 —— 不可预测。
    """
    tenant, users, network = await seed_basic(db_session)
    ctx = ctx_for(users["alice"], tenant)
    target = users["bob"].id

    async def attempt(role: str):
        async with independent_session() as session:
            try:
                result = await change_member_role(
                    session,
                    ctx,
                    network_id=network.id,
                    target_user_id=target,
                    new_role=role,
                    expected_version=1,  # 都基于 version=1
                    request_id=REQUEST_ID,
                )
                await session.commit()
                return ("ok", result.role)
            except ConflictError as exc:
                await session.rollback()
                return ("conflict", str(exc))

    results = await asyncio.gather(
        # 三个**都不同于当前角色（member）**的目标角色。
        # 若把 "member" 放进并发集，会命中"角色未变则幂等返回"的短路分支，
        # 那个分支不做 CAS —— 结果就变成 2 个成功，看起来像 CAS 失效，其实是测试写错。
        attempt(NETWORK_ROLE_ADMIN),
        attempt("viewer"),
        attempt(NETWORK_ROLE_OWNER),
    )

    succeeded = [r for r in results if r[0] == "ok"]
    conflicts = [r for r in results if r[0] == "conflict"]

    assert len(succeeded) == 1, f"CAS 失效：{len(succeeded)} 个请求同时成功 -> {results}"
    assert len(conflicts) == 2, results

    # version 只应推进一次（1 -> 2）
    async with independent_session() as session:
        row = (
            await session.execute(
                select(NetworkMembership).where(
                    NetworkMembership.network_id == network.id,
                    NetworkMembership.user_id == target,
                )
            )
        ).scalar_one()
        assert row.version == 2, f"version 应为 2，实际 {row.version}"


async def test_concurrent_invitation_accept_creates_one_membership(
    db_session: AsyncSession,
) -> None:
    """同一邀请令牌被并发接受：**只产生一条成员关系**。

    保障来自两处：邀请状态的 compare-and-set（同一令牌只能用一次）+
    ``UNIQUE(network_id, user_id)``（兜底）。
    """
    tenant, users, network = await seed_basic(db_session)
    carol = users["carol"]
    ctx = ctx_for(carol, tenant, role="member")

    token = generate_session_token()
    invitation = NetworkInvitation(
        network_id=network.id,
        email=carol.email,
        token_hash=hash_session_token(token),
        status=INVITATION_PENDING,
        expires_at=utcnow() + timedelta(days=1),
        invited_by=users["alice"].id,
    )
    db_session.add(invitation)
    await db_session.commit()

    async def attempt():
        async with independent_session() as session:
            try:
                await accept_network_invitation(
                    session, ctx, token=token, request_id=REQUEST_ID
                )
                await session.commit()
                return "ok"
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return type(exc).__name__

    results = await asyncio.gather(*[attempt() for _ in range(5)])

    async with independent_session() as session:
        memberships = (
            await session.execute(
                select(func.count())
                .select_from(NetworkMembership)
                .where(
                    NetworkMembership.network_id == network.id,
                    NetworkMembership.user_id == carol.id,
                    NetworkMembership.status == NETWORK_MEMBERSHIP_ACTIVE,
                )
            )
        ).scalar_one()

    assert memberships == 1, f"并发接受产生了 {memberships} 条成员关系（应为 1）"
    assert results.count("ok") >= 1, f"至少要有一个成功，实际：{results}"
    # 其余请求应因令牌已用而失败（GoneError -> 410），不能是 500
    assert "IntegrityError" not in results, f"不应靠唯一约束兜底暴露 500：{results}"


async def test_concurrent_remove_owners_never_leaves_zero_owners(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**最后 Owner 守卫的经典竞态**：两个 Owner 同时互相移除。

    正确行为：至少保留一位 Owner。
    典型错误：两个请求都读到"当前有 2 位 Owner"，都通过守卫，都移除 -> **0 位 Owner**，
    网络变成无人可管的孤儿。

    ## 为什么要用 barrier 强制交错

    直接 ``asyncio.gather`` 两个操作是**不可靠**的：协程的 await 点会让它们自然
    错开，一个可能先完整跑完，于是第二个读到 count=1 就正确拒绝了 —— 测试"通过"
    但什么都没验证到。

    这里 monkeypatch ``lock_network`` —— 让两个事务**都先到 barrier 报到**，然后
    才去抢锁。这样才确定性地制造出「两个事务同时想进临界区」的局面：

    - **有锁**：一个进去、另一个排队；先者移除后 count=1，后者重读后被守卫拒绝 → 剩 1 位 Owner ✅
    - **无锁**：两个都进去、都读到 count=2、都移除 → **0 位 Owner** ❌

    注意 barrier 不能放在 ``count_active_owners`` 里：加了锁之后第二个事务根本
    到不了那一步，会死锁（第一次写这个测试就是这么挂住的）。

    这正是必须用 PostgreSQL 的原因 —— SQLite 的库级写锁会让第二个事务进不来。
    """
    tenant, users, network = await seed_basic(db_session)

    # 先把 bob 升成第二个 owner，让"互相移除"成立
    async with independent_session() as session:
        row = (
            await session.execute(
                select(NetworkMembership).where(
                    NetworkMembership.network_id == network.id,
                    NetworkMembership.user_id == users["bob"].id,
                )
            )
        ).scalar_one()
        row.role = NETWORK_ROLE_OWNER
        await session.commit()

    # ── 强制交错：两个事务都到齐了才一起去抢锁 ──
    import core.network.service as network_service

    real_lock = network_service.lock_network
    barrier = asyncio.Barrier(2)

    async def synchronized_lock(session, network_id):
        await barrier.wait()  # 等另一个事务也准备就绪
        return await real_lock(session, network_id)

    monkeypatch.setattr(network_service, "lock_network", synchronized_lock)

    alice_ctx = ctx_for(users["alice"], tenant)
    bob_ctx = ctx_for(users["bob"], tenant)

    async def remove(target_ctx: AuthContext, target_id: uuid.UUID):
        async with independent_session() as session:
            try:
                await remove_member(
                    session,
                    target_ctx,
                    network_id=network.id,
                    target_user_id=target_id,
                    request_id=REQUEST_ID,
                )
                await session.commit()
                return "ok"
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return type(exc).__name__

    # 加超时：万一将来有人把锁去掉导致互相等待，测试应该失败而不是挂住整个套件
    results = await asyncio.wait_for(
        asyncio.gather(
            remove(alice_ctx, users["bob"].id),  # Alice 移除 Bob（owner）
            remove(bob_ctx, users["alice"].id),  # Bob 移除 Alice（owner）
        ),
        timeout=30,
    )

    async with independent_session() as session:
        owners = await count_active_owners(session, network.id)

    assert owners >= 1, (
        f"网络变成了 0 位 Owner 的孤儿！并发移除结果={results}。"
        "说明'最后一位 Owner'守卫不是原子的 —— "
        "需要在成员变更前对 network 行加排他锁（SELECT … FOR UPDATE）串行化。"
    )

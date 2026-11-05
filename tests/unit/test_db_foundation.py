"""数据库底座测试 —— 表结构、约束、手册规范。

归属需求 8.1/8.2；设计见 ``docs/design-08-schema.md``。

这些测试的重点**不是"表建出来了"**（那看一眼就知道），而是几件肉眼看不见、
一旦失效就会静默出错的事：

1. **阿里手册的三字段与单数表名** —— 规范只写在文档里迟早会漏，这里做成断言；
2. **无外键方案下 ORM 连接是否连对了行** —— 写错 ``primaryjoin`` 可能静默连错；
3. **唯一约束是真约束还是"先查后插"** —— 并发注册同一邮箱只能靠数据库唯一约束
   保证（PRD FR-A1）。先查后插有 TOCTOU 窗口；
4. **`user` 是 SQL 保留字** —— PostgreSQL 上必须加引号，漏了会在切库时才炸；
5. **约束命名约定是否生效** —— Alembic 在 SQLite 上必须走 batch 模式，而 batch
   需要按名字引用约束；匿名约束会让迁移在两种数据库上生成不同 DDL。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.schema import CreateTable

from config.settings import settings
from core.db.base import Base, utcnow
from core.db.models import (
    PUBLICATION_SCOPE_NETWORK,
    PUBLICATION_SCOPE_PRIVATE,
    PUBLICATION_SCOPE_TENANT,
    RESOURCE_TYPE_AGENT,
    AuditEvent,
    Installation,
    Publication,
    User,
    UserCredential,
    UserIdentity,
    UserSession,
)
from core.db.session import get_engine, get_sessionmaker

#: 已实现的全部表（docs/design-08-schema.md §1）。表名一律**单数**。
#: 批次 1：认证核心 + 审计；批次 2：租户；批次 3：网络
EXPECTED_TABLES = {
    # 批次 1
    "user",
    "user_identity",
    "user_credential",
    "session",
    "audit_event",
    # 批次 2
    "tenant",
    "tenant_membership",
    "tenant_invitation",
    # 批次 3
    "network",
    "network_membership",
    "network_invitation",
    # 批次 4（第 12 张表，用户拍板新增）
    "installation",
    # 批次 5（资源发布）
    "publication",
}

#: 批次 1 子集（部分测试只关心认证核心）
BATCH_1_TABLES = {
    "user",
    "user_identity",
    "user_credential",
    "session",
    "audit_event",
}

#: 阿里开发手册【强制】要求的必备字段
MANUAL_STANDARD_COLUMNS = {"id", "gmt_create", "gmt_modified"}


async def test_expected_tables_exist(db_session: AsyncSession) -> None:
    engine = get_engine()

    def _names(sync_conn):
        return set(inspect(sync_conn).get_table_names())

    async with engine.connect() as conn:
        names = await conn.run_sync(_names)

    assert EXPECTED_TABLES <= names, f"缺少表：{EXPECTED_TABLES - names}"


# ── 阿里开发手册规范 ────────────────────────────────────────────────


def test_every_table_has_manual_standard_columns() -> None:
    """手册【强制】：表必备三字段 ``id``、``gmt_create``、``gmt_modified``。

    由 ``UUIDPrimaryKeyMixin`` + ``TimestampMixin`` 结构性保证（见 ``core/db/base.py``）。
    这条测试是新表的守门人：忘记继承 mixin 会立刻失败。
    """
    for name, table in Base.metadata.tables.items():
        columns = set(table.columns.keys())
        missing = MANUAL_STANDARD_COLUMNS - columns
        assert not missing, f"表 {name} 缺少手册必备字段：{missing}"
        assert list(table.primary_key.columns.keys()) == ["id"], (
            f"表 {name} 的主键不是单列 id：{list(table.primary_key.columns.keys())}"
        )


def test_table_names_are_singular() -> None:
    """手册【强制】：表名不使用复数名词。

    这里用"不以 s 结尾"做启发式判断 —— 对本项目的表名足够，但它不是通用规则
    （``address`` 这类词会误判）。真正的守门人是
    ``test_metadata_declares_exactly_expected_tables`` 的精确集合比对。
    """
    for name in Base.metadata.tables:
        assert not name.endswith("s"), f"表名 {name} 使用了复数形式"


async def test_no_foreign_keys_anywhere(db_session: AsyncSession) -> None:
    """手册【强制】：**不得使用外键与级联。**

    这条测试是那条规范的执行者 —— 任何人加回 ``ForeignKey`` 都会在这里失败。
    替代方案（应用层校验）见 ``test_referential_integrity_is_not_enforced_by_db``。
    """
    engine = get_engine()

    def _fks(sync_conn):
        insp = inspect(sync_conn)
        return {table: insp.get_foreign_keys(table) for table in EXPECTED_TABLES}

    async with engine.connect() as conn:
        found = await conn.run_sync(_fks)

    offenders = {t: fks for t, fks in found.items() if fks}
    assert not offenders, f"这些表仍有外键：{offenders}"


def test_reserved_word_table_names_require_quoting() -> None:
    """⚠️ ``user`` 是 PostgreSQL 保留字 —— ORM 会自动加引号，**裸 SQL 必须手写**。

    SQLAlchemy 的 identifier preparer 会自动为保留字加引号，所以 ORM 用法没问题；
    但 ``SELECT * FROM user`` 在 PostgreSQL 上会报语法错误（SQLite 上却能过）——
    典型的"开发没问题、切库才炸"。

    这也是本测试的价值：把哪个表名需要引号**固定在代码里**，而不是等运维发现。
    """
    pg = postgresql.dialect()
    lite = sqlite.dialect()

    assert "user" in pg.identifier_preparer.reserved_words, "PG 应把 user 视为保留字"
    assert pg.identifier_preparer.quote("user") == '"user"'

    # 其余表名两个方言都不需要引号
    for name in ("session", "user_identity", "user_credential", "audit_event"):
        assert pg.identifier_preparer.quote(name) == name, f"{name} 在 PG 上被加了引号"
        assert lite.identifier_preparer.quote(name) == name, f"{name} 在 SQLite 上被加了引号"

    # 生成的 DDL 里确实带引号
    ddl = str(CreateTable(User.__table__).compile(dialect=pg))
    assert 'CREATE TABLE "user"' in ddl, ddl.splitlines()[0]


async def test_constraint_names_follow_convention(db_session: AsyncSession) -> None:
    """约束名必须稳定且符合阿里开发手册的索引命名。

    - 稳定：Alembic batch 模式要按名字引用既有约束；匿名约束（如 SQLite 自增的
      ``sqlite_autoindex_user_1``）会让 SQLite 与 PostgreSQL 的迁移生成不同 DDL。
    - 命名：手册规定主键 ``pk_``、唯一索引 ``uk_``、普通索引 ``idx_``。
    """
    engine = get_engine()

    def _constraints(sync_conn):
        insp = inspect(sync_conn)
        return {
            "user_unique": {u["name"] for u in insp.get_unique_constraints("user")},
            "session_indexes": {i["name"] for i in insp.get_indexes("session")},
            "identity_indexes": {i["name"] for i in insp.get_indexes("user_identity")},
            "credential_unique": {
                u["name"] for u in insp.get_unique_constraints("user_credential")
            },
        }

    async with engine.connect() as conn:
        found = await conn.run_sync(_constraints)

    assert "uk_user_email" in found["user_unique"], found["user_unique"]
    assert "idx_session_user_id_revoked_at" in found["session_indexes"], found[
        "session_indexes"
    ]
    assert "idx_session_expires_at" in found["session_indexes"], found["session_indexes"]
    assert "uk_user_identity_issuer_subject" in found["identity_indexes"], found[
        "identity_indexes"
    ]
    # "一人一条凭证"的保证
    assert "uk_user_credential_user_id" in found["credential_unique"], found[
        "credential_unique"
    ]

    # 不得再出现旧前缀（ix_/uq_）
    all_names = set().union(*found.values())
    assert not {n for n in all_names if n.startswith(("ix_", "uq_"))}, all_names


# ── 无外键方案的直接后果 ────────────────────────────────────────────


async def test_referential_integrity_is_not_enforced_by_db(
    db_session: AsyncSession,
) -> None:
    """⚠️ **无外键的直接代价**：数据库不会拦住"孤儿行"。

    这条测试刻意固化"数据库不管"这一事实 —— 它不是缺陷报告，而是把
    **应用层必须自己校验引用存在性**这个责任显性化。看到这条测试失败（即
    插入被拒了），说明有人加了外键，需要同步更新 ADR 与设计文档。

    应用层的正确做法（批次 2/3 落地时一并实现）：
    - 写 ``user_credential`` / ``user_identity`` / ``session`` 前先确认 ``user`` 存在；
    - 删除用户时按依赖顺序显式清理子表，不能依赖级联。
    """
    # PRAGMA 仍开着（纵深防御），但没有外键可强制，所以孤儿行会写成功。
    # 这条 PRAGMA 是 SQLite 特有的（PostgreSQL 强制外键是常态，不需要开关）。
    if "sqlite" in settings.storage.database_url:
        pragma = await db_session.execute(text("PRAGMA foreign_keys"))
        assert pragma.scalar_one() == 1

    orphan_user_id = uuid.uuid4()
    db_session.add(UserCredential(user_id=orphan_user_id, password_hash="$2b$12$fake"))
    await db_session.commit()  # 不应抛异常 —— 数据库不校验引用完整性

    db_session.add(
        UserSession(
            user_id=orphan_user_id,
            token_hash="f" * 64,
            expires_at=utcnow() + timedelta(days=1),
        )
    )
    await db_session.commit()  # 同上

    count = await db_session.execute(
        select(func.count())
        .select_from(UserCredential)
        .where(UserCredential.user_id == orphan_user_id)
    )
    assert count.scalar_one() == 1, "孤儿凭证行应当写成功（无外键）"


# ── 唯一约束与校验约束 ──────────────────────────────────────────────


async def test_unique_email_is_enforced_by_the_database(db_session: AsyncSession) -> None:
    """并发注册同一邮箱只能靠唯一约束拦住（PRD FR-A1）。"""
    db_session.add(User(email="alice@example.com", display_name="Alice"))
    await db_session.commit()

    db_session.add(User(email="alice@example.com", display_name="Alice 2"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_multiple_users_may_have_no_email(db_session: AsyncSession) -> None:
    """email 可空且允许多行 NULL。

    GitHub 用户未公开邮箱时 email 为空（ADR-08-01）。若把 email 设为 NOT NULL，
    Phase 2 接入 OIDC 时会被迫造假数据；而 UNIQUE 在 SQLite/PostgreSQL 上都
    允许多个 NULL，所以两个无邮箱用户必须能共存。
    """
    db_session.add_all(
        [
            User(email=None, display_name="NoMail A"),
            User(email=None, display_name="NoMail B"),
        ]
    )
    await db_session.commit()  # 不应抛异常

    result = await db_session.execute(
        select(func.count()).select_from(User).where(User.email.is_(None))
    )
    assert result.scalar_one() == 2


async def test_identity_issuer_subject_is_unique(db_session: AsyncSession) -> None:
    """(issuer, subject) 唯一 —— PRD FR-A1 明确要求，且不以邮箱作外部主键。"""
    user = User(email="bob@example.com", display_name="Bob")
    db_session.add(user)
    await db_session.flush()

    db_session.add(UserIdentity(user_id=user.id, issuer="local", subject=str(user.id)))
    await db_session.commit()

    other = User(email="bob2@example.com", display_name="Bob2")
    db_session.add(other)
    await db_session.flush()

    db_session.add(UserIdentity(user_id=other.id, issuer="local", subject=str(user.id)))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_same_issuer_different_subject_is_allowed(db_session: AsyncSession) -> None:
    """同一 issuer 下不同 subject 是不同身份；同一用户可绑定多个 issuer（Phase 2）。"""
    user = User(email="multi@example.com", display_name="Multi")
    db_session.add(user)
    await db_session.flush()

    db_session.add_all(
        [
            UserIdentity(user_id=user.id, issuer="local", subject=str(user.id)),
            UserIdentity(user_id=user.id, issuer="github", subject="12345678"),
        ]
    )
    await db_session.commit()

    # 用 ORM 查询而不是裸 SQL —— 裸 SQL 需要自己处理 Uuid 的存储格式，
    # 见 test_uuid_storage_format_on_sqlite。
    result = await db_session.execute(
        select(func.count())
        .select_from(UserIdentity)
        .where(UserIdentity.user_id == user.id)
    )
    assert result.scalar_one() == 2


async def test_one_credential_per_user(db_session: AsyncSession) -> None:
    """``uk_user_credential_user_id`` 保证"一人一条凭证"。

    按手册每个表都要有独立 ``id`` 主键，所以 ``user_id`` 从主键退为"普通列 +
    唯一约束"。这条测试证明保证**没有减弱**。
    """
    user = User(email="cred@example.com", display_name="Cred")
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        UserCredential(user_id=user.id, password_hash="$2b$12$first")
    )
    await db_session.commit()

    db_session.add(
        UserCredential(user_id=user.id, password_hash="$2b$12$second")
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_session_token_hash_is_unique(db_session: AsyncSession) -> None:
    user = User(email="sess@example.com", display_name="Sess")
    db_session.add(user)
    await db_session.flush()

    expires = utcnow() + timedelta(days=30)
    db_session.add(UserSession(user_id=user.id, token_hash="a" * 64, expires_at=expires))
    await db_session.commit()

    db_session.add(UserSession(user_id=user.id, token_hash="a" * 64, expires_at=expires))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_user_status_check_constraint(db_session: AsyncSession) -> None:
    db_session.add(User(email="bad@example.com", display_name="Bad", status="bogus"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_audit_event_check_constraints(db_session: AsyncSession) -> None:
    db_session.add(
        AuditEvent(
            actor_id="u1",
            actor_kind="bogus",  # 非法
            action="auth.login",
            result="success",
            request_id="req-1",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    db_session.add(
        AuditEvent(
            actor_id="u1",
            actor_kind="user",
            action="auth.login",
            result="bogus",  # 非法
            request_id="req-1",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_audit_may_reference_deleted_entities(db_session: AsyncSession) -> None:
    """审计内容是**逻辑引用**，可以指向已经不存在的对象（PRD §11）。

    「审计保留期独立于成员删除」意味着：移除一个成员不能连带抹掉他做过的
    操作痕迹。所以审计行里的 ``actor_id`` / ``tenant_id`` / ``network_id``
    必须能指向已删除的实体，且永远不能级联删除。
    """
    db_session.add(
        AuditEvent(
            actor_id=str(uuid.uuid4()),
            actor_kind="user",
            tenant_id=uuid.uuid4(),  # 不存在的租户
            network_id=uuid.uuid4(),  # 不存在的网络
            action="network.member.remove",
            result="success",
            request_id="req-2",
        )
    )
    await db_session.commit()  # 不应抛异常

    count = await db_session.execute(select(func.count()).select_from(AuditEvent))
    assert count.scalar_one() == 1


# ── Uuid 与时间的行为 ───────────────────────────────────────────────


async def test_uuid_round_trips_as_uuid_object(db_session: AsyncSession) -> None:
    """主键用 sqlalchemy.Uuid：Python 侧是 uuid.UUID，不是字符串。

    跨方言可移植（SQLite 存 CHAR(32)、PostgreSQL 存原生 uuid），
    到 API 边界再转字符串（见 contract §1.1 的 AuthContext.user_id: str）。
    """
    user = User(email="uuid@example.com", display_name="UUID")
    db_session.add(user)
    await db_session.commit()
    uid = user.id
    assert isinstance(uid, uuid.UUID)

    factory = get_sessionmaker()
    async with factory() as fresh:
        fetched = await fresh.get(User, uid)
        assert fetched is not None
        assert fetched.id == uid


@pytest.mark.skipif(
    "sqlite" not in settings.storage.database_url,
    reason="这条测的是 SQLite 特有的 Uuid 存储格式（CHAR(32) hex）；PG 用原生 uuid 类型",
)
async def test_uuid_storage_format_on_sqlite(db_session: AsyncSession) -> None:
    """⚠️ 陷阱：SQLite 上 ``Uuid`` 列存的是 **32 位无连字符 hex**，不是 ``str(uuid)``。

    ``sqlalchemy.Uuid`` 在无原生 UUID 类型的后端（SQLite）上以 ``CHAR(32)``
    存储 ``uuid.hex``；PostgreSQL 上则用原生 ``uuid`` 类型。

    后果：**裸 SQL 里用 ``WHERE id = :uid`` 且传 ``str(uuid)`` 会一行都查不到**
    —— 不报错，只是静默返回空结果，非常难排查。

    正确做法二选一：
    - 用 ORM 条件（``Model.id == some_uuid``），绑定处理器会做正确转换；
    - 必须用裸 SQL 时，传 ``value.hex`` 而不是 ``str(value)``。
    """
    user = User(email="fmt@example.com", display_name="Fmt")
    db_session.add(user)
    await db_session.commit()

    # 表名 user 是保留字，裸 SQL 必须加引号（见 test_reserved_word_table_names...）
    raw = await db_session.execute(
        text('SELECT id, length(id) FROM "user" WHERE email = \'fmt@example.com\'')
    )
    stored_id, stored_len = raw.one()
    assert stored_id == user.id.hex
    assert stored_len == 32
    assert "-" not in stored_id

    # 用 str(uuid)（带连字符）去匹配 → 查不到
    miss = await db_session.execute(
        text('SELECT COUNT(*) FROM "user" WHERE id = :uid'),
        {"uid": str(user.id)},
    )
    assert miss.scalar_one() == 0

    # 用 .hex 去匹配 → 命中
    hit = await db_session.execute(
        text('SELECT COUNT(*) FROM "user" WHERE id = :uid'),
        {"uid": user.id.hex},
    )
    assert hit.scalar_one() == 1


async def test_user_session_is_active_semantics(db_session: AsyncSession) -> None:
    """is_active 的三态，以及 SQLite 返回 naive datetime 时的 UTC 约定。

    SQLite 没有时区类型，``DateTime(timezone=True)`` 读回来是 naive。约定
    「读到的 naive datetime 一律视作 UTC」（``core/db/base.py``）。这里通过
    ``refresh`` 强制从数据库重新加载，走的就是那条真实路径。
    """
    user = User(email="act@example.com", display_name="Act")
    db_session.add(user)
    await db_session.flush()

    now = utcnow()
    valid = UserSession(
        user_id=user.id, token_hash="1" * 64, expires_at=now + timedelta(days=1)
    )
    expired = UserSession(
        user_id=user.id, token_hash="2" * 64, expires_at=now - timedelta(days=1)
    )
    revoked = UserSession(
        user_id=user.id,
        token_hash="3" * 64,
        expires_at=now + timedelta(days=1),
        revoked_at=now,
    )
    db_session.add_all([valid, expired, revoked])
    await db_session.commit()

    for session_obj in (valid, expired, revoked):
        await db_session.refresh(session_obj)

    # UTCDateTime 保证读出来带 tzinfo —— 这正是它存在的理由。
    # 若换成裸 DateTime(timezone=True)，SQLite 会返回 naive，与 utcnow() 比较
    # 直接抛 TypeError。
    assert valid.expires_at.tzinfo is not None, "UTCDateTime 应补回 UTC tzinfo"
    assert valid.expires_at.utcoffset().total_seconds() == 0

    assert valid.is_active is True
    assert expired.is_active is False
    assert revoked.is_active is False


async def test_utc_datetime_round_trip(db_session: AsyncSession) -> None:
    """``UTCDateTime`` 保证从库里读出来的时间**永远是 aware UTC**。

    这是对"读取时自动补 tzinfo"的回归测试。它挡住的是一个只在"从库里读出来的
    对象"上出现、同一段代码有时报错有时不报的 bug 类：

        expires_at > utcnow()   # naive vs aware → TypeError

    裸 ``DateTime(timezone=True)`` 在 SQLite 上就会这样。
    """
    user = User(email="tz@example.com", display_name="TZ")
    db_session.add(user)
    await db_session.flush()

    expires = utcnow() + timedelta(days=1)
    session_obj = UserSession(
        user_id=user.id, token_hash="9" * 64, expires_at=expires
    )
    db_session.add(session_obj)
    await db_session.commit()
    await db_session.refresh(session_obj)

    # 1) 读回来是 aware
    assert session_obj.expires_at.tzinfo is not None
    # 2) 与 utcnow() 可直接比较（裸 DateTime 在这里会抛 TypeError）
    assert session_obj.expires_at > utcnow()
    # 3) gmt_create / gmt_modified 同样
    assert user.gmt_create.tzinfo is not None
    assert user.gmt_modified.tzinfo is not None
    # 4) 时间值没有被时区转换搞错
    assert abs((session_obj.expires_at - expires).total_seconds()) < 1


async def test_naive_datetime_is_treated_as_utc_on_write(
    db_session: AsyncSession,
) -> None:
    """写入 naive datetime 时按约定视作 UTC，不按本地时区解释。

    若把 naive 当成本地时间，中国时区下写进去的时间会偏 8 小时 —— 这是静默的
    数据错误，不会报错。``UTCDateTime.process_bind_param`` 明确按 UTC 处理。
    """
    naive = datetime(2030, 1, 1, 12, 0, 0)  # 无 tzinfo
    user = User(email="naive@example.com", display_name="Naive")
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        UserSession(user_id=user.id, token_hash="8" * 64, expires_at=naive)
    )
    await db_session.commit()

    fetched = (
        await db_session.execute(
            select(UserSession).where(UserSession.token_hash == "8" * 64)
        )
    ).scalar_one()

    assert fetched.expires_at.tzinfo is not None
    assert fetched.expires_at.utcoffset().total_seconds() == 0
    # 12:00 被当作 UTC 12:00，而不是本地 12:00（那会变成 UTC 04:00）
    assert fetched.expires_at.replace(tzinfo=None) == naive


async def test_gmt_fields_are_populated_and_updated(db_session: AsyncSession) -> None:
    """``gmt_create`` / ``gmt_modified`` 由 mixin 自动填充与刷新。

    手册要求每个表都有这两个字段；如果 mixin 的 ``default``/``onupdate`` 没生效，
    它们会是 NULL 而 NOT NULL 约束又会拦住插入 —— 但更隐蔽的情况是"能插进去但
    时间不对"，所以这里显式验证语义。
    """
    user = User(email="gmt@example.com", display_name="GMT")
    db_session.add(user)
    await db_session.commit()

    assert user.gmt_create is not None
    assert user.gmt_modified is not None
    created = user.gmt_create

    user.display_name = "GMT Updated"
    await db_session.commit()
    await db_session.refresh(user)

    assert user.gmt_modified > created, "更新后 gmt_modified 应当推进"
    assert user.gmt_create == created, "更新不应改变 gmt_create"


async def test_metadata_declares_exactly_expected_tables(db_session: AsyncSession) -> None:
    """防止未实现批次的表被意外加进来。

    批次 4（publication）尚未实现。若这里失败，说明有人在没有对应迁移的情况下
    往 metadata 里加了表 —— 那会导致 ``alembic check`` 报差异，或者运行时
    "no such table"。
    """
    assert set(Base.metadata.tables) == EXPECTED_TABLES


# ── 无外键方案下，ORM 关系是否还连得对 ────────────────────────────────


async def test_relationships_join_correctly_without_foreign_keys(
    db_session: AsyncSession,
) -> None:
    """⚠️ 无外键后，连接条件由 ``primaryjoin`` 提供 —— 必须证明它连的是**对的行**。

    没有外键，SQLAlchemy 无法推断连接，也不会替我们校验 ``primaryjoin`` 写对没有。
    写错的后果可能是：

    - 直接报错（``NoForeignKeysError``）—— 好消息，立刻发现；
    - **静默连错行或产生笛卡尔积** —— 坏消息，这条测试就是为了堵它。

    所以这里插入两个用户，各自有子行，然后确认加载出来的只属于自己的那些。
    """
    alice = User(email="rel-alice@example.com", display_name="Alice")
    bob = User(email="rel-bob@example.com", display_name="Bob")
    db_session.add_all([alice, bob])
    await db_session.flush()

    db_session.add_all(
        [
            UserIdentity(user_id=alice.id, issuer="local", subject=str(alice.id)),
            UserIdentity(user_id=bob.id, issuer="local", subject=str(bob.id)),
            UserCredential(user_id=alice.id, password_hash="$2b$12$alice"),
            UserSession(
                user_id=alice.id,
                token_hash="a" * 64,
                expires_at=utcnow() + timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()

    stmt = (
        select(User)
        .where(User.id == alice.id)
        .options(
            selectinload(User.identities),
            selectinload(User.credential),
            selectinload(User.sessions),
        )
    )
    loaded = (await db_session.execute(stmt)).scalar_one()

    # identities：只有 Alice 自己那条，不能把 Bob 的也带出来
    assert len(loaded.identities) == 1, "primaryjoin 连错了：捞到了别人的身份"
    assert loaded.identities[0].user_id == alice.id

    # credential：一对一
    assert loaded.credential is not None
    assert loaded.credential.user_id == alice.id

    # sessions：同理
    assert len(loaded.sessions) == 1
    assert loaded.sessions[0].user_id == alice.id

    # 反向关系也要连对
    back = (
        await db_session.execute(
            select(UserIdentity)
            .where(UserIdentity.user_id == bob.id)
            .options(selectinload(UserIdentity.user))
        )
    ).scalar_one()
    assert back.user.email == "rel-bob@example.com"


async def test_lazy_loading_raises_instead_of_silently_querying(
    db_session: AsyncSession,
) -> None:
    """``lazy="raise"`` 让 async 下的隐式懒加载立刻失败，而不是抛 MissingGreenlet。

    async 上下文里触发懒加载会抛 ``MissingGreenlet``，报错位置离真正原因很远。
    设成 ``raise`` 后问题在定义处就暴露，并提示改用 ``selectinload()``。
    """
    user = User(email="lazy@example.com", display_name="Lazy")
    db_session.add(user)
    await db_session.commit()

    with pytest.raises(InvalidRequestError, match="lazy"):
        _ = user.identities


# ══════════════════════════════════════════════════════════════════════
# 批次 4 —— installation（用户 2026-10-26 拍板新增的第 12 张表）
# ══════════════════════════════════════════════════════════════════════


class TestInstallationTable:
    """``installation`` 表的结构约束。

    这张表存在的唯一理由是 **审计可追溯**（PRD FR-N7）：没有安装记录就没有稳定的
    工作负载主体 id。所以下面的断言集中在"能不能唯一确定一条安装记录"和
    "吊销是否留痕"上。
    """

    async def test_has_uuid_pk_and_manual_timestamps(
        self, db_session: AsyncSession
    ) -> None:
        """阿里手册【强制】：主键 + ``gmt_create`` / ``gmt_modified``。"""
        columns = {c.name for c in Installation.__table__.columns}
        assert {"id", "gmt_create", "gmt_modified"} <= columns
        # 主键是 UUID，不是自增 bigint —— 安装 id 会出现在 URL 里，不能可枚举
        assert Installation.__table__.c.id.primary_key

    async def test_one_install_per_user_per_publication(
        self, db_session: AsyncSession
    ) -> None:
        """``(tenant_id, installed_by, publication_id)`` 唯一。

        重复安装必须是"重新激活同一行"，而不是新增行 —— 否则审计里会出现
        十几条同一用户的安装记录，看不出到底哪次是有效的。
        """
        tenant_id = uuid.uuid4()
        publication_id = uuid.uuid4()
        user_id = uuid.uuid4()

        db_session.add(
            Installation(
                tenant_id=tenant_id,
                publication_id=publication_id,
                installed_by=user_id,
            )
        )
        await db_session.commit()

        db_session.add(
            Installation(
                tenant_id=tenant_id,
                publication_id=publication_id,
                installed_by=user_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_same_publication_installed_by_two_users_is_allowed(
        self, db_session: AsyncSession
    ) -> None:
        """同一条发布项可以被多个成员各自安装 —— 唯一约束必须只精确到"人"。"""
        tenant_id = uuid.uuid4()
        publication_id = uuid.uuid4()

        db_session.add_all(
            [
                Installation(
                    tenant_id=tenant_id,
                    publication_id=publication_id,
                    installed_by=uuid.uuid4(),
                ),
                Installation(
                    tenant_id=tenant_id,
                    publication_id=publication_id,
                    installed_by=uuid.uuid4(),
                ),
            ]
        )
        await db_session.commit()  # 不应抛

    async def test_default_status_is_active(self, db_session: AsyncSession) -> None:
        installation = Installation(
            tenant_id=uuid.uuid4(),
            publication_id=uuid.uuid4(),
            installed_by=uuid.uuid4(),
        )
        db_session.add(installation)
        await db_session.commit()
        assert installation.status == "active"

    async def test_invalid_status_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(
            Installation(
                tenant_id=uuid.uuid4(),
                publication_id=uuid.uuid4(),
                installed_by=uuid.uuid4(),
                status="deleted",  # 只允许 active / revoked
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_revoke_keeps_the_row(self, db_session: AsyncSession) -> None:
        """吊销是**软删除**：行保留，只改状态。

        审计要能回答「谁什么时候装过、什么时候被吊销」，物理删除就把证据丢了。
        """
        installation = Installation(
            tenant_id=uuid.uuid4(),
            publication_id=uuid.uuid4(),
            installed_by=uuid.uuid4(),
        )
        db_session.add(installation)
        await db_session.commit()
        installation_id = installation.id

        installation.status = "revoked"
        await db_session.commit()

        reloaded = await db_session.get(Installation, installation_id)
        assert reloaded is not None
        assert reloaded.status == "revoked"

    def test_no_foreign_keys(self) -> None:
        """无外键（阿里手册）：``publication_id`` 指向 Step 5 才落地的表。

        这不是疏漏 —— 无外键方案要求应用层在安装前自行确认 publication 存在。
        """
        assert Installation.__table__.foreign_keys == set()


# ══════════════════════════════════════════════════════════════════════
# 批次 5 —— publication（资源发布）
# ══════════════════════════════════════════════════════════════════════


def _publication(**overrides: object) -> Publication:
    """构造一条合法的 publication（测试用默认值）。"""
    data: dict[str, object] = {
        "resource_type": RESOURCE_TYPE_AGENT,
        "release_id": "agent-model-v1",
        "scope": PUBLICATION_SCOPE_NETWORK,
        "scope_id": uuid.uuid4(),
        "published_by": uuid.uuid4(),
    }
    data.update(overrides)
    return Publication(**data)  # type: ignore[arg-type]


class TestPublicationTable:
    """``publication`` 表的结构约束与那条**补口子的部分唯一索引**。"""

    async def test_has_uuid_pk_and_manual_timestamps(self) -> None:
        columns = {c.name for c in Publication.__table__.columns}
        assert {"id", "gmt_create", "gmt_modified"} <= columns
        assert Publication.__table__.c.id.primary_key

    async def test_same_release_cannot_be_published_twice_to_same_network(
        self, db_session: AsyncSession
    ) -> None:
        """契约约束：``UNIQUE(resource_type, release_id, scope, scope_id)``。"""
        network_id = uuid.uuid4()
        publisher = uuid.uuid4()

        db_session.add(_publication(scope_id=network_id, published_by=publisher))
        await db_session.commit()

        db_session.add(_publication(scope_id=network_id, published_by=publisher))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_global_scope_duplicate_is_still_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """⚠️ 这条测试守住一个**容易被漏掉的口子**。

        ``scope ∈ {private, public}`` 时 ``scope_id`` 为 NULL。而 SQL 标准里
        **NULL 互不相等**，所以契约那条四列唯一约束对它们**完全失效** ——
        同一版本可以被重复发布出多行。

        本实现额外加了一条 ``WHERE scope_id IS NULL`` 的**部分唯一索引**兜住。
        如果谁把这行删了，这条测试会立刻失败。
        """
        publisher = uuid.uuid4()

        db_session.add(
            _publication(scope=PUBLICATION_SCOPE_PRIVATE, scope_id=None, published_by=publisher)
        )
        await db_session.commit()

        db_session.add(
            _publication(scope=PUBLICATION_SCOPE_PRIVATE, scope_id=None, published_by=publisher)
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_same_release_different_network_is_allowed(
        self, db_session: AsyncSession
    ) -> None:
        """同一版本可以发布到多个网络 —— 唯一性只精确到"范围"。"""
        publisher = uuid.uuid4()
        db_session.add_all(
            [
                _publication(scope_id=uuid.uuid4(), published_by=publisher),
                _publication(scope_id=uuid.uuid4(), published_by=publisher),
            ]
        )
        await db_session.commit()  # 不应抛

    async def test_network_scope_requires_scope_id(
        self, db_session: AsyncSession
    ) -> None:
        """PRD §7.2 原文要求：发布到网络范围时必须指明是哪个网络。"""
        db_session.add(_publication(scope=PUBLICATION_SCOPE_NETWORK, scope_id=None))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_tenant_scope_requires_scope_id(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(_publication(scope=PUBLICATION_SCOPE_TENANT, scope_id=None))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_private_scope_allows_null_scope_id(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(_publication(scope=PUBLICATION_SCOPE_PRIVATE, scope_id=None))
        await db_session.commit()  # 不应抛
        assert True

    async def test_invalid_resource_type_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(_publication(resource_type="workflow"))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_invalid_scope_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(_publication(scope="world", scope_id=None))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_default_status_is_active(self, db_session: AsyncSession) -> None:
        publication = _publication()
        db_session.add(publication)
        await db_session.commit()
        assert publication.status == "active"

    async def test_no_foreign_keys(self) -> None:
        """无外键 —— 而且 ``release_id`` 这一列**连外键也无能为力**：
        资源本体存在 registry.json / 文件系统里，不在数据库里（ADR-08-08）。
        """
        assert Publication.__table__.foreign_keys == set()

    def test_release_id_is_a_string_not_a_uuid(self) -> None:
        """``release_id`` 是跨系统逻辑引用，必须能装下非 UUID 的版本号。

        如果哪天有人把它改成 ``Uuid`` 类型，这条会失败 —— 那会破坏与
        ``resources/*/registry.json`` 的对接。
        """
        col = Publication.__table__.c.release_id
        assert col.type.length == 64
        # Uuid 类型没有 length 属性；String(64) 才有
        assert not str(col.type).lower().startswith("uuid")


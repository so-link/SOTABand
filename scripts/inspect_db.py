r"""打印当前数据库的表结构与行数 —— 验收用。

## 为什么要有这个脚本

验收时经常要回答「这些表到底建了没、有没有外键、迁移到哪一版了」。
临时在 PowerShell 里拼 Python 代码会被引号转义搞死（here-string 里再嵌
f-string 的花括号和引号极其易错），所以固化成脚本。

## 用法

```powershell
# 在项目根目录执行
$env:PYTHONUTF8=1

# 默认读 storage/sotaband.db（SQLite）
.\venv\Scripts\python.exe scripts\inspect_db.py

# 或指定任意 DATABASE_URL（含 PostgreSQL）
$env:DATABASE_URL='postgresql+asyncpg://sotaband:sotaband@localhost:55432/sotaband'
.\venv\Scripts\python.exe scripts\inspect_db.py
```

## 期望看到

- 迁移版本 = 当前 head（写这份文档时是 ``0005``）
- **12 张业务表** + ``alembic_version``
- **外键数量 = 0** —— 这是刻意的（阿里开发手册【强制】不得使用外键与级联，
  见 ADR-08-11）。如果这里不是 0，说明有人加了外键，要回头核对决策。
"""

from __future__ import annotations

import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url

# 期望的业务表（docs/design-08-schema.md §1，共 13 张）
EXPECTED_TABLES = {
    "user",
    "user_identity",
    "user_credential",
    "session",
    "audit_event",
    "tenant",
    "tenant_membership",
    "tenant_invitation",
    "network",
    "network_membership",
    "network_invitation",
    "installation",
    "publication",
}


def main() -> int:
    try:
        from config.settings import settings
        from core.db.session import resolve_database_url
    except Exception as exc:  # noqa: BLE001
        print(f"导入项目配置失败：{exc}")
        return 2

    url = resolve_database_url(settings.storage.database_url)
    parsed = make_url(url)
    where = parsed.host or ""
    if parsed.port:
        where = f"{where}:{parsed.port}"
    if parsed.database:
        where = f"{where}/{parsed.database}" if where else parsed.database
    print(f"数据库：{parsed.drivername}  （{where}）")
    print()

    if parsed.drivername.startswith("sqlite"):
        return _inspect_sqlite(parsed.database or "")
    return _inspect_async(url)


# ── SQLite ────────────────────────────────────────────────────────────


def _inspect_sqlite(path: str) -> int:
    import sqlite3
    from urllib.parse import unquote

    from core.db.session import resolve_database_url  # noqa: F401  (保持一致的类型)

    db_path = unquote(path).lstrip("/") if path.startswith("/") else unquote(path)
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        print(f"打不开数据库 {db_path}：{exc}")
        return 2

    row = conn.execute("select version_num from alembic_version").fetchone()
    print(f"迁移版本：{row[0] if row else '（无 alembic_version 表）'}")
    print()

    tables = [
        r[0]
        for r in conn.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%' order by name"
        )
    ]
    print(f"{'表名':<26}{'行数':>8}")
    print("-" * 34)
    for table in tables:
        count = conn.execute(f'select count(*) from "{table}"').fetchone()[0]
        print(f"{table:<26}{count:>8}")

    fk_total = sum(
        len(conn.execute(f'pragma foreign_key_list("{t}")').fetchall()) for t in tables
    )
    print()
    print(f"外键数量：{fk_total}   （期望 0 —— 阿里开发手册【强制】不得使用外键与级联）")
    return _report_gaps(set(tables), fk_total)


# ── PostgreSQL / 其它 ─────────────────────────────────────────────────


def _inspect_async(url: str) -> int:
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    async def run() -> tuple[str, list[tuple[str, int]], int]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                version = (
                    await conn.execute(text("select version_num from alembic_version"))
                ).scalar_one_or_none()

                rows = (
                    await conn.execute(
                        text(
                            "select tablename from pg_tables "
                            "where schemaname='public' order by tablename"
                        )
                    )
                ).scalars()
                tables = list(rows)

                counts: list[tuple[str, int]] = []
                for table in tables:
                    n = (
                        await conn.execute(text(f'select count(*) from "{table}"'))
                    ).scalar_one()
                    counts.append((table, int(n)))

                fk = (
                    await conn.execute(
                        text(
                            "select count(*) from information_schema.table_constraints "
                            "where constraint_type='FOREIGN KEY' "
                            "and table_schema='public'"
                        )
                    )
                ).scalar_one()
                return version or "（空）", counts, int(fk)
        finally:
            await engine.dispose()

    version, counts, fk_total = asyncio.run(run())
    print(f"迁移版本：{version}")
    print()
    print(f"{'表名':<26}{'行数':>8}")
    print("-" * 34)
    for table, n in counts:
        print(f"{table:<26}{n:>8}")
    print()
    print(f"外键数量：{fk_total}   （期望 0 —— 阿里开发手册【强制】不得使用外键与级联）")
    return _report_gaps({t for t, _ in counts}, fk_total)


# ── 一致性结论 ────────────────────────────────────────────────────────


def _report_gaps(tables: set[str], fk_total: int) -> int:
    print()
    missing = EXPECTED_TABLES - tables

    if missing:
        print(f"❌ 缺少业务表：{sorted(missing)}")
        print("   修复：python -m alembic upgrade head")
        return 1

    print(f"✅ {len(EXPECTED_TABLES)} 张业务表齐全")

    if fk_total:
        print(f"❌ 检测到 {fk_total} 个外键 —— 与本项目 ADR-08-11 的决策冲突（应当为 0）")
        return 1

    print("✅ 外键数量为 0，符合阿里开发手册（无外键、无级联）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

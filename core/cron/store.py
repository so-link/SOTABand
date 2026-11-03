"""定时任务与执行历史存储（SQLite）。

沿用 core/observability/task_history.py 的写法：标准库 sqlite3 落在
storage/platform.db，WAL + 全局锁 + 懒初始化连接。所有公共函数为同步且
极短（单条 INSERT/UPDATE/SELECT），在 async 路由中直接调用不构成阻塞。

表结构见 docs/cron-scheduler-design.md 第 4 节。cron_runs 按任务限量保留，
避免长跑场景把库撑大。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:  # 部分发行版（如 Debian 旧 libsqlite3）的系统 sqlite3 缺符号，退回自带库
    import sqlite3
    sqlite3.sqlite_version  # noqa: B018  触发 _sqlite3 C 扩展加载
except ImportError:
    import pysqlite3 as sqlite3  # type: ignore[no-redef]

from core.user_paths import platform_db

# 用户目录下 platform.db（每用户一库；默认 /root/SB-USER/0/platform.db）
_DB_PATH = platform_db()

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# 每个任务保留的执行历史条数上限
RUN_RETENTION = 200
# cron_runs.output_json 截断阈值（字符）。工具可能返回上百行表格，全存会撑爆库。
OUTPUT_MAX_CHARS = 20000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cron_jobs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    params_json   TEXT NOT NULL DEFAULT '{}',
    cron          TEXT NOT NULL,
    timezone      TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    enabled       INTEGER NOT NULL DEFAULT 1,
    owner         TEXT NOT NULL DEFAULT '',
    timeout       INTEGER NOT NULL DEFAULT 600,
    misfire       TEXT NOT NULL DEFAULT 'skip',
    next_run_at   REAL,
    last_run_at   REAL,
    last_status   TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cron_jobs_owner ON cron_jobs (owner, enabled);

CREATE TABLE IF NOT EXISTS cron_runs (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    started_at   REAL NOT NULL,
    finished_at  REAL,
    status       TEXT NOT NULL DEFAULT 'running',
    trigger      TEXT NOT NULL DEFAULT 'schedule',
    message      TEXT DEFAULT '',
    output_json  TEXT DEFAULT '',
    error        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cron_runs_job ON cron_runs (job_id, started_at DESC);
"""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def init() -> None:
    """建库建表（启动时显式调用，让失败暴露在启动阶段）。"""
    with _lock:
        _db()


def _job_row(d: sqlite3.Row) -> dict[str, Any]:
    j = dict(zip(d.keys(), d))
    try:
        j["params"] = json.loads(j.get("params_json") or "{}")
    except json.JSONDecodeError:
        j["params"] = {}
    j["enabled"] = bool(j.get("enabled"))
    j.pop("params_json", None)
    return j


def create(job: dict[str, Any]) -> dict[str, Any]:
    """新建任务。job 需含 name/kind/target_id/cron/owner，其余取默认。"""
    now = time.time()
    job_id = job.get("id") or f"cron_{int(now)}_{uuid.uuid4().hex[:6]}"
    with _lock:
        _db().execute(
            "INSERT INTO cron_jobs (id, name, kind, target_id, params_json, cron,"
            " timezone, enabled, owner, timeout, misfire, next_run_at, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                job["name"],
                job["kind"],
                job["target_id"],
                json.dumps(job.get("params") or {}, ensure_ascii=False),
                job["cron"],
                job.get("timezone") or "Asia/Shanghai",
                1 if job.get("enabled", True) else 0,
                job.get("owner") or "",
                int(job.get("timeout") or 600),
                job.get("misfire") or "skip",
                job.get("next_run_at"),
                now,
                now,
            ),
        )
        _db().commit()
    return get(job_id) or {}


def update(job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """按字段更新任务（只改传入的键）。"""
    cols, vals = [], []
    mapping = {
        "name": "name", "kind": "kind", "target_id": "target_id",
        "cron": "cron", "timezone": "timezone", "owner": "owner",
        "timeout": "timeout", "misfire": "misfire",
        "next_run_at": "next_run_at", "last_run_at": "last_run_at",
        "last_status": "last_status",
    }
    for key, col in mapping.items():
        if key in updates:
            cols.append(f"{col}=?")
            vals.append(updates[key])
    if "enabled" in updates:
        cols.append("enabled=?")
        vals.append(1 if updates["enabled"] else 0)
    if "params" in updates:
        cols.append("params_json=?")
        vals.append(json.dumps(updates["params"] or {}, ensure_ascii=False))
    if not cols:
        return get(job_id)
    cols.append("updated_at=?")
    vals += [time.time(), job_id]
    with _lock:
        _db().execute(
            f"UPDATE cron_jobs SET {', '.join(cols)} WHERE id=?", (*vals,)
        )
        _db().commit()
    return get(job_id)


def get(job_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _db().execute("SELECT * FROM cron_jobs WHERE id=?", (job_id,)).fetchone()
    return _job_row(row) if row else None


def list_jobs(owner: str | None = None, include_disabled: bool = True) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM cron_jobs", []
    conds, params = [], []
    if owner:
        conds.append("owner=?")
        params.append(owner)
    if not include_disabled:
        conds.append("enabled=1")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC"
    args = params
    with _lock:
        rows = _db().execute(sql, args).fetchall()
    return [_job_row(r) for r in rows]


def delete(job_id: str) -> bool:
    """删除任务定义（保留执行历史，便于事后追溯）。"""
    with _lock:
        cur = _db().execute("DELETE FROM cron_jobs WHERE id=?", (job_id,))
        _db().commit()
        return cur.rowcount > 0


# ── 执行历史 ──

def run_start(job_id: str, trigger: str = "schedule") -> str:
    run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}"
    with _lock:
        _db().execute(
            "INSERT INTO cron_runs (id, job_id, started_at, status, trigger)"
            " VALUES (?,?,?,?,?)",
            (run_id, job_id, time.time(), "running", trigger),
        )
        _db().commit()
    return run_id


def run_finish(run_id: str, status: str, message: str = "",
               output: Any = None, error: str = "") -> None:
    text = ""
    if output is not None:
        try:
            text = json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(output)
        if len(text) > OUTPUT_MAX_CHARS:
            text = text[:OUTPUT_MAX_CHARS] + "…（已截断）"
    with _lock:
        _db().execute(
            "UPDATE cron_runs SET status=?, finished_at=?, message=?,"
            " output_json=?, error=? WHERE id=?",
            (status, time.time(), (message or "")[:1000], text, (error or "")[:2000], run_id),
        )
        _db().commit()


def _prune_runs(job_id: str) -> None:
    """删除超出保留上限的旧执行记录。"""
    _db().execute(
        "DELETE FROM cron_runs WHERE job_id=? AND id NOT IN"
        " (SELECT id FROM cron_runs WHERE job_id=? ORDER BY started_at DESC LIMIT ?)",
        (job_id, job_id, RUN_RETENTION),
    )
    _db().commit()


def runs(job_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        rows = _db().execute(
            "SELECT * FROM cron_runs WHERE job_id=?"
            " ORDER BY started_at DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        _prune_runs(job_id)
    return [_run_row(r) for r in rows]


def recent_runs(limit: int = 30) -> list[dict[str, Any]]:
    with _lock:
        rows = _db().execute(
            "SELECT * FROM cron_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_run_row(r) for r in rows]


def _run_row(d: sqlite3.Row) -> dict[str, Any]:
    j = dict(zip(d.keys(), d))
    j["duration_ms"] = (
        int((j["finished_at"] - j["started_at"]) * 1000)
        if j.get("finished_at") and j.get("started_at") else None
    )
    return j

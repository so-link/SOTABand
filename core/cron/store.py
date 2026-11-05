"""定时任务与执行历史存储（SQLite，按用户分库）。

每用户一库（``<USER_HOME>/platform.db``）：任务与执行历史属于创建者的私有
数据，因此和对话/审计一样落在自己的库里。连接按 uid 缓存（core.user_db）。

**uid 是显式参数**：调度器是系统级循环，没有"当前请求身份"，必须按任务所属
用户显式读写，否则会写进别人的库。因此：
- 路由层调用传 uid=None（= 当前请求身份），天然只看见自己的任务；
- 调度器用 ``iter_jobs()`` 跨用户遍历，拿到 (uid, job) 后按 uid 回写。

表结构见 docs/cron-scheduler-design.md 第 4 节。cron_runs 按任务限量保留，
避免长跑场景把库撑大。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterator

from core.user_db import UserDbPool, sqlite3, uids as all_uids

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

_pool = UserDbPool(_SCHEMA)


def _db(uid: str | None = None) -> sqlite3.Connection:
    return _pool.get(uid)


def _lock(uid: str | None = None):
    return _pool.uid_lock(uid)


def init() -> None:
    """建库建表（启动时显式调用，让失败暴露在启动阶段）。

    遍历已有用户目录逐个建表——只建当前身份（通常是匿名 0）会让其它用户的
    库在首次被调度器读到时才建，失败暴露在调度循环里更难定位。
    """
    for uid in all_uids() or [None]:
        with _lock(uid):
            _db(uid)


def _job_row(d: sqlite3.Row) -> dict[str, Any]:
    j = dict(zip(d.keys(), d))
    try:
        j["params"] = json.loads(j.get("params_json") or "{}")
    except json.JSONDecodeError:
        j["params"] = {}
    j["enabled"] = bool(j.get("enabled"))
    j.pop("params_json", None)
    return j


def create(job: dict[str, Any], uid: str | None = None) -> dict[str, Any]:
    """新建任务。job 需含 name/kind/target_id/cron/owner，其余取默认。"""
    now = time.time()
    job_id = job.get("id") or f"cron_{int(now)}_{uuid.uuid4().hex[:6]}"
    with _lock(uid):
        _db(uid).execute(
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
        _db(uid).commit()
    return get(job_id, uid) or {}


def update(job_id: str, updates: dict[str, Any], uid: str | None = None) -> dict[str, Any] | None:
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
        return get(job_id, uid)
    cols.append("updated_at=?")
    vals += [time.time(), job_id]
    with _lock(uid):
        _db(uid).execute(
            f"UPDATE cron_jobs SET {', '.join(cols)} WHERE id=?", (*vals,)
        )
        _db(uid).commit()
    return get(job_id, uid)


def get(job_id: str, uid: str | None = None) -> dict[str, Any] | None:
    with _lock(uid):
        row = _db(uid).execute("SELECT * FROM cron_jobs WHERE id=?", (job_id,)).fetchone()
    return _job_row(row) if row else None


def list_jobs(owner: str | None = None, include_disabled: bool = True,
              uid: str | None = None) -> list[dict[str, Any]]:
    conds, params = [], []
    if owner:
        conds.append("owner=?")
        params.append(owner)
    if not include_disabled:
        conds.append("enabled=1")
    sql = "SELECT * FROM cron_jobs"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC"
    with _lock(uid):
        rows = _db(uid).execute(sql, params).fetchall()
    return [_job_row(r) for r in rows]


def iter_jobs(include_disabled: bool = True) -> Iterator[tuple[str, dict[str, Any]]]:
    """跨全部用户遍历任务，产出 ``(uid, job)``。

    系统级调度器专用——它没有请求身份，不能靠 current_uid()。
    """
    for uid in all_uids():
        for job in list_jobs(include_disabled=include_disabled, uid=uid):
            yield uid, job


def find_job(job_id: str) -> tuple[str, dict[str, Any]] | None:
    """跨用户按 id 查任务，返回 ``(uid, job)``。

    调度器的 ``upsert(job_id)`` 由路由层触发（那里知道是谁），但重启恢复、
    手动触发等入口只有 job_id，需要能定位到归属用户。
    """
    for uid in all_uids():
        job = get(job_id, uid)
        if job:
            return uid, job
    return None


def delete(job_id: str, uid: str | None = None) -> bool:
    """删除任务定义（保留执行历史，便于事后追溯）。"""
    with _lock(uid):
        cur = _db(uid).execute("DELETE FROM cron_jobs WHERE id=?", (job_id,))
        _db(uid).commit()
        return cur.rowcount > 0


def delete_all(uid: str) -> int:
    """清空该用户的全部任务定义（删号时调用）。保留执行历史与库文件本身。"""
    with _lock(uid):
        cur = _db(uid).execute("DELETE FROM cron_jobs")
        _db(uid).commit()
        return cur.rowcount


# ── 执行历史 ──

def run_start(job_id: str, trigger: str = "schedule", uid: str | None = None) -> str:
    run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}"
    with _lock(uid):
        _db(uid).execute(
            "INSERT INTO cron_runs (id, job_id, started_at, status, trigger)"
            " VALUES (?,?,?,?,?)",
            (run_id, job_id, time.time(), "running", trigger),
        )
        _db(uid).commit()
    return run_id


def run_finish(run_id: str, status: str, message: str = "", output: Any = None,
               error: str = "", uid: str | None = None) -> None:
    text = ""
    if output is not None:
        try:
            text = json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(output)
        if len(text) > OUTPUT_MAX_CHARS:
            text = text[:OUTPUT_MAX_CHARS] + "…（已截断）"
    with _lock(uid):
        _db(uid).execute(
            "UPDATE cron_runs SET status=?, finished_at=?, message=?,"
            " output_json=?, error=? WHERE id=?",
            (status, time.time(), (message or "")[:1000], text, (error or "")[:2000], run_id),
        )
        _db(uid).commit()


def _prune_runs(job_id: str, uid: str | None = None) -> None:
    """删除超出保留上限的旧执行记录。"""
    _db(uid).execute(
        "DELETE FROM cron_runs WHERE job_id=? AND id NOT IN"
        " (SELECT id FROM cron_runs WHERE job_id=? ORDER BY started_at DESC LIMIT ?)",
        (job_id, job_id, RUN_RETENTION),
    )
    _db(uid).commit()


def runs(job_id: str, limit: int = 50, uid: str | None = None) -> list[dict[str, Any]]:
    with _lock(uid):
        rows = _db(uid).execute(
            "SELECT * FROM cron_runs WHERE job_id=?"
            " ORDER BY started_at DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        _prune_runs(job_id, uid)
    return [_run_row(r) for r in rows]


def recent_runs(limit: int = 30, uid: str | None = None) -> list[dict[str, Any]]:
    with _lock(uid):
        rows = _db(uid).execute(
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

"""任务历史存储（SQLite）。

追加型记录（Agent 每次执行一条）不适合 JSON 全量读写，采用标准库
sqlite3 落在 storage/platform.db：INSERT 一行即一条历史，按时间索引查询。

并发模型：连接按线程创建（check_same_thread=False + 全局锁串行写入），
WAL 模式允许读写并发。所有公共函数为同步且极短（单条 INSERT/UPDATE），
在 async 路由中直接调用不构成阻塞。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

try:  # 部分发行版（如 Debian 旧 libsqlite3）的系统 sqlite3 缺符号，退回自带库
    import sqlite3
    sqlite3.sqlite_version  # noqa: B018  触发 _sqlite3 C 扩展加载
except ImportError:
    import pysqlite3 as sqlite3  # type: ignore[no-redef]

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "storage" / "platform.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    agent_name  TEXT DEFAULT '',
    session_id  TEXT DEFAULT '',
    input       TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'running',  -- running / success / failed
    started_at  REAL NOT NULL,
    finished_at REAL,
    duration_ms INTEGER,
    rounds      INTEGER DEFAULT 0,  -- 会话聚合记录的对话轮数
    error       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_task_history_started
    ON task_history (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_history_session
    ON task_history (session_id, agent_id);
"""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(_SCHEMA)
        # 旧库迁移：补 rounds 列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）
        cols = {r["name"] for r in _conn.execute(
            "PRAGMA table_info(task_history)").fetchall()}
        if "rounds" not in cols:
            _conn.execute("ALTER TABLE task_history ADD COLUMN rounds INTEGER DEFAULT 0")
        _conn.commit()
    return _conn


def start(agent_id: str, input_text: str, session_id: str = "",
          agent_name: str = "") -> int:
    """记录一次执行开始，返回 task_id（执行结束后用 finish 更新）。"""
    with _lock:
        cur = _db().execute(
            "INSERT INTO task_history (agent_id, agent_name, session_id, input,"
            " status, started_at) VALUES (?,?,?,?,?,?)",
            (agent_id, agent_name, session_id, (input_text or "")[:2000],
             "running", time.time()),
        )
        _db().commit()
        return int(cur.lastrowid)


def start_or_touch(agent_id: str, input_text: str, session_id: str = "",
                   agent_name: str = "") -> int:
    """按会话聚合的记录入口（对话场景专用）。

    同一 session 的多轮用户输入不再各记一条，而是合并为一条以会话主题
    （该会话首条输入）为标题的记录，返回其 task_id；后续用 accumulate
    累计轮数与耗时。session_id 为空时退化为 start。
    """
    if not session_id:
        return start(agent_id, input_text, session_id, agent_name)
    with _lock:
        row = _db().execute(
            "SELECT id FROM task_history WHERE session_id=? AND agent_id=?"
            " ORDER BY id DESC LIMIT 1", (session_id, agent_id)
        ).fetchone()
        if row:
            return int(row["id"])
        cur = _db().execute(
            "INSERT INTO task_history (agent_id, agent_name, session_id, input,"
            " status, started_at, rounds) VALUES (?,?,?,?,?,?,0)",
            (agent_id, agent_name, session_id, (input_text or "")[:200],
             "running", time.time()),
        )
        _db().commit()
        return int(cur.lastrowid)


def accumulate(task_id: int, duration_ms: int, status: str = "success",
               error: str = "") -> None:
    """会话聚合记录的轮次结束：轮数 +1、耗时累加、状态取最新一轮结果。"""
    with _lock:
        row = _db().execute(
            "SELECT id FROM task_history WHERE id=?", (task_id,)
        ).fetchone()
        if not row:
            return
        _db().execute(
            "UPDATE task_history SET status=?, finished_at=?,"
            " duration_ms=COALESCE(duration_ms,0)+?, error=?,"
            " rounds=COALESCE(rounds,0)+1 WHERE id=?",
            (status, time.time(), duration_ms, (error or "")[:500], task_id),
        )
        _db().commit()


def finish(task_id: int, status: str = "success", error: str = "") -> None:
    """记录执行结束（success/failed），写入耗时与错误摘要。"""
    with _lock:
        row = _db().execute(
            "SELECT started_at FROM task_history WHERE id=?", (task_id,)
        ).fetchone()
        if not row:
            return
        now = time.time()
        _db().execute(
            "UPDATE task_history SET status=?, finished_at=?, duration_ms=?,"
            " error=? WHERE id=?",
            (status, now, int((now - row[0]) * 1000), (error or "")[:500], task_id),
        )
        _db().commit()


def delete(task_id: int) -> bool:
    """删除一条任务历史记录，返回是否删到了。"""
    with _lock:
        cur = _db().execute("DELETE FROM task_history WHERE id=?", (task_id,))
        _db().commit()
        return cur.rowcount > 0


def recent(limit: int = 50, agent_id: str | None = None) -> list[dict[str, Any]]:
    """最近任务（时间倒序）。running 状态的任务也会返回。"""
    with _lock:
        if agent_id:
            rows = _db().execute(
                "SELECT * FROM task_history WHERE agent_id=?"
                " ORDER BY started_at DESC LIMIT ?", (agent_id, limit)
            ).fetchall()
        else:
            rows = _db().execute(
                "SELECT * FROM task_history ORDER BY started_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [dict(zip(r.keys(), r)) for r in rows]


def last_used_per_agent(limit: int = 20) -> list[dict[str, Any]]:
    """每个 Agent 的最近一次执行（用于「最近使用的 Agent」排序）。"""
    with _lock:
        rows = _db().execute(
            "SELECT agent_id, MAX(started_at) AS last_at, COUNT(*) AS use_count"
            " FROM task_history GROUP BY agent_id"
            " ORDER BY last_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(zip(r.keys(), r)) for r in rows]

"""对话持久化存储（SQLite）。

此前对话只存浏览器 localStorage，换浏览器/清缓存即丢失。改为后端落
storage/platform.db：chat_sessions 一会话一行，chat_messages 一条消息一行。

与 task_history 同库不同表，连接管理方式一致（单连接 + 全局锁 + WAL）。
"""

from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  TEXT PRIMARY KEY,
    agent_id    TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,             -- user / agent
    content    TEXT DEFAULT '',
    cards      TEXT DEFAULT '[]',         -- JSON 数组（内联卡片）
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, id);
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


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(zip(r.keys(), r)) for r in _db().execute(sql, args).fetchall()]


def append_message(session_id: str, agent_id: str, role: str,
                   content: str, cards: list[dict] | None = None) -> None:
    """追加一条消息；首条用户消息的文本同时作为会话标题。"""
    content = (content or "")[:20000]
    with _lock:
        conn = _db()
        now = time.time()
        conn.execute(
            "INSERT INTO chat_sessions (session_id, agent_id, title, created_at, updated_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET updated_at=?",
            (session_id, agent_id, "", now, now, now),
        )
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, cards, created_at)"
            " VALUES (?,?,?,?,?)",
            (session_id, role, content, json.dumps(cards or [], ensure_ascii=False), now),
        )
        if role == "user":
            conn.execute(
                "UPDATE chat_sessions SET title=? WHERE session_id=? AND title=''",
                (content[:60], session_id),
            )
        conn.commit()


def set_title(session_id: str, title: str) -> None:
    """写入会话标题（LLM 总结后调用；失败时保留首条消息截断的兜底标题）。"""
    with _lock:
        conn = _db()
        conn.execute("UPDATE chat_sessions SET title=? WHERE session_id=?",
                     ((title or "")[:80], session_id))
        conn.commit()


def message_count(session_id: str) -> int:
    """会话已有消息条数（用于判断是否只在首轮结束后总结标题）。"""
    with _lock:
        row = _db().execute(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return int(row["n"]) if row else 0


def get_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """会话消息（时间正序）。cards 字段解析回数组。"""
    with _lock:
        rows = _rows(
            "SELECT id, role, content, cards, created_at FROM chat_messages"
            " WHERE session_id=? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        )
    for r in rows:
        try:
            r["cards"] = json.loads(r.get("cards") or "[]")
        except Exception:
            r["cards"] = []
    return rows


def list_sessions(limit: int = 50, include_agent_spaces: bool = False) -> list[dict[str, Any]]:
    """会话列表（按最近更新倒序），带消息条数。

    默认只返回交互 Agent 的会话：agent_id 为空（历史数据）或交互 Agent
    的才要，Agent 空间落库的会话（agent_id=具体 Agent）不混入「对话历史」
    面板——它们有自己的详情页历史。
    """
    cond = "" if include_agent_spaces else (
        "WHERE s.agent_id = '' OR s.agent_id = ?"
    )
    args: tuple = (limit,) if include_agent_spaces else ("interactive-agent", limit)
    with _lock:
        return _rows(
            "SELECT s.session_id, s.agent_id, s.title, s.created_at, s.updated_at,"
            " COUNT(m.id) AS message_count"
            " FROM chat_sessions s LEFT JOIN chat_messages m ON m.session_id = s.session_id"
            f" {cond}"
            " GROUP BY s.session_id ORDER BY s.updated_at DESC LIMIT ?",
            args,
        )


def delete_session(session_id: str) -> bool:
    """删除整个会话及其消息，返回是否删到了。"""
    with _lock:
        conn = _db()
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        cur = conn.execute("DELETE FROM chat_sessions WHERE session_id=?", (session_id,))
        conn.commit()
        return cur.rowcount > 0

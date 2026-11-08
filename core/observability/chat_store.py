"""对话持久化存储（SQLite）。

此前对话只存浏览器 localStorage，换浏览器/清缓存即丢失。改为后端落
storage/platform.db：chat_sessions 一会话一行，chat_messages 一条消息一行。

与 task_history 同库不同表，连接管理方式一致（单连接 + 全局锁 + WAL）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from core.user_db import UserDbPool

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
    attachments TEXT DEFAULT '[]',        -- JSON 数组（附件/引用，历史回放用）
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, id);
"""

def _migrate(conn) -> None:
    """老库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加新列。

    attachments 是后加的（历史消息回放附件/引用），存量库没有这一列，
    不补的话写入会被静默丢弃（append_message 走了退化的老列分支）。
    """
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()]
    if "attachments" not in cols:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN attachments TEXT DEFAULT '[]'")


# 每用户一库（<USER_HOME>/platform.db）：连接按 uid 缓存，见 core.user_db
_pool = UserDbPool(_SCHEMA, migrate=_migrate)


def _db(uid: str | None = None):
    return _pool.get(uid)


def _lock(uid: str | None = None):
    return _pool.uid_lock(uid)


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(zip(r.keys(), r)) for r in _db().execute(sql, args).fetchall()]


def append_message(session_id: str, agent_id: str, role: str,
                   content: str, cards: list[dict] | None = None,
                   attachments: list[dict] | None = None) -> None:
    """追加一条消息；首条用户消息的文本同时作为会话标题。"""
    content = (content or "")[:20000]
    with _lock():
        conn = _db()
        now = time.time()
        conn.execute(
            "INSERT INTO chat_sessions (session_id, agent_id, title, created_at, updated_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET updated_at=?",
            (session_id, agent_id, "", now, now, now),
        )
        # attachments 列是后加的：老库无此列时退化为只写 cards
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()]
        if "attachments" in cols:
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, cards, attachments, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (session_id, role, content, json.dumps(cards or [], ensure_ascii=False),
                 json.dumps(attachments or [], ensure_ascii=False), now),
            )
        else:
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
    with _lock():
        conn = _db()
        conn.execute("UPDATE chat_sessions SET title=? WHERE session_id=?",
                     ((title or "")[:80], session_id))
        conn.commit()


def message_count(session_id: str) -> int:
    """会话已有消息条数（用于判断是否只在首轮结束后总结标题）。"""
    with _lock():
        row = _db().execute(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return int(row["n"]) if row else 0


def get_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """会话消息（时间正序）。cards / attachments 字段解析回数组。"""
    with _lock():
        # attachments 列是后加的：老库无此列时 SELECT 会报错，退化为不含该列
        cols = [r["name"] for r in _db().execute("PRAGMA table_info(chat_messages)").fetchall()]
        att_col = "attachments" if "attachments" in cols else "'' AS attachments"
        rows = _rows(
            f"SELECT id, role, content, cards, {att_col}, created_at FROM chat_messages"
            " WHERE session_id=? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        )
    for r in rows:
        try:
            r["cards"] = json.loads(r.get("cards") or "[]")
        except Exception:
            r["cards"] = []
        # attachments 列是后加的：老库无此列时留空
        raw_att = r.get("attachments")
        if raw_att is None:
            continue
        try:
            r["attachments"] = json.loads(raw_att or "[]")
        except Exception:
            r["attachments"] = []
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
    with _lock():
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
    with _lock():
        conn = _db()
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        cur = conn.execute("DELETE FROM chat_sessions WHERE session_id=?", (session_id,))
        conn.commit()
        return cur.rowcount > 0


def delete_sessions_by_agent(agent_id: str) -> int:
    """删除某 Agent 的全部会话及其消息，返回删除的会话数。

    删除 Agent 时调用：Agent 没了，它的会话在界面上已不可见，
    留在库里只会变成无法打开的孤儿数据。
    """
    with _lock():
        conn = _db()
        sids = [r[0] for r in conn.execute(
            "SELECT session_id FROM chat_sessions WHERE agent_id=?", (agent_id,)).fetchall()]
        for sid in sids:
            conn.execute("DELETE FROM chat_messages WHERE session_id=?", (sid,))
        cur = conn.execute("DELETE FROM chat_sessions WHERE agent_id=?", (agent_id,))
        conn.commit()
        return len(sids) if sids else cur.rowcount

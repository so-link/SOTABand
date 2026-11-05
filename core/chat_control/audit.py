"""对话操作审计（SQLite chat_ops 表）。

沿用 core/cron/store.py 的写法：标准库 sqlite3 落在 storage/platform.db，
WAL + 全局锁 + 懒初始化连接。所有公共函数为同步且极短，async 路由中直接调用。

审计是"对话能否被信任"的最后一环：结果卡片只认网关回执，而网关回执以本表
为准。因此**每一次平台调用（含只读）都必须落一行**，写操作再额外记确认/执行事件。

args_json / result_json 有截断阈值，避免大输出把库撑大。
"""

from __future__ import annotations

import json
import time
from typing import Any

from core.user_db import UserDbPool, sqlite3

ARGS_MAX_CHARS = 4000
RESULT_MAX_CHARS = 4000
# 审计流水保留条数（按会话无上限，全局按时间裁剪）
RETENTION = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_ops (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    op_id        TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    owner        TEXT NOT NULL DEFAULT '',
    tool         TEXT NOT NULL DEFAULT '',
    args_json    TEXT NOT NULL DEFAULT '{}',
    risk         TEXT NOT NULL DEFAULT 'read',
    status       TEXT NOT NULL DEFAULT '',
    result_json  TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER,
    created_at   REAL NOT NULL,
    finished_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_chat_ops_session ON chat_ops (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_ops_owner ON chat_ops (owner, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_ops_op ON chat_ops (op_id);
"""

# 每用户一库：连接按 uid 缓存，见 core.user_db
_pool = UserDbPool(_SCHEMA)


def _db(uid: str | None = None) -> sqlite3.Connection:
    return _pool.get(uid)


def _lock(uid: str | None = None):
    return _pool.uid_lock(uid)


def init() -> None:
    """建库建表（启动时显式调用，让失败暴露在启动阶段）。"""
    with _lock():
        _db()


def _dumps(obj: Any, limit: int) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def record(*, op_id: str, session_id: str, owner: str, tool: str,
           args: dict, risk: str, status: str, result: Any = None,
           error: str = "", duration_ms: int | None = None,
           finished: bool = True, uid: str | None = None) -> None:
    """写一条审计流水。审计失败不得影响业务，异常在此吞掉并打印。

    uid：落哪个用户的库。None=当前请求身份（网关路径）；交互 Agent 子进程
    写工作空间工具生命周期审计时显式传 owner，不依赖环境变量的隐式身份。
    """
    now = time.time()
    try:
        with _lock(uid):
            _db(uid).execute(
                "INSERT INTO chat_ops (op_id, session_id, owner, tool, args_json,"
                " risk, status, result_json, error, duration_ms, created_at, finished_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (op_id, session_id, owner, tool, _dumps(args or {}, ARGS_MAX_CHARS),
                 risk, status, _dumps(result, RESULT_MAX_CHARS) if result is not None else "",
                 (error or "")[:2000], duration_ms, now,
                 now if finished else None),
            )
            _db(uid).execute(
                "DELETE FROM chat_ops WHERE id NOT IN"
                " (SELECT id FROM chat_ops ORDER BY created_at DESC LIMIT ?)",
                (RETENTION,),
            )
            _db(uid).commit()
    except Exception as e:  # 审计不可用不能阻塞对话
        print(f"[chat_audit] 写入失败: {e}")


def _row(r: sqlite3.Row) -> dict[str, Any]:
    j = dict(zip(r.keys(), r))
    try:
        j["args"] = json.loads(j.get("args_json") or "{}")
    except json.JSONDecodeError:
        j["args"] = {}
    j.pop("args_json", None)
    return j


def recent(owner: str | None = None, session_id: str | None = None,
           limit: int = 50) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM chat_ops", []
    if owner:
        params.append(owner)
        sql += " WHERE owner=?"
    if session_id:
        sql += (" AND" if params else " WHERE") + " session_id=?"
        params.append(session_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(min(int(limit or 50), 200))
    with _lock():
        rows = _db().execute(sql, params).fetchall()
    return [_row(r) for r in rows]

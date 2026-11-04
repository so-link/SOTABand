"""按用户分库的 SQLite 连接池（platform.db 的唯一访问入口）

多用户系统（2026-11）落地要点：每个用户在 ``<USER_HOME_ROOT>/<uid>/platform.db``
有自己的库（会话 / 任务历史 / 审计 / 定时任务 / 个人偏好）。此前各存储模块在
**导入期**就把库路径算成 ``platform_db()``（当时恒为 uid=0），一旦多用户启用，
普通用户的写入会全落进超管 0 号库——隔离直接失效。本模块把「按当前身份选库」
收敛成一处实现。

并发模型沿用既有写法：单连接 ``check_same_thread=False`` + 每库一把可重入锁
串行写入 + WAL。所有公共函数都是短 SQL，async 路由里直接调用不构成阻塞。

- 当前身份的库：``get()``（内部走 ``core.user_paths.current_uid()``）
- 指定身份的库：``get(uid)``——给 cron 调度器这类**系统级跨用户**调用方用，
  它没有"当前请求身份"，必须显式指定，否则会写到别人的库里。
- 遍历全部用户：``uids()`` 扫描 USER_HOME_ROOT 下的目录名。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

try:  # 部分发行版（如 Debian 旧 libselinux/libsqlite3）系统 sqlite3 缺符号，退回自带库
    import sqlite3
    sqlite3.sqlite_version  # noqa: B018  触发 _sqlite3 C 扩展加载
except ImportError:  # pragma: no cover - 取决于宿主环境
    import pysqlite3 as sqlite3  # type: ignore[no-redef]

from core import user_paths
from core.user_paths import current_uid, sanitize_uid


def _db_path_for(uid: str | None = None) -> Path:
    """库路径解析。经模块属性转发，测试可 monkeypatch ``user_paths.platform_db``。"""
    return user_paths.platform_db(uid)

# 迁移钩子：CREATE TABLE IF NOT EXISTS 不会改已存在的表，加列靠这里补
Migrator = Callable[[sqlite3.Connection], None]


class UserDbPool:
    """一个「表族」对应一个池（如对话、定时任务），按 uid 缓存连接。"""

    def __init__(self, schema: str, migrate: Migrator | None = None) -> None:
        self._schema = schema
        self._migrate = migrate
        self._lock = threading.Lock()
        self._conns: dict[str, sqlite3.Connection] = {}
        self._locks: dict[str, threading.RLock] = {}

    def path(self, uid: str | None = None) -> Path:
        return _db_path_for(current_uid() if uid is None else sanitize_uid(uid))

    def get(self, uid: str | None = None) -> sqlite3.Connection:
        """取（必要时建）该用户的库连接。uid=None 表示当前请求身份。"""
        key = current_uid() if uid is None else sanitize_uid(uid)
        conn = self._conns.get(key)
        if conn is not None:
            return conn
        with self._lock:
            conn = self._conns.get(key)
            if conn is not None:
                return conn
            db_path = _db_path_for(key)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(self._schema)
            if self._migrate is not None:
                self._migrate(conn)
            conn.commit()
            self._conns[key] = conn
            return conn

    def uid_lock(self, uid: str | None = None) -> threading.RLock:
        """该用户库的写锁。按 uid 分锁，避免一个用户的慢查询卡住其他人。"""
        key = current_uid() if uid is None else sanitize_uid(uid)
        with self._lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def reset(self, uid: str | None = None) -> None:
        """丢弃连接（测试隔离、库文件被替换后强制重开）。uid=None 丢弃全部。"""
        with self._lock:
            keys = list(self._conns) if uid is None else [sanitize_uid(uid)]
            for key in keys:
                conn = self._conns.pop(key, None)
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001 - 关闭失败不影响重置语义
                        pass
                self._locks.pop(key, None)


def uids() -> list[str]:
    """已存在用户目录的全部 uid（含超管 0）。用于跨用户的系统级遍历。"""
    try:
        return sorted(
            p.name for p in user_paths.USER_HOME_ROOT.iterdir() if p.is_dir() and p.name
        )
    except OSError:
        return []

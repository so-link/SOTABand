"""用户文件登记表（file_id ↔ 用户目录内相对路径）

设计动机：文件接口此前按**绝对路径**收发——上传返回 ``filePath``，预览/
下载/删除再把路径原样传回。这有两个根本问题：

1. **越权**：``GET /api/file/download?path=/root/SB-USER/0/uploads/x.png``
   谁都能拼。黑名单式校验挡不住任意路径读取，根因是"路径即凭证"。
2. **路径即接口契约**：目录结构调整、用户搬家，所有存过路径的地方
   （工作区树 JSON、数据集清单、历史消息卡片）全部失效。

现在对外只有 ``file_id``：文件实体仍落在用户目录
（``<USER_HOME>/uploads/...``），但浏览器只见 id；服务端经
``core.user_paths.user_home()`` 把 id 解析成**当前用户目录下**的绝对路径，
跨用户读取在解析这一步就不可能。相对路径存在这里，用户目录整体可以
搬迁 / 换根。

表的存放沿用 ``user_prefs`` 范式：每用户一份，落在自己的 platform.db，
天然按用户隔离——file_id 即使被猜到，也只在属主自己的库里查得到。
"""

from __future__ import annotations

import mimetypes
import secrets
import time
from pathlib import Path

# 复用 user_db 已解析好的驱动（系统 sqlite3 可能缺符号被替换为 pysqlite3），
# 异常类必须同源，否则 IntegrityError 捕获会失效
from core.user_db import sqlite3
from core.user_db import UserDbPool
from core.user_paths import user_home

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_files (
    file_id    TEXT PRIMARY KEY,
    rel_path   TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    size       INTEGER NOT NULL DEFAULT 0,
    mime       TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'upload',
    created_at REAL NOT NULL
);
"""

_pool = UserDbPool(_SCHEMA)

# 文件种类：upload=用户上传, plot=绘图产物, tool=工具输出, other=其它受控文件
KINDS = {"upload", "plot", "tool", "other"}


def _lock():
    return _pool.uid_lock()


def _db():
    return _pool.get()


def _new_file_id() -> str:
    return f"f_{secrets.token_hex(6)}"


def to_rel(path: str | Path) -> str:
    """把 USER_HOME 内的路径规范化为相对路径；目录外一律拒绝。

    resolve() 之后再比，符号链接指向 USER_HOME 外也会被识破。
    """
    home = user_home().resolve()
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = home / p
    resolved = p.resolve()
    try:
        rel = resolved.relative_to(home)
    except ValueError:
        raise ValueError(f"路径不在用户目录内: {path}")
    rel = str(rel).replace("\\", "/")
    if not rel or rel == ".":
        raise ValueError("路径不能是用户目录本身")
    return rel


def _row_to_dict(r: sqlite3.Row) -> dict:
    return {
        "file_id": r["file_id"],
        "rel_path": r["rel_path"],
        "name": r["name"],
        "size": r["size"],
        "mime": r["mime"],
        "kind": r["kind"],
        "created_at": r["created_at"],
    }


def register(path: str | Path, kind: str = "upload") -> dict:
    """登记一个用户目录内的文件；同路径已登记则复用原 file_id（幂等）。

    幂等很重要：工具每次运行都返回同一个产物路径（如固定命名的图），
    重复登记若生成新 id，历史卡片就会指向一堆死 id。
    """
    rel = to_rel(path)
    abs_path = (user_home() / rel).resolve()
    if not abs_path.is_file():
        raise FileNotFoundError(rel)
    if kind not in KINDS:
        kind = "other"
    size = abs_path.stat().st_size
    mime = mimetypes.guess_type(str(abs_path))[0] or ""
    name = abs_path.name
    now = time.time()
    with _lock():
        db = _db()
        row = db.execute(
            "SELECT * FROM user_files WHERE rel_path=?", (rel,)
        ).fetchone()
        if row is not None:
            # 复用 id，但刷新可能变化的元数据（同名覆盖上传）
            db.execute(
                "UPDATE user_files SET name=?, size=?, mime=?, kind=? WHERE file_id=?",
                (name, size, mime, kind, row["file_id"]),
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM user_files WHERE file_id=?", (row["file_id"],)
            ).fetchone()
            return _row_to_dict(row)
        file_id = _new_file_id()
        try:
            db.execute(
                "INSERT INTO user_files (file_id, rel_path, name, size, mime, kind, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (file_id, rel, name, size, mime, kind, now),
            )
        except sqlite3.IntegrityError:
            # 并发登记同一路径：以先插入者为准
            db.commit()
            row = db.execute(
                "SELECT * FROM user_files WHERE rel_path=?", (rel,)
            ).fetchone()
            if row is None:
                raise
            return _row_to_dict(row)
        db.commit()
    return {
        "file_id": file_id, "rel_path": rel, "name": name, "size": size,
        "mime": mime, "kind": kind, "created_at": now,
    }


def get(file_id: str) -> dict | None:
    with _lock():
        r = _db().execute(
            "SELECT * FROM user_files WHERE file_id=?", (str(file_id),)
        ).fetchone()
    return _row_to_dict(r) if r else None


def resolve(file_id: str) -> Path:
    """file_id → 当前用户目录下的绝对路径。

    防御纵深：登记项是可信的，但表可能被旧数据/手工改动污染，解析结果
    仍必须落在 user_home() 内才放行。
    """
    rec = get(file_id)
    if rec is None:
        raise FileNotFoundError(f"文件不存在: {file_id}")
    try:
        checked = to_rel(user_home() / rec["rel_path"])  # 校验用，返回规范相对路径
    except ValueError:
        delete(rec["file_id"])
        raise FileNotFoundError(f"文件记录非法: {file_id}")
    abs_path = (user_home() / checked).resolve()
    if not abs_path.is_file():
        raise FileNotFoundError(f"文件已被删除: {rec['name']}")
    return abs_path


def list_files(kind: str | None = None) -> list[dict]:
    with _lock():
        if kind:
            rows = _db().execute(
                "SELECT * FROM user_files WHERE kind=? ORDER BY created_at DESC",
                (kind,),
            ).fetchall()
        else:
            rows = _db().execute(
                "SELECT * FROM user_files ORDER BY created_at DESC"
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete(file_id: str, purge: bool = True) -> bool:
    """注销登记。purge=True 时同时删除磁盘文件（工作区删除语义）。

    磁盘删除只在 resolve 校验通过（路径确在当前用户目录内）之后进行，
    防止登记数据被污染时删到目录外的文件。
    """
    rec = get(file_id)
    if rec is None:
        return False
    purged = False
    if purge:
        try:
            abs_path = (user_home() / to_rel(user_home() / rec["rel_path"])).resolve()
            if abs_path.is_file():
                abs_path.unlink()
                purged = True
        except (OSError, ValueError):
            pass
    with _lock():
        _db().execute("DELETE FROM user_files WHERE file_id=?", (rec["file_id"],))
        _db().commit()
    return purged

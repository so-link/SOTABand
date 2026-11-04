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
import re
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
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL DEFAULT 0   -- 覆盖上传/重登记时刷新
);
"""


def _migrate(conn) -> None:
    """老库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。"""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(user_files)").fetchall()]
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE user_files ADD COLUMN updated_at REAL NOT NULL DEFAULT 0")


_pool = UserDbPool(_SCHEMA, migrate=_migrate)

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
        "updated_at": r["updated_at"],
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
            # 复用 id，但刷新可能变化的元数据（同名覆盖上传）：
            # 大小/格式变了就更新 updated_at，让"最后修改时间"可用
            db.execute(
                "UPDATE user_files SET name=?, size=?, mime=?, kind=?, updated_at=?"
                " WHERE file_id=?",
                (name, size, mime, kind, now, row["file_id"]),
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM user_files WHERE file_id=?", (row["file_id"],)
            ).fetchone()
            return _row_to_dict(row)
        file_id = _new_file_id()
        try:
            db.execute(
                "INSERT INTO user_files"
                " (file_id, rel_path, name, size, mime, kind, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (file_id, rel, name, size, mime, kind, now, now),
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
        "mime": mime, "kind": kind, "created_at": now, "updated_at": now,
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


# ── 工具产物自动登记 ──

# 递归扫描结果对象的最大深度（工具返回是浅结构，超深即异常数据）
_SCAN_MAX_DEPTH = 4


def register_result_paths(obj, kind: str = "tool") -> list[dict]:
    """递归登记结果对象中引用的用户目录文件，就地注入 ``<字段名>_id``。

    工具（含 AI 生成的工具）的产物路径约定为 ``*_path`` 字段（image_path /
    file_path / output_path…），且必须写在 SOTABAND_USER_HOME 下（执行器注入
    的环境变量）。登记收在**平台出口**而不是逐个工具里改：

    - 存量与 AI 新生成的工具零改动——它们继续返回 ``*_path``，平台负责换成
      浏览器可用的 file_id；
    - 指向用户目录外（或不存在）的路径不登记也不报错：工具输出不可信，
      但前端只认注入的 ``<字段名>_id`` / ``<字段名>Id``，路径字段本身
      已无访问能力。

    返回登记成功的记录列表（供调用方放进 ``fileIds`` 聚合字段）。
    """

    def scan(node, depth: int) -> list[dict]:
        found: list[dict] = []
        if depth > _SCAN_MAX_DEPTH:
            return found
        if isinstance(node, dict):
            for key, val in list(node.items()):
                if isinstance(val, str) and key.endswith("_path"):
                    rec = _try_register(val, kind)
                    if rec is not None:
                        node[f"{key}_id"] = rec["file_id"]
                        # 驼峰别名：前端统一按 xxxId 读（image_path → imageId）
                        head = key[: -len("_path")]
                        camel = head.split("_")[0] + "".join(w.title() for w in head.split("_")[1:])
                        node[f"{camel}Id"] = rec["file_id"]
                        found.append(rec)
                elif isinstance(val, (dict, list)):
                    found.extend(scan(val, depth + 1))
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    found.extend(scan(item, depth + 1))
        return found

    return scan(obj, 0)


def _try_register(value: str, kind: str) -> dict | None:
    try:
        p = Path(value).expanduser()
        if not p.is_absolute() or not p.is_file():
            return None
        rel = to_rel(p)
        k = "plot" if rel.startswith("plots/") else kind
        return register(p, kind=k)
    except (ValueError, OSError):
        return None


# ── 工具参数中的 file_id 解析（与出口登记对称的入口转换） ──

_FILE_ID_RE = re.compile(r"^f_[0-9a-f]{12}$")


def looks_like_file_id(value: str) -> bool:
    return bool(_FILE_ID_RE.fullmatch(value or ""))


def resolve_params(params: dict) -> dict:
    """把参数值里的 file_id / 文件名换成工具可用的绝对路径（就地修改并返回）。

    前端界面只持有 file_id；但工具参数的语义就是路径（AI 生成的工具按
    路径写死逻辑）。平台在调用前做这一层替换：

    - 整串恰好是 file_id 形态（f_+12hex，高熵随机，业务字符串不可能撞上）
      且能在**当前用户**库中解析成功的值 → 替换为真实路径；
    - 对话里附加文件时只显示名字，参数里可能写的是**文件名**（含扩展名）——
      唯一匹配时同样解析；解析不到就原样传递（工具拿到无效值与拿到无效
      路径的失败面相同，不额外造错）。
    """
    if not isinstance(params, dict):
        return params
    for key, val in list(params.items()):
        if not isinstance(val, str) or not val:
            continue
        if looks_like_file_id(val):
            try:
                params[key] = str(resolve(val))
                continue
            except FileNotFoundError:
                pass
        resolved = _resolve_by_name(val)
        if resolved is not None:
            params[key] = str(resolved)
    return params


_NAME_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def _resolve_by_name(name: str) -> Path | None:
    """按文件名精确匹配当前用户登记文件；同名多个不解析（避免歧义）。

    只处理含扩展名的名字：普通字符串（"hello"、"10"）不该被拖进文件解析，
    否则用户输入恰好等于某个文件名时会被误伤。
    """
    if not _NAME_SUFFIX_RE.search(name):
        return None
    with _lock():
        rows = _db().execute(
            "SELECT file_id FROM user_files WHERE name=?", (name,)
        ).fetchall()
    if len(rows) != 1:
        return None
    try:
        return resolve(rows[0]["file_id"])
    except FileNotFoundError:
        return None

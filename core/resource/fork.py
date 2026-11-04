"""显式 fork：把内置资源复制为当前用户的私有副本（覆盖层语义）

2026-11-04 方案 C：内置资源（resources/，随 git 分发）默认只读使用；要改
就先「复制为我的副本」——把定义文件与实现目录整份复制到
USER_HOME/resources-private/，之后编辑都落在私有副本上。读取已是私有
优先（paths.resource_roots()），副本自动生效；「丢弃副本」删除私有文件
与私有 registry 条目即恢复内置原样。

forked_from 记录 fork 时内置内容的哈希：之后内置被 git 升级，比对哈希
即可提示「上游有更新」，由用户决定是否丢弃副本重新复制。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from core.resource import paths

# 复制/哈希时排除的目录：虚拟环境与缓存不属于资源内容
_EXCLUDE_DIRS = {".venv", "__pycache__", ".pytest_cache"}


def _definitions_dir(root: Path, space: str) -> Path:
    return root / space / "definitions"


def _impl_dir(root: Path, space: str, resource_id: str) -> Path:
    return root / space / "implementations" / resource_id


def iter_resource_files(root: Path, space: str, resource_id: str) -> list[Path]:
    """该资源在指定根下的全部内容文件（定义 <id>*.md + 实现目录），排序稳定。"""
    files: list[Path] = []
    ddir = _definitions_dir(root, space)
    if ddir.is_dir():
        files.extend(p for p in ddir.glob(f"{resource_id}*") if p.is_file())
    idir = _impl_dir(root, space, resource_id)
    if idir.is_dir():
        for p in sorted(idir.rglob("*")):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(idir).parts
            if any(part in _EXCLUDE_DIRS for part in rel_parts):
                continue
            files.append(p)
    return sorted(files)


def content_hash(root: Path, space: str, resource_id: str) -> str:
    """资源内容指纹：相对路径 + 文件字节。内置被 git 升级后哈希会变。"""
    h = hashlib.sha256()
    for f in iter_resource_files(root, space, resource_id):
        try:
            rel = str(f.relative_to(root))
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
        except OSError:
            continue
    return h.hexdigest()[:16]


def exists_in_builtin(space: str, resource_id: str) -> bool:
    return bool(iter_resource_files(paths.BUILTIN_ROOT, space, resource_id))


def exists_in_private(space: str, resource_id: str) -> bool:
    return bool(iter_resource_files(paths.private_root(), space, resource_id))


def fork_copy(space: str, resource_id: str) -> int:
    """把内置内容复制到私有根，返回复制的文件数。

    先清掉私有残留再复制：重复 fork（例如丢弃后又 fork）必须得到与当前
    内置一致的内容，否则上一次的修改会混进「新副本」。
    """
    src_files = iter_resource_files(paths.BUILTIN_ROOT, space, resource_id)
    if not src_files:
        raise FileNotFoundError(f"内置资源不存在: {space}/{resource_id}")
    drop_copy(space, resource_id)
    n = 0
    for src in src_files:
        rel = src.relative_to(paths.BUILTIN_ROOT)
        dst = paths.private_root() / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return n


def drop_copy(space: str, resource_id: str, root: str = "private") -> None:
    """删除指定根下该资源的全部文件（定义 + 实现目录）。

    ``root`` 可以是 "private"（默认，当前用户的私有根）或 "shared"
    （超管发布的共享根，删除后对所有用户生效）。
    """
    proot = paths.shared_root() if root == "shared" else paths.private_root()
    ddir = _definitions_dir(proot, space)
    if ddir.is_dir():
        for p in ddir.glob(f"{resource_id}*"):
            if p.is_file():
                p.unlink()
    idir = _impl_dir(proot, space, resource_id)
    if idir.is_dir():
        shutil.rmtree(idir)

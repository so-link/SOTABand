"""文件访问边界（D3 决策的实现，v2 评审后修订）

## 背景

引擎有「工作区间打开目录 / 预览文件 / 下载产物」这类功能，历史实现直接拿
请求里的 ``path`` 去读/删服务器本地文件。单机单人时没问题；**一台服务器多人
共用**时，任何登录用户都能读到别人的数据集文件、``.env``（LLM API Key）、
``storage/sotaband.db``（全库密码哈希/会话/审计），甚至删任意文件。

## 两层边界（缺一不可）

1. **路径边界**：规范化后必须在允许根内，且不命中敏感黑名单；
2. **归属边界**（v2 评审补）：数据集根 / 上传根按 **owner** 过滤 ——
   否则「路径在允许根内」就等于「所有人可读所有数据集文件」。平台元数据
   （``registry.json`` / ``workspace_*.json`` / ``data/exports`` 等）不进入
   任何用户可删的根。

调用方（路由）负责把 :class:`PathAccessError` 映射成 403。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from config.settings import settings
from core.resource.paths import PROJECT_ROOT, PRIVATE_ROOT
from core.runtime.paths import RUNTIME_ROOT

#: 上传根（每个用户一个子目录：``UPLOAD_ROOT/<owner>/...``）
UPLOAD_ROOT = RUNTIME_ROOT / "uploads"

#: 永远拒绝的路径片段（目录名 / 文件名，大小写不敏感）
_SENSITIVE_DIR_NAMES = {"storage", ".git", "venv", ".venv", "node_modules", "__pycache__"}
_SENSITIVE_FILE_NAMES = {
    ".env",
    # 平台元数据：删掉等于清空注册表 / 工作区状态，绝不暴露给文件接口
    "registry.json",
    "workspace_tools.json",
    "workspace_tree.json",
    "groups.json",
}


class PathAccessError(ValueError):
    """路径不在允许范围内（路由层映射为 403）。"""


def _dataset_entries() -> list[dict]:
    """已注册数据集条目（原始 JSON，不实例化 registry，避免触发写盘）。"""
    try:
        from core.runtime.paths import data_registry_path

        entries = json.loads(data_registry_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _dataset_entry_root(entry: dict) -> Path | None:
    raw = str(entry.get("data_path") or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _present_locally(root: Path) -> bool:
    try:
        return root.is_dir() and any(root.iterdir())
    except (OSError, PermissionError):
        return False


def _is_machine_owner(owner: str) -> bool:
    """8.1 之前的机器身份（local-xxxx）；升级时按"未认领"历史条目处理。"""
    return owner.startswith("local-")


def dataset_roots(owner_id: str | None, *, own_only: bool = False) -> list[Path]:
    """数据集目录。

    - ``owner_id`` 为空（服务端内部/迁移场景）→ 全部；
    - ``own_only=True`` → 只返回 owner 本人的目录；
    - 否则 → 本人 + 历史无主本机条目 + public 可见性（未来云上共享数据集）。
    """
    roots: list[Path] = []
    for entry in _dataset_entries():
        root = _dataset_entry_root(entry)
        if root is None:
            continue
        owner = str(entry.get("owner") or "")
        if owner_id is None:
            roots.append(root)
            continue
        if owner == owner_id:
            roots.append(root)
            continue
        if own_only:
            continue
        if (not owner or _is_machine_owner(owner)) and _present_locally(root):
            roots.append(root)  # 历史无主/机器身份但本机有数据：迁移启发式
            continue
        if str(entry.get("visibility") or "private") == "public":
            roots.append(root)
    return roots


def _extra_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in settings.file_access.allowed_roots:
        p = Path(raw).expanduser()
        try:
            roots.append(p.resolve())
        except OSError:
            continue
    return roots


def _dedup(roots: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for p in roots:
        key = os.path.normcase(str(p))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def upload_dir_for(owner_id: str) -> Path:
    """某用户专属上传目录（按 user_id 命名空间）。"""
    safe = str(owner_id or "anonymous").replace("/", "_").replace("\\", "_")
    return UPLOAD_ROOT / safe


def _resolve_or_self(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def _dataset_allowed_roots(owner_id: str | None = None) -> list[Path]:
    """「注册数据集」允许的根目录（比文件读取更窄，v3 评审后收紧）。

    只允许平台数据目录 + **当前用户自己的**上传目录 + 管理员显式配置的
    额外根。**不含**整个 ``UPLOAD_ROOT`` / ``RUNTIME_ROOT`` / ``PROJECT_ROOT``
    —— 否则任何登录用户都能把别人的上传目录、``runtime/tasks`` 等平台目录
    注册成数据集，再经 ``DELETE /api/data/{id}`` 触发 ``rmtree``。
    """
    roots: list[Path] = [
        PROJECT_ROOT / "data",
        PROJECT_ROOT / "output",
        PRIVATE_ROOT / "data",
        RUNTIME_ROOT / "datasets",
    ]
    if owner_id:
        roots.append(upload_dir_for(str(owner_id)))
    roots.extend(_extra_roots())
    return _dedup([_resolve_or_self(p) for p in roots])


def _protected_dataset_roots() -> list[Path]:
    """绝不能被"整个注册成数据集"的根目录（再删除就是灾难）。"""
    return [
        _resolve_or_self(PROJECT_ROOT),
        _resolve_or_self(RUNTIME_ROOT),
        _resolve_or_self(PRIVATE_ROOT),
        _resolve_or_self(UPLOAD_ROOT),
        # 平台数据根本身：只能登记其子目录（否则 delete 会清空整个 data/）
        _resolve_or_self(PROJECT_ROOT / "data"),
        _resolve_or_self(PROJECT_ROOT / "output"),
        _resolve_or_self(PRIVATE_ROOT / "data"),
        _resolve_or_self(RUNTIME_ROOT / "datasets"),
    ]


def allowed_read_roots(owner_id: str | None = None) -> list[Path]:
    """文件读取（下载/预览/图片/音频/浏览）允许的根目录。

    **不含整个 ``PRIVATE_ROOT``**（其中混有他人工具/模型产物）；
    上传目录只给当前用户自己的子目录；数据集目录按归属过滤。
    """
    roots = [PROJECT_ROOT, RUNTIME_ROOT]
    if owner_id:
        roots.append(upload_dir_for(owner_id))
        roots.extend(dataset_roots(owner_id))
    else:
        roots.extend(dataset_roots(None))
    roots.extend(_extra_roots())
    return _dedup(roots)


def allowed_write_roots(owner_id: str | None = None) -> list[Path]:
    """文件写入/删除允许的根目录（删除面比读取更窄）。

    只允许：自己的上传目录、自己拥有的数据集目录、项目输出目录、runtime。
    平台元数据（registry.json 等）由敏感黑名单兜底拒绝。
    """
    roots: list[Path] = [RUNTIME_ROOT, PROJECT_ROOT / "output"]
    if owner_id:
        roots.append(upload_dir_for(owner_id))
        roots.extend(dataset_roots(owner_id, own_only=True))
    roots.extend(_extra_roots())
    return _dedup(roots)


def is_sensitive_path(path: Path | str) -> bool:
    """公开判断：路径是否命中敏感黑名单（供目录列举类接口过滤条目）。"""
    try:
        return _is_sensitive(Path(path))
    except (OSError, TypeError):
        return True


def _is_sensitive(path: Path) -> bool:
    for part in path.parts:
        low = part.lower()
        if low in _SENSITIVE_DIR_NAMES:
            return True
    name = path.name.lower()
    if name in _SENSITIVE_FILE_NAMES:
        return True
    if name.startswith(".env"):
        return True
    parts_low = [p.lower() for p in path.parts]
    return "exports" in parts_low


def _within(path: Path, root: Path) -> bool:
    """path 是否在 root 之内（含 root 本身），两者均已 resolve。"""
    if path == root:
        return True
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_checked_path(
    raw: str,
    *,
    roots: list[Path] | None = None,
    must_exist: bool = False,
    owner_id: str | None = None,
    writable: bool = False,
) -> Path:
    """把用户传入的路径规范化并做边界校验，返回绝对路径。

    Raises:
        PathAccessError: 空路径 / 不在允许根内 / 命中敏感黑名单 / （可选）不存在
    """
    text = str(raw or "").strip()
    if not text or "\x00" in text:
        raise PathAccessError("路径为空或非法")

    try:
        path = Path(text).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathAccessError(f"路径无法解析: {text}") from exc

    if roots is not None:
        candidates = roots
    elif writable:
        candidates = allowed_write_roots(owner_id)
    else:
        candidates = allowed_read_roots(owner_id)
    if not any(_within(path, root) for root in candidates):
        raise PathAccessError("路径不在允许的目录范围内")

    if _is_sensitive(path):
        raise PathAccessError("路径涉及受保护的目录，已拒绝")

    # 上传目录的特殊归属规则：位于 UPLOAD_ROOT 下时必须是**自己的**子目录
    if owner_id is not None:
        upload_root = UPLOAD_ROOT.resolve()
        own_upload = upload_dir_for(owner_id).resolve()
        if _within(path, upload_root) and not _within(path, own_upload):
            raise PathAccessError("无权访问其他用户上传的文件")

    if must_exist and not path.exists():
        raise PathAccessError("路径不存在")
    return path


def _resolve_dataset_candidate(raw: str) -> Path:
    text = str(raw or "").strip()
    if not text or "\x00" in text:
        raise PathAccessError("数据集路径不能为空")
    try:
        return Path(text).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathAccessError(f"数据集路径无法解析: {text}") from exc


def _reject_cross_owner_upload(path: Path, owner_id: str | None) -> None:
    """上传目录只能登记 / 删除**自己**的子目录（防登记他人上传目录再删除）。"""
    upload_root = _resolve_or_self(UPLOAD_ROOT)
    if not _within(path, upload_root):
        return
    if not owner_id:
        # 无法判定归属时 fail-closed（路由层总会传入登录用户 UUID）
        raise PathAccessError("无法确认上传目录归属，已拒绝")
    own_upload = _resolve_or_self(upload_dir_for(str(owner_id)))
    if not _within(path, own_upload):
        raise PathAccessError("无权操作其他用户上传的目录")


def _check_dataset_overlap(
    path: Path, *, owner_id: str | None, deletion: bool
) -> None:
    """与已注册数据集重叠检查：数据集目录必须独立（删一个会连带删另一个）。

    - 注册（``deletion=False``）：任何重叠都拒绝（含自己的，避免同 id 复用目录）；
    - 删除（``deletion=True``）：允许与**自己的 / 无主历史**条目重叠，拒绝
      与其他用户的数据集重叠（否则可借删除他人的数据集目录）。
    """
    for entry in _dataset_entries():
        other = _dataset_entry_root(entry)
        if other is None:
            continue
        entry_owner = str(entry.get("owner") or "")
        if deletion and (not entry_owner or entry_owner == (owner_id or "")):
            continue
        if path == other:
            raise PathAccessError(
                "该目录与其他用户的数据集重叠"
                if deletion
                else "该目录已被注册为数据集"
            )
        if _within(path, other) or _within(other, path):
            raise PathAccessError("该目录与其他已注册数据集目录重叠")


def validate_dataset_path(raw: str, owner_id: str | None = None) -> Path:
    """校验「注册数据集」时提交的 data_path（v3 评审后收紧归属）。

    比普通文件读取更严格：
    - 必须是目录（或空由调用方处理）；
    - 必须在数据允许根内：``PROJECT_ROOT/data``、``PROJECT_ROOT/output``、
      ``PRIVATE_ROOT/data``、``RUNTIME_ROOT/datasets``、**当前用户自己的**
      上传子目录、``SOTABAND_FILE_ALLOWED_ROOTS``；
    - 不能是资源根本身（项目根 / runtime / 私有根 / 上传根 / 平台数据根）；
    - 不能是其他用户的上传目录；
    - 不允许与已注册的数据集目录重叠（父子关系都不行），否则删一个会连带
      删掉另一个。

    Args:
        raw: 客户端提交的路径
        owner_id: 当前用户 UUID（注册时必须传入；``None`` 时按内部/迁移场景
            处理，上传目录一律拒绝）
    """
    path = _resolve_dataset_candidate(raw)
    allowed = _dataset_allowed_roots(owner_id)
    if not any(_within(path, root) for root in allowed):
        raise PathAccessError(
            "数据集目录必须在 data/、output/、resources-private/data/、"
            "runtime/datasets/ 或本人上传目录下"
        )

    for forbidden in _protected_dataset_roots():
        if path == forbidden:
            raise PathAccessError("不能把资源根目录本身注册为数据集")

    _reject_cross_owner_upload(path, owner_id)

    if _is_sensitive(path):
        raise PathAccessError("路径涉及受保护的目录，已拒绝")

    if path.exists() and not path.is_dir():
        raise PathAccessError("数据集路径必须是目录")

    _check_dataset_overlap(path, owner_id=owner_id, deletion=False)
    return path


def validate_dataset_deletion(path: str | Path, owner_id: str | None = None) -> Path:
    """删除数据集文件前的安全校验（v3 评审新增，失败 → 路由层 409）。

    删除面比注册更窄：即使路径"看起来"在允许根里，也不允许删除
    - 任何资源根 / 平台数据根本身（``data/``、``output/``、上传根…）；
    - 敏感目录（``storage``、``.env``、平台 registry 等）；
    - 其他用户的上传目录；
    - 与其他用户已注册数据集重叠的目录。

    历史数据集可能登记在允许根之外（如旧机器的 ``/Users/leo/...``）：这类
    条目同样拒绝删除文件，但注册表条目仍可由调用方决定是否移除。
    """
    resolved = _resolve_dataset_candidate(str(path))
    allowed = _dataset_allowed_roots(owner_id)
    if not any(_within(resolved, root) for root in allowed):
        raise PathAccessError("数据集目录不在允许删除的范围内")

    if resolved in _protected_dataset_roots() or any(
        resolved == root for root in allowed
    ):
        raise PathAccessError("拒绝删除资源根目录本身")

    _reject_cross_owner_upload(resolved, owner_id)

    if _is_sensitive(resolved):
        raise PathAccessError("路径涉及受保护的目录，已拒绝")

    _check_dataset_overlap(resolved, owner_id=owner_id, deletion=True)
    return resolved


__all__ = [
    "PathAccessError",
    "UPLOAD_ROOT",
    "allowed_read_roots",
    "allowed_write_roots",
    "dataset_roots",
    "is_sensitive_path",
    "resolve_checked_path",
    "upload_dir_for",
    "validate_dataset_deletion",
    "validate_dataset_path",
]

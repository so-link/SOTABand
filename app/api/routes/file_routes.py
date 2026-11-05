"""文件管理路由 — 上传、存储、预览、工作区文件树

## 安全边界（2026-11-04 起的硬性约束）

引擎有「工作区间打开目录 / 预览 / 下载 / 删除」功能。单机单人时任意路径都能
接受；**一台服务器多人共用**时这等于任何登录用户可读别人的数据集、``.env``
（LLM API Key）、``storage/sotaband.db``（密码哈希/会话/审计），并可删任意文件。

因此本模块的路径参数一律经 :mod:`core.file_guard` 校验：

- 读：:func:`core.file_guard.resolve_checked_path`（允许根 + 敏感黑名单）；
- 写/删：:func:`core.file_guard.resolve_checked_path` + writable 根；
- 未登录：路由级认证依赖直接 401（``app/main.py`` 的 include_router ``_auth``）。

**工作区文件树按用户隔离**：状态源为 ``storage/workspace_tree/{user_id}.json``。
历史全局文件 ``storage/workspace_tree.json`` 只作为只读迁移来源（首个读取它的
用户看到旧状态，但之后写入落自己的命名空间），避免多用户互相覆盖。
"""

import json as _json
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_auth_context
from config.settings import settings
from core.db.session import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession
from core.auth.context import AuthContext
from core.file_guard import (
    PathAccessError,
    UPLOAD_ROOT,
    allowed_read_roots,
    allowed_write_roots,
    is_sensitive_path,
    resolve_checked_path,
    upload_dir_for,
)

router = APIRouter()

# 上传文件存储根目录（直接复用 core.file_guard.UPLOAD_ROOT，保持一致）
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

# ── 工作区间文件树 —— 后端按用户持久化 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_STORAGE_DIR = _PROJECT_ROOT / "storage"
_LEGACY_TREE_FILE = _STORAGE_DIR / "workspace_tree.json"


class WorkspaceTreePayload(BaseModel):
    root: dict[str, Any]


def _tree_file(user_id: str) -> Path:
    safe = str(user_id or "anonymous").replace("/", "_").replace("\\", "_")
    return _STORAGE_DIR / "workspace_tree" / f"{safe}.json"


def _read_workspace_tree(user_id: str) -> dict | None:
    path = _tree_file(user_id)
    try:
        if path.exists():
            data = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "root" in data:
                return data["root"]
        # 兼容迁移：旧全局文件仅作为只读来源（不复制到每个用户，避免状态放大）
        if _LEGACY_TREE_FILE.exists():
            data = _json.loads(_LEGACY_TREE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "root" in data:
                return data["root"]
    except Exception:
        pass
    return None


def _write_workspace_tree(user_id: str, root: dict) -> None:
    path = _tree_file(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _json.dumps({"root": root}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _safe_upload_name(raw: str) -> str:
    """取纯文件名，防路径穿越（CWE-22）。"""
    name = Path(str(raw or "")).name
    if not name or name in (".", ".."):
        raise HTTPException(400, "文件名非法")
    return name


def _guard(
    raw: str,
    *,
    owner_id: str,
    writable: bool = False,
    must_exist: bool = False,
) -> Path:
    """路径 + 归属边界校验；失败统一 403（不泄露服务器目录结构）。"""
    try:
        roots = allowed_write_roots(owner_id) if writable else None
        return resolve_checked_path(
            raw, roots=roots, must_exist=must_exist, owner_id=owner_id
        )
    except PathAccessError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/workspace/tree")
async def get_workspace_tree(ctx: AuthContext = Depends(get_auth_context)):
    """返回当前用户后端保存的工作区间文件树（无则返回 null）。"""
    return {"root": _read_workspace_tree(ctx.user_id)}


@router.put("/workspace/tree")
async def save_workspace_tree(
    payload: WorkspaceTreePayload, ctx: AuthContext = Depends(get_auth_context)
):
    """整体覆盖保存**当前用户**的工作区间文件树。"""
    _write_workspace_tree(ctx.user_id, payload.root)
    return {"ok": True}


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    subdir: str = Form(""),
    ctx: AuthContext = Depends(get_auth_context),
):
    """上传文件，保存到 runtime 上传目录（大小受限、文件名净化）。"""
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    safe_name = _safe_upload_name(file.filename)
    # 子目录：优先用传入的 subdir，否则按日期。必须是单层安全名
    dir_name = subdir if subdir else time.strftime("%Y-%m-%d")
    if Path(dir_name).name != dir_name or dir_name in {".", ".."}:
        raise HTTPException(400, "上传子目录无效")
    # 每个用户一个命名空间，避免互相看到/删除对方的上传附件
    dest_dir = upload_dir_for(ctx.user_id) / dir_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 处理重名
    dest_path = dest_dir / safe_name
    if dest_path.exists():
        stem, ext = os.path.splitext(safe_name)
        dest_path = dest_dir / f"{stem}_{int(time.time())}{ext}"

    max_bytes = max(1, settings.file_access.upload_max_mb) * 1024 * 1024
    written = 0
    with open(dest_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                out.close()
                try:
                    dest_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise HTTPException(
                    413,
                    f"文件超过上限（{settings.file_access.upload_max_mb} MB）",
                )
            out.write(chunk)

    fmt = dest_path.suffix.lstrip(".").lower()
    return {
        "id": f"file-{uuid.uuid4().hex}",
        "fileName": dest_path.name,
        "filePath": str(dest_path),
        "fileSize": written,
        "format": fmt,
        "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.post("/delete")
async def delete_file(
    req: dict,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
):
    """删除指定路径的文件（仅允许 writable 根内的文件，不允许删目录/源码）。"""
    path = str(req.get("path", "") or "")
    if not path:
        raise HTTPException(400, "缺少文件路径")

    file_path = _guard(path, owner_id=ctx.user_id, writable=True, must_exist=True)
    if file_path.is_dir():
        raise HTTPException(400, "请使用目录删除接口删除目录")

    try:
        file_path.unlink()
        from core.auth.audit_actions import ACTION_FILE_DELETED, record_resource_change

        await record_resource_change(
            session, ctx,
            action=ACTION_FILE_DELETED, target_type="file", target_id=str(file_path),
        )
        return {"status": "ok", "message": "文件已删除"}
    except PermissionError:
        raise HTTPException(403, "无权限删除该文件")
    except Exception as e:  # noqa: BLE001 - 磁盘错误按 500 返回
        raise HTTPException(500, f"删除失败: {e}")


@router.get("/image")
async def serve_image(
    path: str, ctx: AuthContext = Depends(get_auth_context)
):
    """提供图片文件（用于界面渲染）"""
    from fastapi.responses import FileResponse

    img_path = _guard(path, owner_id=ctx.user_id, must_exist=True)
    mime_type, _ = mimetypes.guess_type(str(img_path))
    return FileResponse(img_path, media_type=mime_type or "image/png")


@router.get("/download")
async def download_file(
    path: str, ctx: AuthContext = Depends(get_auth_context)
):
    """下载/预览文件（仅允许根内路径）"""
    from fastapi.responses import FileResponse as _FileResponse

    file_path = _guard(path, owner_id=ctx.user_id, must_exist=True)
    mime_type, _ = mimetypes.guess_type(str(file_path))

    # 检测实际文件格式（某些工具生成 AIFF-C 但用 .wav 扩展名）
    if mime_type == "audio/wav":
        try:
            with open(file_path, "rb") as _f:
                header = _f.read(12)
                if header[8:12] == b"AIFC" or header[8:12] == b"AIFF":
                    mime_type = "audio/aiff"
        except Exception:
            pass

    return _FileResponse(
        file_path,
        media_type=mime_type or "application/octet-stream",
        filename=file_path.name,
        content_disposition_type="attachment" if file_path.suffix.lower() == ".xlsx" else "inline",
    )


@router.get("/audio")
async def serve_audio(
    path: str, ctx: AuthContext = Depends(get_auth_context)
):
    """提供音频文件（专门用于 <audio> 标签播放，支持 Range 请求）"""
    from fastapi.responses import FileResponse as _FileResponse

    file_path = _guard(path, owner_id=ctx.user_id, must_exist=True)
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return _FileResponse(
        file_path,
        media_type=mime_type or "audio/wav",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/list")
async def list_files(ctx: AuthContext = Depends(get_auth_context)):
    """列出**当前用户**已上传的文件（按 user_id 命名空间隔离）"""
    files = []
    root = upload_dir_for(ctx.user_id)
    if root.exists():
        for f in root.rglob("*"):
            if f.is_file():
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "format": f.suffix.lstrip("."),
                    "size": f.stat().st_size,
                })
    return {"files": files, "count": len(files)}


@router.get("/scan-directory")
async def scan_directory(path: str, ctx: AuthContext = Depends(get_auth_context)):
    """扫描目录，返回目录树结构（仅允许根内、且按归属校验的目录）。"""
    dir_path = _guard(path, owner_id=ctx.user_id, must_exist=True)
    if not dir_path.is_dir():
        raise HTTPException(400, f"路径不是目录: {dir_path}")

    def build_node(p: Path, depth: int = 0) -> dict | None:
        if depth > 6:
            return None
        if p.is_dir():
            children = []
            try:
                entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except PermissionError:
                entries = []
            for child in entries:
                # 不允许经"扫根目录"旁路列出 storage/.env/.git 等敏感条目
                if is_sensitive_path(child):
                    continue
                node = build_node(child, depth + 1)
                if node:
                    children.append(node)
                if len(children) >= 500:
                    break
            return {
                "id": str(p),
                "name": p.name or str(p),
                "type": "directory",
                "category": "folder",
                "path": str(p),
                "expanded": depth < 2,
                "children": children,
            }
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return {
            "id": str(p),
            "name": p.name,
            "type": "file",
            "category": "unknown",
            "path": str(p),
            "format": p.suffix.lstrip(".").lower() or None,
            "size": size,
        }

    root_node = build_node(dir_path)
    if root_node is None:
        raise HTTPException(500, "无法构建目录树")
    root_node["expanded"] = True
    return {"root": root_node}


@router.get("/browse-directory")
async def browse_directory(
    path: str = "", ctx: AuthContext = Depends(get_auth_context)
):
    """列出指定目录下的子目录和文件（逐层浏览；仅允许根内 + 归属校验）。"""
    if not path:
        # 历史行为返回磁盘根；多用户下这等于暴露整盘，改为默认项目根
        current = Path(_PROJECT_ROOT)
    else:
        current = _guard(path, owner_id=ctx.user_id, must_exist=True)

    if not current.exists():
        raise HTTPException(404, f"目录不存在: {current}")
    if not current.is_dir():
        raise HTTPException(400, f"路径不是目录: {current}")

    try:
        entries = sorted(current.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        raise HTTPException(403, f"无权限访问目录: {current}")

    subdirs = []
    files = []
    for e in entries:
        if is_sensitive_path(e):
            continue
        try:
            if e.is_dir():
                subdirs.append({"name": e.name, "path": str(e)})
            elif e.is_file():
                files.append({
                    "name": e.name,
                    "path": str(e),
                    "format": e.suffix.lstrip(".").lower() or None,
                    "size": e.stat().st_size,
                })
        except (PermissionError, OSError):
            continue

    # 父目录仅在仍处于允许根内时才给出，避免借 parent 跳出边界
    parent: str | None = None
    try:
        resolved_parent = current.parent.resolve()
        if any(
            resolved_parent == root or root in resolved_parent.parents
            for root in allowed_read_roots(ctx.user_id)
        ):
            parent = str(resolved_parent)
    except OSError:
        parent = None

    _ = allowed_read_roots  # 保持导入：父目录边界判断使用上方 allowed_read_roots()
    return {
        "current": str(current),
        "parent": parent,
        "subdirs": subdirs,
        "files": files,
    }

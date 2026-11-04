"""文件管理路由 — 上传、存储"""

import json as _json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

router = APIRouter()

# 上传子目录名：只允许单层字母/数字/._-（如 2026-10-31、dataset_1759...），
# 拒绝 "/" 与 ".."——否则 subdir 可把上传文件写到任意绝对路径。
_UPLOAD_SUBDIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 经文件接口一律不可读/删的敏感位置（密钥与身份数据）。
# 这是防御纵深：根因仍需「接口鉴权 + 执行隔离」，不能只靠名单。
_SENSITIVE_NAME_PREFIXES = (".env",)
_SENSITIVE_NAMES = {"secret.key", "id_rsa", "id_ed25519", "authorized_keys", "credentials", "credentials.json"}


def _sensitive_dirs() -> set[Path]:
    from core.user_paths import auth_dir

    # 密钥/身份文件是系统级（storage/auth）；历史版本曾在私有根 users/，一并拦截
    from core.resource import paths as _paths

    return {
        auth_dir().resolve(),
        (_paths.private_root() / "users").resolve(),
        (Path.home() / ".ssh").resolve(),
    }


def _assert_path_allowed(path: Path) -> None:
    """拒绝读取/删除密钥与身份文件，并禁止穿越到敏感目录。"""
    resolved = path.resolve()
    if resolved.name.lower() in _SENSITIVE_NAMES or resolved.name.lower().startswith(_SENSITIVE_NAME_PREFIXES):
        raise HTTPException(403, "不允许访问该路径")
    for blocked in _sensitive_dirs():
        try:
            resolved.relative_to(blocked)
        except ValueError:
            continue
        raise HTTPException(403, "不允许访问该路径")

# 上传文件存储根目录（用户目录：USER_HOME/uploads，每用户隔离）
from core.user_paths import user_home as _user_home


def _upload_root() -> Path:
    """当前用户的上传根（每请求动态解析：导入期求值会把所有人固定到同一个用户）。"""
    root = _user_home() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── 工作区间文件树 —— 后端持久化 ──
# 「工作区间」里用户上传 / 导入 / 打开的文件与目录属于引擎状态，而非浏览器状态。
# 以前整棵树只存在前端 localStorage，换浏览器 / 无痕窗口 / 清缓存后工作区间就全空了，
# 且后端完全无从知晓用户的工作现场。现在状态源是 USER_HOME/workspace_tree.json，
# 前端 localStorage 仅作降级缓存（与工具空间 workspace_tools.json 同一范式）。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _workspace_tree_file() -> Path:
    return _user_home() / "workspace_tree.json"


class WorkspaceTreePayload(BaseModel):
    root: dict[str, Any]


def _read_workspace_tree() -> dict | None:
    tree_file = _workspace_tree_file()
    try:
        if tree_file.exists():
            data = _json.loads(tree_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "root" in data:
                return data["root"]
    except Exception:
        pass
    return None


def _write_workspace_tree(root: dict) -> None:
    tree_file = _workspace_tree_file()
    tree_file.parent.mkdir(parents=True, exist_ok=True)
    tree_file.write_text(
        _json.dumps({"root": root}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_CATEGORY_BY_EXT = {
    "image": {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff"},
    "table": {"csv", "tsv", "xlsx", "xls", "parquet"},
    "text": {"txt", "md", "json", "yaml", "yml", "log", "py", "js", "ts"},
    "eeg": {"edf", "bdf", "set", "fif"},
    "model": {"pt", "pth", "ckpt", "onnx", "h5", "safetensors"},
    "archive": {"zip", "tar", "gz", "bz2", "7z"},
}


def _category_of(ext: str) -> str:
    for cat, exts in _CATEGORY_BY_EXT.items():
        if ext in exts:
            return cat
    return "unknown"


def _build_node(path: Path) -> dict:
    if path.is_dir():
        children = [_build_node(c) for c in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))]
        return {
            "id": f"dir-{path}", "name": path.name, "type": "directory", "category": "folder",
            "path": str(path), "expanded": False, "children": children,
        }
    ext = path.suffix.lstrip(".").lower()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "id": f"file-{path}", "name": path.name, "type": "file", "category": _category_of(ext),
        "path": str(path), "format": ext, "size": size,
    }


def _scan_upload_root() -> dict | None:
    """工作区间树的兜底：存储文件丢失时，从磁盘上的 uploads 目录重建（真实数据，非 mock）。"""
    root_dir = _upload_root()
    children = [_build_node(c) for c in sorted(root_dir.iterdir(), key=lambda p: (p.is_file(), p.name))]
    if not children:
        return None
    return {
        "id": "workspace-root", "name": "工作区间", "type": "directory", "category": "folder",
        "path": "", "expanded": True, "children": children,
    }


@router.get("/workspace/tree")
async def get_workspace_tree():
    """返回后端保存的工作区间文件树。

    存储文件缺失时用 data/uploads 的磁盘内容兜底重建，避免升级或清缓存后工作区凭空消失。
    """
    root = _read_workspace_tree()
    if not root:
        root = _scan_upload_root()
        if root:
            _write_workspace_tree(root)
    return {"root": root}


@router.put("/workspace/tree")
async def save_workspace_tree(payload: WorkspaceTreePayload):
    """整体覆盖保存工作区间文件树（上传 / 导入 / 打开目录 / 删除 / 展开收起后调用）。"""
    _write_workspace_tree(payload.root)
    return {"ok": True}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), subdir: str = Form("")):
    """上传文件，保存到 data/uploads/ 目录。

    Args:
        subdir: 可选子目录（如时间戳）。不传则按日期分目录。
    """
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    # 安全文件名
    safe_name = Path(file.filename).name
    # 子目录：优先用传入的 subdir，否则按日期；只允许安全单层目录名
    dir_name = subdir.strip() if subdir else time.strftime("%Y-%m-%d")
    if not _UPLOAD_SUBDIR_RE.fullmatch(dir_name):
        raise HTTPException(400, "子目录名非法：只允许字母、数字、点、下划线、连字符")
    dest_dir = _upload_root() / dir_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 处理重名
    dest_path = dest_dir / safe_name
    if dest_path.exists():
        stem, ext = os.path.splitext(safe_name)
        dest_path = dest_dir / f"{stem}_{int(time.time())}{ext}"

    # 保存文件
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    size = dest_path.stat().st_size
    fmt = dest_path.suffix.lstrip(".").lower()

    return {
        "id": f"file-{int(time.time())}",
        "fileName": dest_path.name,
        "filePath": str(dest_path),
        "fileSize": size,
        "format": fmt,
        "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.post("/delete")
async def delete_file(req: dict):
    """删除指定路径的文件（工作区文件删除）"""
    path = req.get("path", "")
    if not path:
        raise HTTPException(400, "缺少文件路径")

    file_path = Path(path).expanduser().resolve()
    _assert_path_allowed(file_path)
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")

    # 安全检查：只允许删除「文件」，不允许删除目录或系统关键路径
    if file_path.is_dir():
        raise HTTPException(400, "请使用目录删除接口删除目录")
    home = Path.home().resolve()
    root = Path(file_path.anchor)
    if file_path == home or file_path == root:
        raise HTTPException(403, "不允许删除系统关键路径")

    try:
        file_path.unlink()
        return {"status": "ok", "message": "文件已删除"}
    except PermissionError:
        raise HTTPException(403, "无权限删除该文件")
    except Exception as e:
        raise HTTPException(500, f"删除失败: {str(e)}")


@router.get("/image")
async def serve_image(path: str):
    """提供图片文件（用于界面渲染）"""
    from pathlib import Path as _Path
    from fastapi.responses import FileResponse
    import mimetypes
    img_path = _Path(path)
    _assert_path_allowed(img_path)
    if not img_path.exists():
        raise HTTPException(404, "图片不存在")
    mime_type, _ = mimetypes.guess_type(str(img_path))
    return FileResponse(img_path, media_type=mime_type or "image/png")


@router.get("/download")
async def download_file(path: str):
    """下载/预览任意文件"""
    from pathlib import Path as _Path
    from fastapi.responses import FileResponse as _FileResponse
    import mimetypes
    file_path = _Path(path)
    _assert_path_allowed(file_path)
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")
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
        content_disposition_type="inline",
    )


@router.get("/audio")
async def serve_audio(path: str):
    """提供音频文件（专门用于 <audio> 标签播放，支持 Range 请求）"""
    from pathlib import Path as _Path
    from fastapi.responses import FileResponse as _FileResponse
    import mimetypes
    file_path = _Path(path)
    _assert_path_allowed(file_path)
    if not file_path.exists():
        raise HTTPException(404, "音频文件不存在")
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return _FileResponse(
        file_path,
        media_type=mime_type or "audio/wav",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/list")
async def list_files():
    """列出所有已上传文件"""
    files = []
    upload_root = _upload_root()
    for f in upload_root.rglob("*"):
        if f.is_file():
            files.append({
                "name": f.name,
                "path": str(f),
                "format": f.suffix.lstrip("."),
                "size": f.stat().st_size,
            })
    return {"files": files, "count": len(files)}


@router.get("/scan-directory")
async def scan_directory(path: str):
    """扫描本地目录，返回目录树结构（用于工作区「打开目录」）。

    Args:
        path: 本地目录的绝对路径。
    """
    dir_path = Path(path).expanduser().resolve()
    if not dir_path.exists():
        raise HTTPException(404, f"目录不存在: {dir_path}")
    if not dir_path.is_dir():
        raise HTTPException(400, f"路径不是目录: {dir_path}")

    def build_node(p: Path, depth: int = 0) -> dict:
        """递归构建目录树节点，限制深度避免过深目录导致性能问题"""
        if depth > 6:
            return None
        if p.is_dir():
            children = []
            try:
                entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except PermissionError:
                entries = []
            for child in entries:
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
        # 文件
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
    # 根节点名称用目录名，展开
    root_node["expanded"] = True
    return {"root": root_node}


@router.get("/browse-directory")
async def browse_directory(path: str):
    """列出指定目录下的子目录和文件（用于目录浏览器逐层浏览）。

    Args:
        path: 目录绝对路径。为空时返回根目录（/）。
    """
    import platform

    if not path:
        if platform.system() == "Windows":
            current = Path("C:\\")
        else:
            current = Path("/")
    else:
        current = Path(path).expanduser()

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

    parent = str(current.parent) if current.parent != current else None

    return {
        "current": str(current),
        "parent": parent,
        "subdirs": subdirs,
        "files": files,
    }

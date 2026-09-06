"""文件管理路由 — 上传、存储"""

import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

router = APIRouter()

# 上传文件存储根目录
UPLOAD_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "data" / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


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
    # 子目录：优先用传入的 subdir，否则按日期
    dir_name = subdir if subdir else time.strftime("%Y-%m-%d")
    dest_dir = UPLOAD_ROOT / dir_name
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
    if UPLOAD_ROOT.exists():
        for f in UPLOAD_ROOT.rglob("*"):
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

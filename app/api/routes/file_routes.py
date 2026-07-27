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
    """删除指定路径的文件"""
    path = req.get("path", "")
    if not path:
        raise HTTPException(400, "缺少文件路径")
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")
    # 安全检查：只允许删除 data/uploads/ 下的文件
    if not str(file_path.resolve()).startswith(str(UPLOAD_ROOT.resolve())):
        raise HTTPException(403, "不允许删除该目录外的文件")
    try:
        file_path.unlink()
        return {"status": "ok", "message": "文件已删除"}
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
    from fastapi.responses import Response
    import mimetypes
    file_path = _Path(path)
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")
    mime_type, _ = mimetypes.guess_type(str(file_path))
    content = file_path.read_bytes()
    return Response(
        content=content,
        media_type=mime_type or "application/octet-stream",
        headers={"Content-Disposition": f"inline; filename=\"{file_path.name}\""},
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

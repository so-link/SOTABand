"""文件管理路由 — 上传、存储"""

import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException

router = APIRouter()

# 上传文件存储根目录
UPLOAD_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "data" / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件，保存到 data/uploads/ 目录"""
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    # 安全文件名
    safe_name = Path(file.filename).name
    # 按日期分目录避免单目录文件过多
    date_dir = time.strftime("%Y-%m-%d")
    dest_dir = UPLOAD_ROOT / date_dir
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

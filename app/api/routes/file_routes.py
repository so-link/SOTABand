"""文件管理路由 — 上传与受控访问（按 file_id，不接受任意路径）

2026-11 改造：接口一律不再收发文件系统路径。此前 image/download/audio/delete
都接受 ``?path=`` 绝对路径，等于把"本机任意可读文件"暴露成接口（黑名单式
校验挡不住）；现在只有登记过的文件可访问，标识是 ``core.user_files`` 签发的
file_id，解析永远落在**当前用户目录**内。任意本地目录浏览（scan-directory /
browse-directory）随之删除——工作区只能装用户自己的受控文件。
"""

import json as _json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

from core import user_files

router = APIRouter()

# 上传子目录名：只允许单层字母/数字/._-（如 2026-10-31、dataset_1759...），
# 拒绝 "/" 与 ".."——否则 subdir 可把上传文件写到任意绝对路径。
_UPLOAD_SUBDIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# 上传文件存储根目录（用户目录：USER_HOME/uploads，每用户隔离）
from core.user_paths import user_home as _user_home


def _upload_root() -> Path:
    """当前用户的上传根（每请求动态解析：导入期求值会把所有人固定到同一个用户）。"""
    root = _user_home() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── 工作区间文件树 —— 后端持久化 ──
# 「工作区间」里的文件节点属于引擎状态，而非浏览器状态：以前整棵树只存在
# 前端 localStorage，换浏览器/清缓存后工作区间就全空了。现在状态源是
# USER_HOME/workspace_tree.json（与工具空间 workspace_tools.json 同一范式）。
# 文件节点存 fileId（不存路径）——路径由服务端按身份解析，树数据搬到家外
# 机器、给别的浏览器看都不会指错文件。
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


@router.get("/workspace/tree")
async def get_workspace_tree():
    """返回当前用户的工作区间文件树（节点以 fileId 引用文件）。"""
    return {"root": _read_workspace_tree()}


@router.put("/workspace/tree")
async def save_workspace_tree(payload: WorkspaceTreePayload):
    """整体覆盖保存工作区间文件树（上传 / 删除 / 展开收起后调用）。"""
    _write_workspace_tree(payload.root)
    return {"ok": True}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), subdir: str = Form("")):
    """上传文件：保存到当前用户 uploads/ 并登记，返回 file_id。

    响应里不再有 filePath——浏览器拿到路径就等于拿到越权素材，
    后续一切访问都以 fileId 为凭证。
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

    rec = user_files.register(dest_path, kind="upload")
    fmt = dest_path.suffix.lstrip(".").lower()

    return {
        "fileId": rec["file_id"],
        "fileName": rec["name"],
        "fileSize": rec["size"],
        "format": fmt,
        "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.get("")
async def list_registered_files():
    """当前用户登记过的全部文件（工作区兜底重建、调试用）。"""
    return {"files": user_files.list_files()}


@router.get("/{file_id}")
async def serve_file(file_id: str):
    """按 file_id 提供文件内容（预览/下载/图片/音频共用）。

    inline 处置：前端以 <img>/<audio>/fetch 消费；另存为用下载按钮在
    浏览器端完成。404 语义统一——未登记、非法、跨用户、磁盘缺失都是
    "文件不存在"，不给探测他人文件留侧信道。
    """
    from fastapi.responses import FileResponse
    import mimetypes

    try:
        abs_path = user_files.resolve(file_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    mime_type, _ = mimetypes.guess_type(str(abs_path))
    # 检测实际文件格式（某些工具生成 AIFF-C 但用 .wav 扩展名）
    if mime_type == "audio/wav":
        try:
            with open(abs_path, "rb") as _f:
                header = _f.read(12)
                if header[8:12] in (b"AIFC", b"AIFF"):
                    mime_type = "audio/aiff"
        except OSError:
            pass
    return FileResponse(
        abs_path,
        media_type=mime_type or "application/octet-stream",
        filename=abs_path.name,
        content_disposition_type="inline",
    )


@router.delete("/{file_id}")
async def delete_registered_file(file_id: str):
    """删除文件：注销登记并删掉磁盘文件（工作区删除语义）。"""
    if user_files.get(file_id) is None:
        raise HTTPException(404, "文件不存在")
    user_files.delete(file_id, purge=True)
    return {"status": "ok"}

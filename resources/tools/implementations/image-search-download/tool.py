
# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

# ── 项目根路径 ──
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录 ──
_DATA_DIR = _PROJECT_ROOT / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

# ── API 调用辅助 ──
def _call_api(api_name: str, **params) -> dict:
    """调用系统 API"""
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)

# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具"""
    import subprocess as _sp
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_name
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' not found"}
    venv_py = tool_dir / ".venv" / "bin" / "python"
    py_exe = str(venv_py) if venv_py.exists() else sys.executable
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}).read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(proc.stdout.strip())
    except:
        return {"status": "failed", "message": proc.stderr[:500]}

# ── 文件路径辅助 ──
def _resolve_path(path: str) -> str:
    """将相对/绝对路径转为绝对路径（基于 _PROJECT_ROOT）"""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)

# === 头部结束，以下由 LLM 生成 ===

import shutil
from datetime import datetime
from bing_image_downloader import downloader


def execute(**kwargs) -> dict[str, Any]:
    try:
        # 获取输入参数
        req = kwargs.get("req")
        n = kwargs.get("n")
        dataset = kwargs.get("dataset")

        if not req or not n or not dataset:
            return {
                "status": "failed",
                "message": "缺少必要参数: req, n, dataset 均为必填"
            }

        # 将 n 转换为整数（输入可能为字符串）
        try:
            n = int(n)
        except (ValueError, TypeError):
            return {
                "status": "failed",
                "message": "参数 n 必须为正整数"
            }

        if n <= 0:
            return {
                "status": "failed",
                "message": "参数 n 必须为正整数"
            }

        # 生成时间戳目录
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        download_dir = _DOWNLOADS_DIR / timestamp
        os.makedirs(download_dir, exist_ok=True)

        # 调用 bing-image-downloader 下载图片
        # 注意：该库会在 output_dir 下自动创建一个以搜索词命名的子目录
        try:
            downloader.download(
                query=req,
                limit=n,
                output_dir=str(download_dir),
                adult_filter_off=True,
                force_replace=False,
                timeout=600000,
                verbose=False
            )
        except Exception as e:
            # 下载库可能抛出异常，例如网络问题
            return {
                "status": "failed",
                "message": f"图片下载失败: {str(e)}"
            }

        # 下载完成后，图片位于 download_dir / req 子目录下
        source_subdir = download_dir / req
        if not source_subdir.exists() or not any(source_subdir.iterdir()):
            # 清理空目录
            if source_subdir.exists():
                source_subdir.rmdir()
            return {
                "status": "failed",
                "message": f"未搜索到相关图片: {req}"
            }

        # 将图片移动到 download_dir 根目录
        image_files = []
        for f in source_subdir.iterdir():
            if f.is_file():
                dest = download_dir / f.name
                shutil.move(str(f), str(dest))
                image_files.append(dest)
        source_subdir.rmdir()  # 删除空子目录

        if not image_files:
            return {
                "status": "failed",
                "message": "下载目录中无有效图片文件"
            }

        # 按文件名排序，取第一张
        image_files.sort(key=lambda p: p.name)
        first_image = image_files[0]

        # 统计数据
        file_count = len(image_files)
        total_size = sum(f.stat().st_size for f in image_files)
        formats = sorted(set(f.suffix.lstrip('.').lower() for f in image_files if f.suffix))

        # 注册数据集
        # 构造 raw_md 描述
        raw_md = f"图片搜索结果：{req}"

        # 调用数据集注册 API
        try:
            result_api = _call_api(
                "api-data-register",
                id=dataset,
                name=dataset,
                raw_md=raw_md,
                data_path=str(download_dir),
                file_count=file_count,
                total_size=total_size,
                formats=formats
            )
            # 检查返回结果，假设成功时包含 dataset_id
            dataset_id = result_api.get("dataset_id")
            if not dataset_id:
                return {
                    "status": "failed",
                    "message": "数据集注册失败: API 未返回 dataset_id"
                }
        except Exception as e:
            return {
                "status": "failed",
                "message": f"数据集注册API调用异常: {str(e)}"
            }

        # 构造相对路径（相对于项目根）
        rel_path = first_image.relative_to(_PROJECT_ROOT)
        image_path = f"./{rel_path.as_posix()}"

        return {
            "status": "success",
            "message": "成功下载并注册数据集",
            "output_format": "image",
            "data": {
                "image_path": image_path
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"工具执行异常: {str(e)}"
        }


# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

# ── 运行目录（由平台注入，禁止写死绝对路径或按 __file__ 层级反推）──
# SOTABAND_ROOT      系统根：代码 + 内置资源，视为只读
# SOTABAND_USER_HOME 当前用户目录：本工具的一切产物写这里
# TOOL_DIR           本工具实现目录
def _env_path(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None

def _find_up(path, marker):
    """兜底：向上找 marker 所在目录。只按特征文件定位，不假定固定层级。"""
    p = path
    for _ in range(12):
        if (p / marker).exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None

_tool_dir = _env_path("TOOL_DIR") or (Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())
_PROJECT_ROOT = _env_path("SOTABAND_ROOT") or _find_up(_tool_dir, "core") or Path.cwd()
# 用户目录：env 缺失时按「SOTABAND_HOME 根 + uid」还原，绝不退回系统根
_USER_HOME = _env_path("SOTABAND_USER_HOME") or (
    (_env_path("SOTABAND_HOME") or _PROJECT_ROOT.parent / "SB-USER")
    / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录（产物一律落用户目录，系统根随 git 分发不可写脏）──
_DATA_DIR = _USER_HOME / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

def _out_dir(*parts) -> Path:
    """本工具产物目录：USER_HOME/data/<工具或子目录>/<...>，自动创建。"""
    d = _DATA_DIR.joinpath(*parts) if parts else _DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d

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
    # 资源根读取顺序：私有优先（用户「复制为副本」后的修改生效），系统公共层兜底
    _roots = [_USER_HOME, _env_path("SOTABAND_HOME") or (_PROJECT_ROOT.parent / "SB-USER")
              / (os.environ.get("SOTABAND_SYSTEM_UID", "100").strip() or "100")]
    tool_dir = next((r / "tools" / "implementations" / tool_name for r in _roots
                         if (r / "tools" / "implementations" / tool_name / "tool.py").exists()),
                    _roots[0] / "tools" / "implementations" / tool_name)
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
        download_dir.mkdir(parents=True, exist_ok=True)

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
                f.rename(dest)
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

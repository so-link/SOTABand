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

# ── LLM 调用辅助（统一走系统配置的 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。

    跟随全局配置（config/settings.py 的 PROVIDER_PRESETS）自动选择服务商：
    DeepSeek / OpenAI / Kimi / 智谱 / 通义 / 硅基流动 / MiniMax / MiMo / 豆包 等。
    禁止在本工具内直连任何具体服务商端点或硬编码模型名。
    """
    import asyncio
    from core.llm.client import create_llm_client
    client = create_llm_client()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.chat(messages, **kwargs))
        loop.run_until_complete(client.aclose())
        return result
    finally:
        loop.close()

# ── 工具调用辅助 ──
def _call_tool(tool_name: str, **params) -> dict:
    """调用已注册的工具（通过 registry.json 查找工具 ID 对应的实现目录）"""
    import subprocess as _sp
    # 从 registry.json 中查找工具 ID（目录名）
    reg_path = _PROJECT_ROOT / "resources" / "tools" / "registry.json"
    tool_id = tool_name  # 默认用名称作为 ID
    if reg_path.exists():
        try:
            tools = json.loads(reg_path.read_text(encoding="utf-8"))
            # 先精确匹配 id，再模糊匹配 name
            for t in tools:
                if t.get("id") == tool_name or t.get("name") == tool_name:
                    tool_id = t["id"]
                    break
        except Exception:
            pass
    tool_dir = _PROJECT_ROOT / "resources" / "tools" / "implementations" / tool_id
    tool_file = tool_dir / "tool.py"
    if not tool_file.exists():
        return {"status": "failed", "message": f"Tool '{tool_name}' (id={tool_id}) not found"}
    venv_py = tool_dir / ".venv" / "bin" / "python"
    py_exe = str(venv_py) if venv_py.exists() else sys.executable
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}, encoding='utf-8').read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    # encoding/errors 必须显式：工具输出是含中文的 UTF-8 JSON，
    # Windows 下 text=True 默认按 GBK 解码会直接 UnicodeDecodeError
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=30)
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

import re
import shutil
import traceback


def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def _download_voc2007(local_root: Path) -> Path:
    """下载并解压 VOC2007 的 trainval 与 test 数据。

    Args:
        local_root: 数据集保存根目录，例如 data/datasets/VOC2007

    Returns:
        VOC2007 实际数据目录：local_root / VOCdevkit / VOC2007
    """
    local_root.mkdir(parents=True, exist_ok=True)

    # 磁盘空间预检：VOC2007 解压后约 1.1GB，保留一定余量
    try:
        free = shutil.disk_usage(str(local_root)).free
        min_required = 2 * 1024 ** 3  # 2GB
        if free < min_required:
            raise RuntimeError(
                f"本地存储空间不足：可用 {_format_size(free)}，至少需要 {_format_size(min_required)}"
            )
    except OSError:
        # 磁盘检测失败时继续，后续任务会暴露具体错误
        pass

    try:
        from torchvision.datasets.utils import download_and_extract_archive
    except ImportError as exc:
        raise RuntimeError("缺少 torchvision 依赖，无法下载 VOC2007 数据集") from exc

    archives = [
        {
            "url": "http://host.robots.ox.ac.uk/pascal/VOC/voc2007/VOCtrainval_06-Nov-2007.tar",
            "filename": "VOCtrainval_06-Nov-2007.tar",
            "md5": "c52e279531787c972589f7e41ab4ae64",
        },
        {
            "url": "http://host.robots.ox.ac.uk/pascal/VOC/voc2007/VOCtest_06-Nov-2007.tar",
            "filename": "VOCtest_06-Nov-2007.tar",
            "md5": "b6e924de25625d8de591ea690078ad9f",
        },
    ]

    for archive in archives:
        download_and_extract_archive(
            url=archive["url"],
            download_root=str(local_root),
            extract_root=str(local_root),
            filename=archive["filename"],
            md5=archive["md5"],
        )

    voc_dir = local_root / "VOCdevkit" / "VOC2007"
    if not voc_dir.exists():
        raise RuntimeError(f"下载并解压后未找到预期目录：{voc_dir}")

    return voc_dir


def _count_files(path: Path) -> tuple[int, int, list[str]]:
    """递归统计目录内文件数量、总大小和扩展名列表。"""
    file_count = 0
    total_size = 0
    formats = set()

    for root, _dirs, files in os.walk(str(path)):
        for name in files:
            file_path = Path(root) / name
            try:
                if file_path.is_file():
                    file_count += 1
                    total_size += file_path.stat().st_size
                    ext = file_path.suffix.lstrip(".").lower()
                    if ext:
                        formats.add(ext)
            except OSError:
                # 单个文件读取失败不阻塞整体统计
                continue

    return file_count, total_size, sorted(formats)


def execute(**kwargs) -> dict[str, Any]:
    """工具主入口：下载 VOC2007 数据集并注册。"""
    dataset = kwargs.get("dataset", "")
    dataset = str(dataset).strip()

    if not dataset:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 dataset 不能为空",
            "data": {},
        }

    # 目录命名校验：禁止路径分隔符、空字符、点目录等
    if (
        dataset in {".", ".."}
        or any(ch in dataset for ch in ("/", "\\", "\0"))
        or dataset.lower() == "vocdevkit"
    ):
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 dataset 不符合目录命名规范，请勿包含路径分隔符或使用系统保留名称",
            "data": {},
        }

    target_dir = _DATA_DIR / "datasets" / dataset

    try:
        voc_dir = _download_voc2007(target_dir)

        file_count, total_size, formats = _count_files(voc_dir)
        if not formats:
            formats = ["jpg", "xml"]

        raw_md = (
            f"# {dataset}\n\n"
            f"- 数据集名称：{dataset}\n"
            f"- 数据路径：{voc_dir}\n"
            f"- 文件数量：{file_count}\n"
            f"- 总大小：{_format_size(total_size)}\n"
            f"- 文件格式：{', '.join(formats)}\n\n"
            f"由 SOTABand 工具自动下载并注册。"
        )

        register_result = _call_api(
            "api-data-register",
            id=dataset,
            name=dataset,
            raw_md=raw_md,
            data_path=str(voc_dir),
            file_count=file_count,
            total_size=total_size,
            formats=formats,
        )

        if not register_result or not register_result.get("dataset_id"):
            raise RuntimeError(
                f"数据集注册API未返回 dataset_id，返回内容：{register_result}"
            )

        dataset_id = register_result.get("dataset_id")
        text = (
            f"{dataset} 数据集下载成功，已注册为 {dataset}。\n"
            f"本地目录：{target_dir}\n"
            f"数据目录：{voc_dir}\n"
            f"文件数：{file_count}\n"
            f"总大小：{_format_size(total_size)}"
        )

        return {
            "status": "success",
            "output_format": "text",
            "message": "下载并注册成功",
            "data": {
                "dataset": dataset,
                "local_dir": str(target_dir),
                "voc_dir": str(voc_dir),
                "registered": True,
                "dataset_id": dataset_id,
                "file_count": file_count,
                "total_size": total_size,
                "formats": formats,
                "text": text,
            },
        }

    except Exception as exc:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"工具执行失败：{str(exc)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {},
        }
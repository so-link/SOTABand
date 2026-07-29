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

from PIL import Image


def execute(**kwargs) -> dict[str, Any]:
    """将输入图片转换为黑白（灰度）图像并保存"""
    try:
        file = kwargs.get("file", "")
        if not file:
            return {
                "status": "failed",
                "message": "参数file不能为空",
                "output_format": "image",
                "data": {},
            }

        input_path = _resolve_path(file)
        if not os.path.isfile(input_path):
            return {
                "status": "failed",
                "message": f"输入文件不存在: {input_path}",
                "output_format": "image",
                "data": {},
            }

        # 读取并转换为灰度
        img = Image.open(input_path)
        gray_img = img.convert("L")

        # 构造输出文件名和路径
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        output_dir = _DOWNLOADS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        # 避免文件名冲突
        output_path = output_dir / f"{base_name}_gray.png"
        counter = 1
        while output_path.exists():
            output_path = output_dir / f"{base_name}_gray_{counter}.png"
            counter += 1

        gray_img.save(str(output_path))

        return {
            "status": "success",
            "message": "图片已转换为灰度",
            "output_format": "image",
            "data": {"image_path": str(output_path)},
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"图片处理失败: {str(e)}",
            "output_format": "image",
            "data": {},
        }
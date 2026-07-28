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

import pyttsx3
import tempfile
import wave


def execute(**kwargs) -> dict[str, Any]:
    """文字转语音工具入口"""
    try:
        # 1. 获取输入
        req = kwargs.get("req", "")
        if not req or not isinstance(req, str) or req.strip() == "":
            return {
                "status": "failed",
                "output_format": "file",
                "message": "输入文字不能为空",
                "data": {}
            }

        # 2. 用 pyttsx3 生成临时 AIFF 文件
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        file_name = f"tts_{int(time.time())}.wav"
        output_path = _DATA_DIR / file_name

        # 先用临时文件保存 pyttsx3 的输出（macOS 上是 AIFF-C 格式）
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
            tmp_path = tmp.name

        engine = pyttsx3.init()
        engine.save_to_file(req.strip(), tmp_path)
        engine.runAndWait()

        # 3. 将 AIFF 转换为 WAV（macOS 用 afconvert，Linux 用 ffmpeg）
        try:
            import subprocess as _sp
            if sys.platform == "darwin":
                # macOS: 用系统自带的 afconvert
                _sp.run(
                    ["afconvert", "-f", "WAVE", "-d", "LEI16@22050", tmp_path, str(output_path)],
                    capture_output=True, text=True, timeout=30, check=True
                )
            else:
                # Linux: 尝试 ffmpeg
                _sp.run(
                    ["ffmpeg", "-y", "-i", tmp_path, "-acodec", "pcm_s16le",
                     "-ar", "22050", "-ac", "1", str(output_path)],
                    capture_output=True, text=True, timeout=30, check=True
                )
        except Exception:
            # 转换失败，保留 aiff 格式
            import shutil
            output_path = _DATA_DIR / f"tts_{int(time.time())}.aiff"
            shutil.copy(tmp_path, str(output_path))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # 5. 构造成功响应
        return {
            "status": "success",
            "output_format": "file",
            "message": "语音合成成功",
            "data": {
                "file_path": str(output_path)
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "file",
            "message": f"语音合成失败：{str(e)}",
            "data": {}
        }
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
            shutil.copyfile(tmp_path, str(output_path))
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
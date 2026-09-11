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

import math


def _to_number(value, name: str):
    """将 int/float 或数字字符串转换为数值，失败抛 ValueError"""
    if isinstance(value, bool):
        raise ValueError(f"参数 {name} 必须为数字，不能为布尔值")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"参数 {name} 不能为空")
        try:
            if "." in text or "e" in text.lower():
                return float(text)
            return int(text)
        except ValueError:
            raise ValueError(f"参数 {name} 无法转换为数字: {value!r}")
    raise ValueError(f"参数 {name} 必须为数字类型（int/float）或数字字符串")


def execute(**kwargs) -> dict[str, Any]:
    """工具入口：根据 mode 分发到 square 或 browse-file 模式"""
    try:
        mode = kwargs.get("mode", "square")

        if mode == "square":
            return _mode_square(kwargs)
        elif mode == "browse-file":
            return _mode_browse_file(kwargs)
        else:
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"未知模式: {mode}",
                "data": {}
            }
    except Exception as e:
        import traceback
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"工具执行失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {}
        }


def _mode_square(params: dict) -> dict[str, Any]:
    """模式 1：计算 x 的二次方"""
    try:
        x = _to_number(params.get("x"), "x")
    except ValueError as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": str(e),
            "data": {}
        }

    result = x ** 2
    return {
        "status": "success",
        "output_format": "text",
        "message": f"计算成功，{x}^2 = {result}",
        "data": {
            "text": str(result)
        }
    }


def _mode_browse_file(params: dict) -> dict[str, Any]:
    """模式 2：计算 sin(x) + sin(y)，并将结果写入 file_path 指定的文件"""
    try:
        x = _to_number(params.get("x"), "x")
        y = _to_number(params.get("y"), "y")
    except ValueError as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": str(e),
            "data": {}
        }

    file_path = params.get("file_path")

    # 校验 file_path 是否为合法路径字符串
    if not isinstance(file_path, str) or not file_path.strip():
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 file_path 必须为非空字符串路径",
            "data": {}
        }

    result = math.sin(x) + math.sin(y)
    resolved_path = _resolve_path(file_path.strip())

    try:
        p = Path(resolved_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(result), encoding="utf-8")
    except Exception as e:
        import traceback
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"文件写入失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {}
        }

    return {
        "status": "success",
        "output_format": "file",
        "message": f"计算成功，sin({x}) + sin({y}) = {result}，已写入文件",
        "data": {
            "file_path": resolved_path,
            "result": result
        }
    }
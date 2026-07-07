# === SOTABand 工具标准头部（自动注入） ===
import os, sys, json, time
from pathlib import Path

_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# === 头部结束 ===

from typing import Any, Dict

def execute(**kwargs) -> Dict[str, Any]:
    try:
        # 本工具无输入参数，直接返回预定义问候语
        return {
            "status": "success",
            "message": "问候输出完成",
            "output_format": "text",
            "data": {"text": "hello world"}
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": str(e),
            "output_format": "text",
            "data": {}
        }
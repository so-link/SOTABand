# 自动调试日志

- **工具**: patent-search-wrapper
- **时间**: 20260731_113353
- **结果**: 成功（共 2 轮）
- **日志条目**: 1 轮

---

## 第 1 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "参数 n 缺失或类型错误，需要整数"}

stderr:

```

### 发送给 LLM 的 Prompt

```
Debug this tool code. It failed execution.

=== CURRENT CODE ===
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

def execute(**kwargs) -> dict[str, Any]:
    """专利检索包装器主函数"""
    try:
        # 1. 参数提取与校验
        req = kwargs.get("req")
        n = kwargs.get("n")
        year = kwargs.get("year")
        dataset = kwargs.get("dataset")

        if not req or not isinstance(req, str):
            return {"status": "failed", "message": "参数 req 缺失或类型错误，需要非空字符串"}
        if n is None or not isinstance(n, int):
            return {"status": "failed", "message": "参数 n 缺失或类型错误，需要整数"}
        if year is None or not isinstance(year, int):
            return {"status": "failed", "message": "参数 year 缺失或类型错误，需要整数"}
        if not dataset or not isinstance(dataset, str):
            return {"status": "failed", "message": "参数 dataset 缺失或类型错误，需要非空字符串"}

        # 2. 调用 Lens 专利检索与注册工具
        tool_result = _call_tool("Lens专利检索与注册工具",
                                 req=req, n=n, year=year, dataset=dataset)

        if tool_result.get("status") != "success":
            error_msg = tool_result.get("message", "未知错误")
            return {"status": "failed", "message": f"Lens专利检索与注册工具调用失败: {error_msg}"}

        # 3. 提取原始数据
        raw_data = tool_result.get("data", [])
        if not isinstance(raw_data, list):
            # 兼容可能返回 {"patents": [...]} 的结构
            if isinstance(raw_data, dict) and "patents" in raw_data:
                raw_data = raw_data["patents"]
            else:
                raw_data = []

        # 4. 转换为统一的 table 格式
        columns = ["patent_id", "title", "applicant", "year"]
        rows = []
        for patent in raw_data:
            if isinstance(patent, dict):
                rows.append([
                    patent.get("patent_id", patent.get("id", "")),
                    patent.get("title", ""),
                    patent.get("applicant", patent.get("assignee", "")),
                    patent.get("year", year)
                ])

        # 5. 构造返回消息
        count = len(rows)
        if count == 0:
            message = "检索无结果"
        else:
            message = f"返回{count}条专利记录"

        return {
            "status": "success",
            "message": message,
            "output_format": "table",
            "data": {
                "columns": columns,
                "rows": rows
            }
        }

    except Exception as e:
        return {"status": "failed", "message": str(e)}
=== END CODE ===

=== TEST INPUT ===
{
  "req": "drone",
  "n": "4",
  "year": "2025",
  "dataset": "111"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "参数 n 缺失或类型错误，需要整数"}
stderr: 
=== END RESULT ===


Fix the code based on the error and the dependency feedback above.
- If a dependency was installed successfully: keep the import, fix other code logic issues.
- If a dependency failed to install: replace it with an alternative library or stdlib approach.
- If the error is a code logic bug (not dependency-related): fix the bug.

Output the COMPLETE fixed Python file (including template header).
INTERFACE RULES: execute(**kwargs)->dict, kwargs.get, {status,output_format,message,data}, try/except.
Output ONLY Python code. NO pip install, NO subprocess, NO install directives, NO markdown.
```

### LLM 返回

```
```python
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

def execute(**kwargs) -> dict[str, Any]:
    """专利检索包装器主函数"""
    try:
        # 1. 参数提取与校验
        req = kwargs.get("req")
        n = kwargs.get("n")
        year = kwargs.get("year")
        dataset = kwargs.get("dataset")

        if not req or not isinstance(req, str):
            return {"status": "failed", "message": "参数 req 缺失或类型错误，需要非空字符串"}

        # 接受字符串类型的数字并转换为整数
        try:
            n = int(n)
        except (TypeError, ValueError):
            return {"status": "failed", "message": "参数 n 缺失或类型错误，需要整数"}
        try:
            year = int(year)
        except (TypeError, ValueError):
            return {"status": "failed", "message": "参数 year 缺失或类型错误，需要整数"}
        if not dataset or not isinstance(dataset, str):
            return {"status": "failed", "message": "参数 dataset 缺失或类型错误，需要非空字符串"}

        # 2. 调用 Lens 专利检索与注册工具
        tool_result = _call_tool("Lens专利检索与注册工具",
                                 req=req, n=n, year=year, dataset=dataset)

        if tool_result.get("status") != "success":
            error_msg = tool_result.get("message", "未知错误")
            return {"status": "failed", "message": f"Lens专利检索与注册工具调用失败: {error_msg}"}

        # 3. 提取原始数据
        raw_data = tool_result.get("data", [])
        if not isinstance(raw_data, list):
            # 兼容可能返回 {"patents": [...]} 的结构
            if isinstance(raw_data, dict) and "patents" in raw_data:
                raw_data = raw_data["patents"]
            else:
                raw_data = []

        # 4. 转换为统一的 table 格式
        columns = ["patent_id", "title", "applicant", "year"]
        rows = []
        for patent in raw_data:
            if isinstance(patent, dict):
                rows.append([
                    patent.get("patent_id", patent.get("id", "")),
                    patent.get("title", ""),
                    patent.get("applicant", patent.get("assignee", "")),
                    patent.get("year", year)
                ])

        # 5. 构造返回消息
        count = len(rows)
        if count == 0:
            message = "检索无结果"
        else:
            message = f"返回{count}条专利记录"

        return {
            "status": "success",
            "message": message,
            "output_format": "table",
            "data": {
                "columns": columns,
                "rows": rows
            }
        }

    except Exception as e:
        return {"status": "failed", "message": str(e)}
```
```

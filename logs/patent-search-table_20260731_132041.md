# 自动调试日志

- **工具**: patent-search-table
- **时间**: 20260731_132041
- **结果**: 成功（共 3 轮）
- **日志条目**: 2 轮

---

## 第 1 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "Lens工具返回数据格式错误，期望列表"}

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
    """
    专利信息检索与表格生成器
    参数: req, n, year, dataset
    返回: 表格格式的专利数据
    """
    try:
        # 1. 参数提取与类型转换
        req = kwargs.get("req", "")
        n_raw = kwargs.get("n", None)
        year = kwargs.get("year", "")
        dataset = kwargs.get("dataset", "")

        # 2. 参数验证
        if not req or not isinstance(req, str) or req.strip() == "":
            return {"status": "failed", "message": "参数验证失败：req 不能为空"}
        if n_raw is None:
            return {"status": "failed", "message": "参数验证失败：n 不能为空"}
        try:
            n = int(n_raw)
            if n <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return {"status": "failed", "message": "参数验证失败：n 必须为正整数"}
        if not year or not isinstance(year, str) or year.strip() == "":
            return {"status": "failed", "message": "参数验证失败：year 不能为空"}
        if not dataset or not isinstance(dataset, str) or dataset.strip() == "":
            return {"status": "failed", "message": "参数验证失败：dataset 不能为空"}

        # 3. 调用 Lens 专利检索与注册工具
        lens_result = _call_tool(
            "Lens专利检索与注册工具",
            req=req.strip(),
            n=n,
            year=year.strip(),
            dataset=dataset.strip()
        )

        # 4. 检查调用结果
        if not isinstance(lens_result, dict):
            return {"status": "failed", "message": f"Lens工具返回异常数据类型: {type(lens_result)}"}

        if lens_result.get("status") != "success":
            error_msg = lens_result.get("message", "未知错误")
            return {"status": "failed", "message": f"Lens工具调用异常：{error_msg}"}

        # 5. 提取专利列表
        patents = lens_result.get("data", [])
        if not isinstance(patents, list):
            return {"status": "failed", "message": "Lens工具返回数据格式错误，期望列表"}

        if len(patents) == 0:
            # 未检索到任何专利，按需求返回失败或空表格
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 6. 转换为表格格式
        # 定义标准的中文列名
        target_columns = ["专利号", "标题", "申请日", "专利权人", "摘要"]
        # 字段映射：英文 -> 中文，兼容直接中文返回
        field_map = {
            "patent_number": "专利号",
            "title": "标题",
            "application_date": "申请日",
            "assignee": "专利权人",
            "abstract": "摘要",
            # 同时支持中文键直接使用
            "专利号": "专利号",
            "标题": "标题",
            "申请日": "申请日",
            "专利权人": "专利权人",
            "摘要": "摘要",
        }

        rows = []
        for p in patents:
            if not isinstance(p, dict):
                # 非字典数据跳过或尝试转换
                continue
            row = []
            for cn_col in target_columns:
                # 尝试多种可能的键名
                value = ""
                # 先尝试英文键
                for en_key, cn_key in field_map.items():
                    if cn_key == cn_col and en_key in p:
                        value = p[en_key]
                        break
                # 如果没找到，再尝试直接用中文键（已在field_map中包含）
                if value == "":
                    if cn_col in p:
                        value = p[cn_col]
                    else:
                        # 尝试从任意键匹配
                        for k in p:
                            if field_map.get(k) == cn_col:
                                value = p[k]
                                break
                # 统一转换为字符串，避免None等问题
                row.append(str(value) if value is not None else "")
            if any(row):  # 至少有一列有值才加入
                rows.append(row)

        # 如果有效行数为0，返回未检索到
        if not rows:
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 7. 构建输出
        output_data = {
            "columns": target_columns,
            "rows": rows
        }

        return {
            "status": "success",
            "output_format": "table",
            "message": f"成功检索到 {len(rows)} 条专利",
            "data": output_data
        }

    except Exception as e:
        return {"status": "failed", "message": f"工具执行异常：{str(e)}"}
=== END CODE ===

=== TEST INPUT ===
{
  "req": "目标检测",
  "n": "3",
  "year": "2025",
  "dataset": "目标检测"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "Lens工具返回数据格式错误，期望列表"}
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
    """
    专利信息检索与表格生成器
    参数: req, n, year, dataset
    返回: 表格格式的专利数据
    """
    try:
        # 1. 参数提取与类型转换
        req = kwargs.get("req", "")
        n_raw = kwargs.get("n", None)
        year = kwargs.get("year", "")
        dataset = kwargs.get("dataset", "")

        # 2. 参数验证
        if not req or not isinstance(req, str) or req.strip() == "":
            return {"status": "failed", "message": "参数验证失败：req 不能为空"}
        if n_raw is None:
            return {"status": "failed", "message": "参数验证失败：n 不能为空"}
        try:
            n = int(n_raw)
            if n <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return {"status": "failed", "message": "参数验证失败：n 必须为正整数"}
        if not year or not isinstance(year, str) or year.strip() == "":
            return {"status": "failed", "message": "参数验证失败：year 不能为空"}
        if not dataset or not isinstance(dataset, str) or dataset.strip() == "":
            return {"status": "failed", "message": "参数验证失败：dataset 不能为空"}

        # 3. 调用 Lens 专利检索与注册工具
        lens_result = _call_tool(
            "Lens专利检索与注册工具",
            req=req.strip(),
            n=n,
            year=year.strip(),
            dataset=dataset.strip()
        )

        # 4. 检查调用结果
        if not isinstance(lens_result, dict):
            return {"status": "failed", "message": f"Lens工具返回异常数据类型: {type(lens_result)}"}

        if lens_result.get("status") != "success":
            error_msg = lens_result.get("message", "未知错误")
            return {"status": "failed", "message": f"Lens工具调用异常：{error_msg}"}

        # 5. 提取专利列表（兼容多种返回格式）
        patents = lens_result.get("data", [])
        # 如果 data 是字典，尝试从常见嵌套键中提取列表
        if isinstance(patents, dict):
            found = False
            for key in ["patents", "results", "items", "records"]:
                if key in patents and isinstance(patents[key], list):
                    patents = patents[key]
                    found = True
                    break
            if not found:
                return {
                    "status": "failed",
                    "message": f"Lens工具返回数据不是列表，而是字典，键为: {list(patents.keys())}"
                }
        if not isinstance(patents, list):
            return {
                "status": "failed",
                "message": f"Lens工具返回数据格式错误，期望列表，实际类型: {type(patents)}"
            }

        if len(patents) == 0:
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 6. 转换为表格格式
        target_columns = ["专利号", "标题", "申请日", "专利权人", "摘要"]
        field_map = {
            "patent_number": "专利号",
            "title": "标题",
            "application_date": "申请日",
            "assignee": "专利权人",
            "abstract": "摘要",
            "专利号": "专利号",
            "标题": "标题",
            "申请日": "申请日",
            "专利权人": "专利权人",
            "摘要": "摘要",
        }

        rows = []
        for p in patents:
            if not isinstance(p, dict):
                continue
            row = []
            for cn_col in target_columns:
                value = ""
                # 尝试英文/中文键
                for en_key, cn_key in field_map.items():
                    if cn_key == cn_col and en_key in p:
                        value = p[en_key]
                        break
                if value == "" and cn_col in p:
                    value = p[cn_col]
                row.append(str(value) if value is not None else "")
            if any(row):
                rows.append(row)

        if not rows:
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 7. 构建输出
        output_data = {
            "columns": target_columns,
            "rows": rows
        }

        return {
            "status": "success",
            "output_format": "table",
            "message": f"成功检索到 {len(rows)} 条专利",
            "data": output_data
        }

    except Exception as e:
        return {"status": "failed", "message": f"工具执行异常：{str(e)}"}
```
```

======================

## 第 2 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "Lens工具返回数据不是列表，而是字典，键为: ['columns', 'rows']"}

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
    """
    专利信息检索与表格生成器
    参数: req, n, year, dataset
    返回: 表格格式的专利数据
    """
    try:
        # 1. 参数提取与类型转换
        req = kwargs.get("req", "")
        n_raw = kwargs.get("n", None)
        year = kwargs.get("year", "")
        dataset = kwargs.get("dataset", "")

        # 2. 参数验证
        if not req or not isinstance(req, str) or req.strip() == "":
            return {"status": "failed", "message": "参数验证失败：req 不能为空"}
        if n_raw is None:
            return {"status": "failed", "message": "参数验证失败：n 不能为空"}
        try:
            n = int(n_raw)
            if n <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return {"status": "failed", "message": "参数验证失败：n 必须为正整数"}
        if not year or not isinstance(year, str) or year.strip() == "":
            return {"status": "failed", "message": "参数验证失败：year 不能为空"}
        if not dataset or not isinstance(dataset, str) or dataset.strip() == "":
            return {"status": "failed", "message": "参数验证失败：dataset 不能为空"}

        # 3. 调用 Lens 专利检索与注册工具
        lens_result = _call_tool(
            "Lens专利检索与注册工具",
            req=req.strip(),
            n=n,
            year=year.strip(),
            dataset=dataset.strip()
        )

        # 4. 检查调用结果
        if not isinstance(lens_result, dict):
            return {"status": "failed", "message": f"Lens工具返回异常数据类型: {type(lens_result)}"}

        if lens_result.get("status") != "success":
            error_msg = lens_result.get("message", "未知错误")
            return {"status": "failed", "message": f"Lens工具调用异常：{error_msg}"}

        # 5. 提取专利列表（兼容多种返回格式）
        patents = lens_result.get("data", [])
        # 如果 data 是字典，尝试从常见嵌套键中提取列表
        if isinstance(patents, dict):
            found = False
            for key in ["patents", "results", "items", "records"]:
                if key in patents and isinstance(patents[key], list):
                    patents = patents[key]
                    found = True
                    break
            if not found:
                return {
                    "status": "failed",
                    "message": f"Lens工具返回数据不是列表，而是字典，键为: {list(patents.keys())}"
                }
        if not isinstance(patents, list):
            return {
                "status": "failed",
                "message": f"Lens工具返回数据格式错误，期望列表，实际类型: {type(patents)}"
            }

        if len(patents) == 0:
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 6. 转换为表格格式
        target_columns = ["专利号", "标题", "申请日", "专利权人", "摘要"]
        field_map = {
            "patent_number": "专利号",
            "title": "标题",
            "application_date": "申请日",
            "assignee": "专利权人",
            "abstract": "摘要",
            "专利号": "专利号",
            "标题": "标题",
            "申请日": "申请日",
            "专利权人": "专利权人",
            "摘要": "摘要",
        }

        rows = []
        for p in patents:
            if not isinstance(p, dict):
                continue
            row = []
            for cn_col in target_columns:
                value = ""
                # 尝试英文/中文键
                for en_key, cn_key in field_map.items():
                    if cn_key == cn_col and en_key in p:
                        value = p[en_key]
                        break
                if value == "" and cn_col in p:
                    value = p[cn_col]
                row.append(str(value) if value is not None else "")
            if any(row):
                rows.append(row)

        if not rows:
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 7. 构建输出
        output_data = {
            "columns": target_columns,
            "rows": rows
        }

        return {
            "status": "success",
            "output_format": "table",
            "message": f"成功检索到 {len(rows)} 条专利",
            "data": output_data
        }

    except Exception as e:
        return {"status": "failed", "message": f"工具执行异常：{str(e)}"}
=== END CODE ===

=== TEST INPUT ===
{
  "req": "目标检测",
  "n": "3",
  "year": "2025",
  "dataset": "目标检测"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "Lens工具返回数据不是列表，而是字典，键为: ['columns', 'rows']"}
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
    """
    专利信息检索与表格生成器
    参数: req, n, year, dataset
    返回: 表格格式的专利数据
    """
    try:
        # 1. 参数提取与类型转换
        req = kwargs.get("req", "")
        n_raw = kwargs.get("n", None)
        year = kwargs.get("year", "")
        dataset = kwargs.get("dataset", "")

        # 2. 参数验证
        if not req or not isinstance(req, str) or req.strip() == "":
            return {"status": "failed", "message": "参数验证失败：req 不能为空"}
        if n_raw is None:
            return {"status": "failed", "message": "参数验证失败：n 不能为空"}
        try:
            n = int(n_raw)
            if n <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return {"status": "failed", "message": "参数验证失败：n 必须为正整数"}
        if not year or not isinstance(year, str) or year.strip() == "":
            return {"status": "failed", "message": "参数验证失败：year 不能为空"}
        if not dataset or not isinstance(dataset, str) or dataset.strip() == "":
            return {"status": "failed", "message": "参数验证失败：dataset 不能为空"}

        # 3. 调用 Lens 专利检索与注册工具
        lens_result = _call_tool(
            "Lens专利检索与注册工具",
            req=req.strip(),
            n=n,
            year=year.strip(),
            dataset=dataset.strip()
        )

        # 4. 检查调用结果
        if not isinstance(lens_result, dict):
            return {"status": "failed", "message": f"Lens工具返回异常数据类型: {type(lens_result)}"}

        if lens_result.get("status") != "success":
            error_msg = lens_result.get("message", "未知错误")
            return {"status": "failed", "message": f"Lens工具调用异常：{error_msg}"}

        # 5. 提取数据内容（兼容列表和表格两种格式）
        data = lens_result.get("data", [])

        # 5.1 如果数据本身就是表格格式（包含 columns 和 rows），直接使用
        if isinstance(data, dict) and "columns" in data and "rows" in data:
            rows_count = len(data["rows"])
            return {
                "status": "success",
                "output_format": "table",
                "message": f"成功检索到 {rows_count} 条专利",
                "data": data
            }

        # 5.2 否则尝试从 data 中提取专利列表
        patents = data
        # 如果 data 是字典，尝试从常见嵌套键中提取列表
        if isinstance(patents, dict):
            found = False
            for key in ["patents", "results", "items", "records"]:
                if key in patents and isinstance(patents[key], list):
                    patents = patents[key]
                    found = True
                    break
            if not found:
                return {
                    "status": "failed",
                    "message": f"Lens工具返回数据不是列表，而是字典，键为: {list(patents.keys())}"
                }
        if not isinstance(patents, list):
            return {
                "status": "failed",
                "message": f"Lens工具返回数据格式错误，期望列表，实际类型: {type(patents)}"
            }

        if len(patents) == 0:
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 6. 转换为表格格式
        target_columns = ["专利号", "标题", "申请日", "专利权人", "摘要"]
        field_map = {
            "patent_number": "专利号",
            "title": "标题",
            "application_date": "申请日",
            "assignee": "专利权人",
            "abstract": "摘要",
            "专利号": "专利号",
            "标题": "标题",
            "申请日": "申请日",
            "专利权人": "专利权人",
            "摘要": "摘要",
        }

        rows = []
        for p in patents:
            if not isinstance(p, dict):
                continue
            row = []
            for cn_col in target_columns:
                value = ""
                # 尝试英文/中文键
                for en_key, cn_key in field_map.items():
                    if cn_key == cn_col and en_key in p:
                        value = p[en_key]
                        break
                if value == "" and cn_col in p:
                    value = p[cn_col]
                row.append(str(value) if value is not None else "")
            if any(row):
                rows.append(row)

        if not rows:
            return {"status": "failed", "message": "未检索到符合条件的专利"}

        # 7. 构建输出
        output_data = {
            "columns": target_columns,
            "rows": rows
        }

        return {
            "status": "success",
            "output_format": "table",
            "message": f"成功检索到 {len(rows)} 条专利",
            "data": output_data
        }

    except Exception as e:
        return {"status": "failed", "message": f"工具执行异常：{str(e)}"}
```
```

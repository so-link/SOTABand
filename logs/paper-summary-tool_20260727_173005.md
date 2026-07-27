# 自动调试日志

- **工具**: paper-summary-tool
- **时间**: 20260727_173005
- **结果**: 成功（共 3 轮）
- **日志条目**: 2 轮

---

## 第 1 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "无法获取数据集信息: 未知错误"}

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

def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主函数"""
    try:
        # 1. 参数解析
        dataset = kwargs.get("dataset")
        if not dataset:
            return {"status": "failed", "message": "缺少必填参数: dataset"}

        n = kwargs.get("n", 300)

        # 2. 获取数据集信息并获取路径
        api_result = _call_api("api-data-get", name=dataset)
        if not api_result or api_result.get("status") != "success":
            err_msg = api_result.get("message", "未知错误") if api_result else "无响应"
            return {"status": "failed", "message": f"无法获取数据集信息: {err_msg}"}

        dataset_info = api_result.get("dataset")
        if not dataset_info:
            return {"status": "failed", "message": "数据集信息为空"}

        data_path_str = dataset_info.get("path") or dataset_info.get("directory")
        if not data_path_str:
            return {"status": "failed", "message": "数据集路径未配置"}
        data_path = Path(data_path_str)
        if not data_path.exists():
            return {"status": "failed", "message": f"数据集目录不存在: {data_path}"}

        # 3. 扫描 PDF 文件
        pdf_files = sorted(data_path.glob("*.pdf"))
        if not pdf_files:
            return {
                "status": "success",
                "output_format": "table",
                "message": "未发现 PDF 文件，无需处理",
                "data": {
                    "columns": ["字段", "值"],
                    "rows": [
                        ["处理文件数", "0"],
                        ["数据集", dataset],
                    ],
                },
            }

        # 4. 导入 PDF 解析库（由系统保证依赖存在）
        try:
            import pdfplumber
            use_pdfplumber = True
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                use_pdfplumber = False
            except ImportError:
                return {"status": "failed", "message": "PDF 解析库未安装（需要 pdfplumber 或 PyPDF2）"}

        # 5. 获取 DeepSeek API KEY
        key_result = _call_api("api-deepseek-get-key")
        if not key_result or key_result.get("status") != "success":
            err_msg = key_result.get("message", "未知错误") if key_result else "无响应"
            return {"status": "failed", "message": f"获取 DeepSeek API KEY 失败: {err_msg}"}

        api_key = key_result.get("api_key")
        base_url = key_result.get("base_url")
        model = key_result.get("model", "deepseek-chat")
        if not api_key or not base_url:
            return {"status": "failed", "message": "DeepSeek API 配置不完整（缺少 api_key 或 base_url）"}

        # 6. 初始化 OpenAI 客户端
        try:
            from openai import OpenAI
        except ImportError:
            return {"status": "failed", "message": "openai 库未安装"}

        client = OpenAI(api_key=api_key, base_url=base_url)

        # 7. 遍历处理每一篇 PDF
        total = len(pdf_files)
        success = 0
        failed = 0
        summaries = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                if use_pdfplumber:
                    with pdfplumber.open(str(pdf_file)) as pdf:
                        text = "\n".join(
                            [page.extract_text() or "" for page in pdf.pages]
                        )
                else:
                    reader = PdfReader(str(pdf_file))
                    text = "\n".join(
                        [page.extract_text() or "" for page in reader.pages]
                    )

                # 截断文本，避免超出模型上下文（取前 8000 字符）
                max_text_len = 8000
                if len(text) > max_text_len:
                    text = text[:max_text_len]

                # 调用 DeepSeek 生成摘要
                prompt = (
                    f"请根据以下论文内容，提取论文标题、作者，并生成不超过{n}字的摘要。"
                    f"直接输出结果，格式如下：\n"
                    f"标题：<标题>\n作者：<作者>\n摘要：<摘要>\n\n"
                    f"论文内容：\n{text}"
                )
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1024,
                )
                summary_text = response.choices[0].message.content.strip()

                # 每条结果作为一个独立的二级标题块
                summaries.append(f"## {pdf_file.stem}\n\n{summary_text}\n")
                success += 1

            except Exception as e:
                failed += 1
                summaries.append(
                    f"## {pdf_file.stem}\n\n_处理失败_: {str(e)}\n"
                )

        # 8. 生成汇总 Markdown 文件
        md_content = (
            f"# 论文摘要报告\n\n"
            f"**数据集**: {dataset}\n\n"
            f"**处理文件数**: {total} (成功: {success}, 失败: {failed})\n\n"
        )
        md_content += "\n---\n\n".join(summaries)

        md_path = data_path / "summaries.md"
        md_path.write_text(md_content, encoding="utf-8")

        # 9. 返回结果（表格格式，便于前端渲染）
        return {
            "status": "success",
            "output_format": "table",
            "message": f"已处理论文 {success} 篇，摘要报告已保存至 {md_path}",
            "data": {
                "columns": ["字段", "值"],
                "rows": [
                    ["处理文件数", str(success)],
                    ["数据集", dataset],
                    ["总文件数", str(total)],
                    ["失败文件数", str(failed)],
                    ["报告路径", str(md_path)],
                ],
            },
        }

    except Exception as e:
        return {"status": "failed", "message": str(e)}
=== END CODE ===

=== TEST INPUT ===
{
  "dataset": "memmem",
  "n": "500"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "无法获取数据集信息: 未知错误"}
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

def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主函数"""
    try:
        # 1. 参数解析
        dataset = kwargs.get("dataset")
        if not dataset:
            return {"status": "failed", "message": "缺少必填参数: dataset"}

        # 确保 n 为整数
        raw_n = kwargs.get("n", 300)
        try:
            n = int(raw_n)
        except (ValueError, TypeError):
            n = 300

        # 2. 获取数据集信息并获取路径
        api_result = _call_api("api-data-get", name=dataset)
        if not api_result or api_result.get("status") != "success":
            # 更详细的错误消息，便于调试
            if api_result:
                err_detail = json.dumps(api_result, ensure_ascii=False, default=str)
                err_msg = f"API返回: {err_detail[:500]}"
            else:
                err_msg = "API 无响应"
            return {"status": "failed", "message": f"无法获取数据集信息: {err_msg}"}

        dataset_info = api_result.get("dataset")
        if not dataset_info:
            return {"status": "failed", "message": "数据集信息为空"}

        data_path_str = dataset_info.get("path") or dataset_info.get("directory")
        if not data_path_str:
            return {"status": "failed", "message": "数据集路径未配置"}
        data_path = Path(data_path_str)
        if not data_path.exists():
            return {"status": "failed", "message": f"数据集目录不存在: {data_path}"}

        # 3. 扫描 PDF 文件
        pdf_files = sorted(data_path.glob("*.pdf"))
        if not pdf_files:
            return {
                "status": "success",
                "output_format": "table",
                "message": "未发现 PDF 文件，无需处理",
                "data": {
                    "columns": ["字段", "值"],
                    "rows": [
                        ["处理文件数", "0"],
                        ["数据集", dataset],
                    ],
                },
            }

        # 4. 导入 PDF 解析库（由系统保证依赖存在）
        try:
            import pdfplumber
            use_pdfplumber = True
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                use_pdfplumber = False
            except ImportError:
                return {"status": "failed", "message": "PDF 解析库未安装（需要 pdfplumber 或 PyPDF2）"}

        # 5. 获取 DeepSeek API KEY
        key_result = _call_api("api-deepseek-get-key")
        if not key_result or key_result.get("status") != "success":
            if key_result:
                err_detail = json.dumps(key_result, ensure_ascii=False, default=str)
                err_msg = f"API返回: {err_detail[:500]}"
            else:
                err_msg = "API 无响应"
            return {"status": "failed", "message": f"获取 DeepSeek API KEY 失败: {err_msg}"}

        api_key = key_result.get("api_key")
        base_url = key_result.get("base_url")
        model = key_result.get("model", "deepseek-chat")
        if not api_key or not base_url:
            return {"status": "failed", "message": "DeepSeek API 配置不完整（缺少 api_key 或 base_url）"}

        # 6. 初始化 OpenAI 客户端
        try:
            from openai import OpenAI
        except ImportError:
            return {"status": "failed", "message": "openai 库未安装"}

        client = OpenAI(api_key=api_key, base_url=base_url)

        # 7. 遍历处理每一篇 PDF
        total = len(pdf_files)
        success = 0
        failed = 0
        summaries = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                if use_pdfplumber:
                    with pdfplumber.open(str(pdf_file)) as pdf:
                        text = "\n".join(
                            [page.extract_text() or "" for page in pdf.pages]
                        )
                else:
                    reader = PdfReader(str(pdf_file))
                    text = "\n".join(
                        [page.extract_text() or "" for page in reader.pages]
                    )

                # 截断文本，避免超出模型上下文（取前 8000 字符）
                max_text_len = 8000
                if len(text) > max_text_len:
                    text = text[:max_text_len]

                # 调用 DeepSeek 生成摘要
                prompt = (
                    f"请根据以下论文内容，提取论文标题、作者，并生成不超过{n}字的摘要。"
                    f"直接输出结果，格式如下：\n"
                    f"标题：<标题>\n作者：<作者>\n摘要：<摘要>\n\n"
                    f"论文内容：\n{text}"
                )
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1024,
                )
                summary_text = response.choices[0].message.content.strip()

                # 每条结果作为一个独立的二级标题块
                summaries.append(f"## {pdf_file.stem}\n\n{summary_text}\n")
                success += 1

            except Exception as e:
                failed += 1
                summaries.append(
                    f"## {pdf_file.stem}\n\n_处理失败_: {str(e)}\n"
                )

        # 8. 生成汇总 Markdown 文件
        md_content = (
            f"# 论文摘要报告\n\n"
            f"**数据集**: {dataset}\n\n"
            f"**处理文件数**: {total} (成功: {success}, 失败: {failed})\n\n"
        )
        md_content += "\n---\n\n".join(summaries)

        md_path = data_path / "summaries.md"
        md_path.write_text(md_content, encoding="utf-8")

        # 9. 返回结果（表格格式，便于前端渲染）
        return {
            "status": "success",
            "output_format": "table",
            "message": f"已处理论文 {success} 篇，摘要报告已保存至 {md_path}",
            "data": {
                "columns": ["字段", "值"],
                "rows": [
                    ["处理文件数", str(success)],
                    ["数据集", dataset],
                    ["总文件数", str(total)],
                    ["失败文件数", str(failed)],
                    ["报告路径", str(md_path)],
                ],
            },
        }

    except Exception as e:
        return {"status": "failed", "message": str(e)}
```
```

======================

## 第 2 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "无法获取数据集信息: API返回: {\"dataset\": {\"id\": \"memmem\", \"name\": \"memmem\", \"version\": \"0.1.0\", \"type\": \"generic\", \"status\": \"active\", \"spec_path\": \"definitions/memmem.md\", \"data_path\": \"/Users/leo/Documents/1重要备份/project/2026SOTABand/SOTABand/data/download/20260726_170036\", \"file_count\": 4, \"total_size\": 4449989, \"formats\": [\"pdf\", \"csv\"], \"tags\": [], \"quality_score\": null, \"created_at\": \"2026-07-26T09:00:50Z\"}}"}

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

def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主函数"""
    try:
        # 1. 参数解析
        dataset = kwargs.get("dataset")
        if not dataset:
            return {"status": "failed", "message": "缺少必填参数: dataset"}

        # 确保 n 为整数
        raw_n = kwargs.get("n", 300)
        try:
            n = int(raw_n)
        except (ValueError, TypeError):
            n = 300

        # 2. 获取数据集信息并获取路径
        api_result = _call_api("api-data-get", name=dataset)
        if not api_result or api_result.get("status") != "success":
            # 更详细的错误消息，便于调试
            if api_result:
                err_detail = json.dumps(api_result, ensure_ascii=False, default=str)
                err_msg = f"API返回: {err_detail[:500]}"
            else:
                err_msg = "API 无响应"
            return {"status": "failed", "message": f"无法获取数据集信息: {err_msg}"}

        dataset_info = api_result.get("dataset")
        if not dataset_info:
            return {"status": "failed", "message": "数据集信息为空"}

        data_path_str = dataset_info.get("path") or dataset_info.get("directory")
        if not data_path_str:
            return {"status": "failed", "message": "数据集路径未配置"}
        data_path = Path(data_path_str)
        if not data_path.exists():
            return {"status": "failed", "message": f"数据集目录不存在: {data_path}"}

        # 3. 扫描 PDF 文件
        pdf_files = sorted(data_path.glob("*.pdf"))
        if not pdf_files:
            return {
                "status": "success",
                "output_format": "table",
                "message": "未发现 PDF 文件，无需处理",
                "data": {
                    "columns": ["字段", "值"],
                    "rows": [
                        ["处理文件数", "0"],
                        ["数据集", dataset],
                    ],
                },
            }

        # 4. 导入 PDF 解析库（由系统保证依赖存在）
        try:
            import pdfplumber
            use_pdfplumber = True
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                use_pdfplumber = False
            except ImportError:
                return {"status": "failed", "message": "PDF 解析库未安装（需要 pdfplumber 或 PyPDF2）"}

        # 5. 获取 DeepSeek API KEY
        key_result = _call_api("api-deepseek-get-key")
        if not key_result or key_result.get("status") != "success":
            if key_result:
                err_detail = json.dumps(key_result, ensure_ascii=False, default=str)
                err_msg = f"API返回: {err_detail[:500]}"
            else:
                err_msg = "API 无响应"
            return {"status": "failed", "message": f"获取 DeepSeek API KEY 失败: {err_msg}"}

        api_key = key_result.get("api_key")
        base_url = key_result.get("base_url")
        model = key_result.get("model", "deepseek-chat")
        if not api_key or not base_url:
            return {"status": "failed", "message": "DeepSeek API 配置不完整（缺少 api_key 或 base_url）"}

        # 6. 初始化 OpenAI 客户端
        try:
            from openai import OpenAI
        except ImportError:
            return {"status": "failed", "message": "openai 库未安装"}

        client = OpenAI(api_key=api_key, base_url=base_url)

        # 7. 遍历处理每一篇 PDF
        total = len(pdf_files)
        success = 0
        failed = 0
        summaries = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                if use_pdfplumber:
                    with pdfplumber.open(str(pdf_file)) as pdf:
                        text = "\n".join(
                            [page.extract_text() or "" for page in pdf.pages]
                        )
                else:
                    reader = PdfReader(str(pdf_file))
                    text = "\n".join(
                        [page.extract_text() or "" for page in reader.pages]
                    )

                # 截断文本，避免超出模型上下文（取前 8000 字符）
                max_text_len = 8000
                if len(text) > max_text_len:
                    text = text[:max_text_len]

                # 调用 DeepSeek 生成摘要
                prompt = (
                    f"请根据以下论文内容，提取论文标题、作者，并生成不超过{n}字的摘要。"
                    f"直接输出结果，格式如下：\n"
                    f"标题：<标题>\n作者：<作者>\n摘要：<摘要>\n\n"
                    f"论文内容：\n{text}"
                )
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1024,
                )
                summary_text = response.choices[0].message.content.strip()

                # 每条结果作为一个独立的二级标题块
                summaries.append(f"## {pdf_file.stem}\n\n{summary_text}\n")
                success += 1

            except Exception as e:
                failed += 1
                summaries.append(
                    f"## {pdf_file.stem}\n\n_处理失败_: {str(e)}\n"
                )

        # 8. 生成汇总 Markdown 文件
        md_content = (
            f"# 论文摘要报告\n\n"
            f"**数据集**: {dataset}\n\n"
            f"**处理文件数**: {total} (成功: {success}, 失败: {failed})\n\n"
        )
        md_content += "\n---\n\n".join(summaries)

        md_path = data_path / "summaries.md"
        md_path.write_text(md_content, encoding="utf-8")

        # 9. 返回结果（表格格式，便于前端渲染）
        return {
            "status": "success",
            "output_format": "table",
            "message": f"已处理论文 {success} 篇，摘要报告已保存至 {md_path}",
            "data": {
                "columns": ["字段", "值"],
                "rows": [
                    ["处理文件数", str(success)],
                    ["数据集", dataset],
                    ["总文件数", str(total)],
                    ["失败文件数", str(failed)],
                    ["报告路径", str(md_path)],
                ],
            },
        }

    except Exception as e:
        return {"status": "failed", "message": str(e)}
=== END CODE ===

=== TEST INPUT ===
{
  "dataset": "memmem",
  "n": "500"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "无法获取数据集信息: API返回: {\"dataset\": {\"id\": \"memmem\", \"name\": \"memmem\", \"version\": \"0.1.0\", \"type\": \"generic\", \"status\": \"active\", \"spec_path\": \"definitions/memmem.md\", \"data_path\": \"/Users/leo/Documents/1重要备份/project/2026SOTABand/SOTABand/data/download/20260726_170036\", \"file_count\": 4, \"total_size\": 4449989, \"formats\": [\"pdf\", \"csv\"], \"tags\": [], \"quality_score\": null, \"created_at\": \"2026-07-26T09:00:50Z\"}}"}
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

def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主函数"""
    try:
        # 1. 参数解析
        dataset = kwargs.get("dataset")
        if not dataset:
            return {"status": "failed", "message": "缺少必填参数: dataset"}

        # 确保 n 为整数
        raw_n = kwargs.get("n", 300)
        try:
            n = int(raw_n)
        except (ValueError, TypeError):
            n = 300

        # 2. 获取数据集信息并获取路径
        api_result = _call_api("api-data-get", name=dataset)
        # 检查是否真的返回了数据集信息
        if not api_result or not api_result.get("dataset"):
            err_detail = json.dumps(api_result, ensure_ascii=False, default=str) if api_result else "API 无响应"
            return {"status": "failed", "message": f"无法获取数据集信息: {err_detail[:500]}"}

        dataset_info = api_result["dataset"]
        # 根据实际返回的字段名 data_path 获取路径
        data_path_str = dataset_info.get("data_path")
        if not data_path_str:
            return {"status": "failed", "message": "数据集路径未配置"}
        data_path = Path(data_path_str)
        if not data_path.exists():
            return {"status": "failed", "message": f"数据集目录不存在: {data_path}"}

        # 3. 扫描 PDF 文件
        pdf_files = sorted(data_path.glob("*.pdf"))
        if not pdf_files:
            return {
                "status": "success",
                "output_format": "table",
                "message": "未发现 PDF 文件，无需处理",
                "data": {
                    "columns": ["字段", "值"],
                    "rows": [
                        ["处理文件数", "0"],
                        ["数据集", dataset],
                    ],
                },
            }

        # 4. 导入 PDF 解析库（由系统保证依赖存在）
        try:
            import pdfplumber
            use_pdfplumber = True
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                use_pdfplumber = False
            except ImportError:
                return {"status": "failed", "message": "PDF 解析库未安装（需要 pdfplumber 或 PyPDF2）"}

        # 5. 获取 DeepSeek API KEY
        key_result = _call_api("api-deepseek-get-key")
        # 不再依赖 status 字段，直接检查必要字段是否存在
        if not key_result or not key_result.get("api_key") or not key_result.get("base_url"):
            err_detail = json.dumps(key_result, ensure_ascii=False, default=str) if key_result else "API 无响应"
            return {"status": "failed", "message": f"获取 DeepSeek API KEY 失败: {err_detail[:500]}"}

        api_key = key_result["api_key"]
        base_url = key_result["base_url"]
        model = key_result.get("model", "deepseek-chat")

        # 6. 初始化 OpenAI 客户端
        try:
            from openai import OpenAI
        except ImportError:
            return {"status": "failed", "message": "openai 库未安装"}

        client = OpenAI(api_key=api_key, base_url=base_url)

        # 7. 遍历处理每一篇 PDF
        total = len(pdf_files)
        success = 0
        failed = 0
        summaries = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                if use_pdfplumber:
                    with pdfplumber.open(str(pdf_file)) as pdf:
                        text = "\n".join(
                            [page.extract_text() or "" for page in pdf.pages]
                        )
                else:
                    reader = PdfReader(str(pdf_file))
                    text = "\n".join(
                        [page.extract_text() or "" for page in reader.pages]
                    )

                # 截断文本，避免超出模型上下文（取前 8000 字符）
                max_text_len = 8000
                if len(text) > max_text_len:
                    text = text[:max_text_len]

                # 调用 DeepSeek 生成摘要
                prompt = (
                    f"请根据以下论文内容，提取论文标题、作者，并生成不超过{n}字的摘要。"
                    f"直接输出结果，格式如下：\n"
                    f"标题：<标题>\n作者：<作者>\n摘要：<摘要>\n\n"
                    f"论文内容：\n{text}"
                )
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1024,
                )
                summary_text = response.choices[0].message.content.strip()

                # 每条结果作为一个独立的二级标题块
                summaries.append(f"## {pdf_file.stem}\n\n{summary_text}\n")
                success += 1

            except Exception as e:
                failed += 1
                summaries.append(
                    f"## {pdf_file.stem}\n\n_处理失败_: {str(e)}\n"
                )

        # 8. 生成汇总 Markdown 文件
        md_content = (
            f"# 论文摘要报告\n\n"
            f"**数据集**: {dataset}\n\n"
            f"**处理文件数**: {total} (成功: {success}, 失败: {failed})\n\n"
        )
        md_content += "\n---\n\n".join(summaries)

        md_path = data_path / "summaries.md"
        md_path.write_text(md_content, encoding="utf-8")

        # 9. 返回结果（表格格式，便于前端渲染）
        return {
            "status": "success",
            "output_format": "table",
            "message": f"已处理论文 {success} 篇，摘要报告已保存至 {md_path}",
            "data": {
                "columns": ["字段", "值"],
                "rows": [
                    ["处理文件数", str(success)],
                    ["数据集", dataset],
                    ["总文件数", str(total)],
                    ["失败文件数", str(failed)],
                    ["报告路径", str(md_path)],
                ],
            },
        }

    except Exception as e:
        return {"status": "failed", "message": str(e)}
```
```

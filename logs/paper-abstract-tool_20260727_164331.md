# 自动调试日志

- **工具**: paper-abstract-tool
- **时间**: 20260727_164331
- **结果**: 成功（共 4 轮）
- **日志条目**: 3 轮

---

## 第 1 轮

### 执行结果

```
stdout:
{"status": "failed", "output_format": "text", "message": "参数 n 必须为正整数", "data": {}}

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

# ── 可选依赖导入 ──
try:
    import PyPDF2
    PDF_READER = "PyPDF2"
except ImportError:
    try:
        import pdfplumber
        PDF_READER = "pdfplumber"
    except ImportError:
        PDF_READER = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _extract_text_from_pdf(file_path: str) -> str:
    """从PDF中提取文本内容，优先使用PyPDF2，其次pdfplumber。"""
    if PDF_READER == "PyPDF2":
        text = ""
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"PyPDF2提取失败: {e}")
    elif PDF_READER == "pdfplumber":
        try:
            with pdfplumber.open(file_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"pdfplumber提取失败: {e}")
    else:
        raise ImportError("未找到PDF解析库（PyPDF2或pdfplumber），请安装依赖。")

def _get_dataset_path(dataset_name: str) -> str:
    """通过API获取数据集目录路径。"""
    resp = _call_api("api-data-get", name=dataset_name)
    # 尝试从可能的字段中提取路径
    path = None
    if isinstance(resp, dict):
        # 可能直接有 'path' 键
        if "path" in resp:
            path = resp["path"]
        # 可能嵌套在 'dataset' 键中
        elif "dataset" in resp and isinstance(resp["dataset"], dict):
            path = resp["dataset"].get("path")
        # 也可能是整个返回就是数据集对象
        if not path and "name" in resp:
            # 不太确定，假设有 'directory' 或 'folder'
            path = resp.get("directory") or resp.get("folder")
    if not path or not isinstance(path, str):
        # 如果失败，给出明确错误
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise ValueError(f"获取数据集目录失败: {msg or '未知错误'}")
    return path

def _get_deepseek_credentials() -> dict:
    """获取DeepSeek API KEY及相关配置。"""
    resp = _call_api("api-deepseek-get-key")
    if not isinstance(resp, dict) or "api_key" not in resp:
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise RuntimeError(f"获取DeepSeek API KEY失败: {msg or '未知错误'}")
    return {
        "provider": resp.get("provider", ""),
        "api_key": resp["api_key"],
        "base_url": resp.get("base_url", ""),
        "model": resp.get("model", "")
    }

def _generate_summary(client, model: str, content: str, max_words: int) -> str:
    """调用大模型生成摘要。"""
    system_prompt = (
        f"你是一个学术论文摘要专家。请根据提供的论文内容，生成不超过{max_words}字的中文摘要。"
        "要求语言简洁、准确，抓住核心观点与结论。如果论文内容较长，请提取其中最核心的部分。"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            temperature=0.3,
            max_tokens=4096  # 足够长的输出
        )
        summary = response.choices[0].message.content.strip()
        # 不强制截断，仅记录超字数警告
        actual_len = len(summary)
        if actual_len > max_words:
            print(f"警告: 生成的摘要长度{actual_len}字，超出限制{max_words}字，已保留原文。")
        return summary
    except Exception as e:
        raise RuntimeError(f"调用DeepSeek API失败: {e}")

def execute(**kwargs) -> dict[str, Any]:
    # 1. 获取输入参数
    dataset = kwargs.get("dataset", "")
    n = kwargs.get("n", 200)
    if not dataset:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必填参数：dataset",
            "data": {}
        }
    if not isinstance(n, int) or n <= 0:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为正整数",
            "data": {}
        }

    try:
        # 2. 获取数据集目录
        data_path = _get_dataset_path(dataset)
        pdf_dir = Path(data_path)
        if not pdf_dir.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"数据集目录不存在: {data_path}",
                "data": {}
            }

        # 3. 收集PDF文件并按名称排序
        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            # 无PDF文件，仍生成摘要文件，内含提示
            summary_content = f"# 论文摘要\n\n数据集 `{dataset}` 目录下未找到PDF文件。\n"
            summary_file = pdf_dir / "summary.md"
            summary_file.write_text(summary_content, encoding="utf-8")
            return {
                "status": "success",
                "output_format": "file",
                "message": f"数据集 `{dataset}` 下无 PDF 文件，已生成空摘要。",
                "data": {
                    "processed_count": 0,
                    "saved_dataset": dataset,
                    "file_path": str(summary_file.resolve())
                }
            }

        # 4. 获取DeepSeek API配置
        credentials = _get_deepseek_credentials()
        if OpenAI is None:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "缺少依赖库 openai，请安装 openai>=1.0.0",
                "data": {}
            }
        client = OpenAI(
            api_key=credentials["api_key"],
            base_url=credentials["base_url"]
        )
        model = credentials["model"]

        # 5. 逐个处理PDF
        summaries = []
        processed_count = 0
        warnings = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                text = _extract_text_from_pdf(str(pdf_file))
                if not text:
                    warnings.append(f"文件 {pdf_file.name} 提取文本为空，已跳过。")
                    continue
                # 调用大模型生成摘要
                summary = _generate_summary(client, model, text, n)
                summaries.append((pdf_file.name, summary))
                processed_count += 1
            except Exception as e:
                # PDF提取失败或API调用失败
                error_msg = f"处理文件 {pdf_file.name} 时出错: {e}"
                # 如果是DeepSeek API属于关键错误，终止整个任务
                if "调用DeepSeek API失败" in str(e):
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": error_msg,
                        "data": {"processed_count": processed_count, "saved_dataset": dataset}
                    }
                else:
                    warnings.append(error_msg)
                    continue

        # 6. 生成合并Markdown
        md_lines = ["# 论文摘要\n"]
        for fname, summary in summaries:
            md_lines.append(f"## {fname}\n\n{summary}\n")
        if warnings:
            md_lines.append("\n## 处理警告\n")
            for w in warnings:
                md_lines.append(f"- {w}\n")
        summary_content = "\n".join(md_lines)

        summary_file = pdf_dir / "summary.md"
        summary_file.write_text(summary_content, encoding="utf-8")

        # 7. 返回结果
        return {
            "status": "success",
            "output_format": "file",
            "message": f"成功处理 {processed_count} 篇论文，摘要已保存至 {summary_file.name}",
            "data": {
                "processed_count": processed_count,
                "saved_dataset": dataset,
                "file_path": str(summary_file.resolve()),
                "warnings": warnings if warnings else None
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"任务执行失败: {e}",
            "data": {}
        }
=== END CODE ===

=== TEST INPUT ===
{
  "dataset": "memmem",
  "n": "500"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "output_format": "text", "message": "参数 n 必须为正整数", "data": {}}
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

# ── 可选依赖导入 ──
try:
    import PyPDF2
    PDF_READER = "PyPDF2"
except ImportError:
    try:
        import pdfplumber
        PDF_READER = "pdfplumber"
    except ImportError:
        PDF_READER = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _extract_text_from_pdf(file_path: str) -> str:
    """从PDF中提取文本内容，优先使用PyPDF2，其次pdfplumber。"""
    if PDF_READER == "PyPDF2":
        text = ""
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"PyPDF2提取失败: {e}")
    elif PDF_READER == "pdfplumber":
        try:
            with pdfplumber.open(file_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"pdfplumber提取失败: {e}")
    else:
        raise ImportError("未找到PDF解析库（PyPDF2或pdfplumber），请安装依赖。")

def _get_dataset_path(dataset_name: str) -> str:
    """通过API获取数据集目录路径。"""
    resp = _call_api("api-data-get", name=dataset_name)
    # 尝试从可能的字段中提取路径
    path = None
    if isinstance(resp, dict):
        # 可能直接有 'path' 键
        if "path" in resp:
            path = resp["path"]
        # 可能嵌套在 'dataset' 键中
        elif "dataset" in resp and isinstance(resp["dataset"], dict):
            path = resp["dataset"].get("path")
        # 也可能是整个返回就是数据集对象
        if not path and "name" in resp:
            # 不太确定，假设有 'directory' 或 'folder'
            path = resp.get("directory") or resp.get("folder")
    if not path or not isinstance(path, str):
        # 如果失败，给出明确错误
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise ValueError(f"获取数据集目录失败: {msg or '未知错误'}")
    return path

def _get_deepseek_credentials() -> dict:
    """获取DeepSeek API KEY及相关配置。"""
    resp = _call_api("api-deepseek-get-key")
    if not isinstance(resp, dict) or "api_key" not in resp:
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise RuntimeError(f"获取DeepSeek API KEY失败: {msg or '未知错误'}")
    return {
        "provider": resp.get("provider", ""),
        "api_key": resp["api_key"],
        "base_url": resp.get("base_url", ""),
        "model": resp.get("model", "")
    }

def _generate_summary(client, model: str, content: str, max_words: int) -> str:
    """调用大模型生成摘要。"""
    system_prompt = (
        f"你是一个学术论文摘要专家。请根据提供的论文内容，生成不超过{max_words}字的中文摘要。"
        "要求语言简洁、准确，抓住核心观点与结论。如果论文内容较长，请提取其中最核心的部分。"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            temperature=0.3,
            max_tokens=4096  # 足够长的输出
        )
        summary = response.choices[0].message.content.strip()
        # 不强制截断，仅记录超字数警告
        actual_len = len(summary)
        if actual_len > max_words:
            print(f"警告: 生成的摘要长度{actual_len}字，超出限制{max_words}字，已保留原文。")
        return summary
    except Exception as e:
        raise RuntimeError(f"调用DeepSeek API失败: {e}")

def execute(**kwargs) -> dict[str, Any]:
    # 1. 获取输入参数
    dataset = kwargs.get("dataset", "")
    n = kwargs.get("n", 200)
    if not dataset:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必填参数：dataset",
            "data": {}
        }
    # 将 n 转换为整数，兼容字符串类型的输入
    try:
        n = int(n)
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为整数",
            "data": {}
        }
    if n <= 0:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为正整数",
            "data": {}
        }

    try:
        # 2. 获取数据集目录
        data_path = _get_dataset_path(dataset)
        pdf_dir = Path(data_path)
        if not pdf_dir.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"数据集目录不存在: {data_path}",
                "data": {}
            }

        # 3. 收集PDF文件并按名称排序
        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            # 无PDF文件，仍生成摘要文件，内含提示
            summary_content = f"# 论文摘要\n\n数据集 `{dataset}` 目录下未找到PDF文件。\n"
            summary_file = pdf_dir / "summary.md"
            summary_file.write_text(summary_content, encoding="utf-8")
            return {
                "status": "success",
                "output_format": "file",
                "message": f"数据集 `{dataset}` 下无 PDF 文件，已生成空摘要。",
                "data": {
                    "processed_count": 0,
                    "saved_dataset": dataset,
                    "file_path": str(summary_file.resolve())
                }
            }

        # 4. 获取DeepSeek API配置
        credentials = _get_deepseek_credentials()
        if OpenAI is None:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "缺少依赖库 openai，请安装 openai>=1.0.0",
                "data": {}
            }
        client = OpenAI(
            api_key=credentials["api_key"],
            base_url=credentials["base_url"]
        )
        model = credentials["model"]

        # 5. 逐个处理PDF
        summaries = []
        processed_count = 0
        warnings = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                text = _extract_text_from_pdf(str(pdf_file))
                if not text:
                    warnings.append(f"文件 {pdf_file.name} 提取文本为空，已跳过。")
                    continue
                # 调用大模型生成摘要
                summary = _generate_summary(client, model, text, n)
                summaries.append((pdf_file.name, summary))
                processed_count += 1
            except Exception as e:
                # PDF提取失败或API调用失败
                error_msg = f"处理文件 {pdf_file.name} 时出错: {e}"
                # 如果是DeepSeek API属于关键错误，终止整个任务
                if "调用DeepSeek API失败" in str(e):
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": error_msg,
                        "data": {"processed_count": processed_count, "saved_dataset": dataset}
                    }
                else:
                    warnings.append(error_msg)
                    continue

        # 6. 生成合并Markdown
        md_lines = ["# 论文摘要\n"]
        for fname, summary in summaries:
            md_lines.append(f"## {fname}\n\n{summary}\n")
        if warnings:
            md_lines.append("\n## 处理警告\n")
            for w in warnings:
                md_lines.append(f"- {w}\n")
        summary_content = "\n".join(md_lines)

        summary_file = pdf_dir / "summary.md"
        summary_file.write_text(summary_content, encoding="utf-8")

        # 7. 返回结果
        return {
            "status": "success",
            "output_format": "file",
            "message": f"成功处理 {processed_count} 篇论文，摘要已保存至 {summary_file.name}",
            "data": {
                "processed_count": processed_count,
                "saved_dataset": dataset,
                "file_path": str(summary_file.resolve()),
                "warnings": warnings if warnings else None
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"任务执行失败: {e}",
            "data": {}
        }
```
```

======================

## 第 2 轮

### 执行结果

```
stdout:
{"status": "failed", "output_format": "text", "message": "任务执行失败: 获取数据集目录失败: 未知错误", "data": {}}

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

# ── 可选依赖导入 ──
try:
    import PyPDF2
    PDF_READER = "PyPDF2"
except ImportError:
    try:
        import pdfplumber
        PDF_READER = "pdfplumber"
    except ImportError:
        PDF_READER = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _extract_text_from_pdf(file_path: str) -> str:
    """从PDF中提取文本内容，优先使用PyPDF2，其次pdfplumber。"""
    if PDF_READER == "PyPDF2":
        text = ""
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"PyPDF2提取失败: {e}")
    elif PDF_READER == "pdfplumber":
        try:
            with pdfplumber.open(file_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"pdfplumber提取失败: {e}")
    else:
        raise ImportError("未找到PDF解析库（PyPDF2或pdfplumber），请安装依赖。")

def _get_dataset_path(dataset_name: str) -> str:
    """通过API获取数据集目录路径。"""
    resp = _call_api("api-data-get", name=dataset_name)
    # 尝试从可能的字段中提取路径
    path = None
    if isinstance(resp, dict):
        # 可能直接有 'path' 键
        if "path" in resp:
            path = resp["path"]
        # 可能嵌套在 'dataset' 键中
        elif "dataset" in resp and isinstance(resp["dataset"], dict):
            path = resp["dataset"].get("path")
        # 也可能是整个返回就是数据集对象
        if not path and "name" in resp:
            # 不太确定，假设有 'directory' 或 'folder'
            path = resp.get("directory") or resp.get("folder")
    if not path or not isinstance(path, str):
        # 如果失败，给出明确错误
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise ValueError(f"获取数据集目录失败: {msg or '未知错误'}")
    return path

def _get_deepseek_credentials() -> dict:
    """获取DeepSeek API KEY及相关配置。"""
    resp = _call_api("api-deepseek-get-key")
    if not isinstance(resp, dict) or "api_key" not in resp:
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise RuntimeError(f"获取DeepSeek API KEY失败: {msg or '未知错误'}")
    return {
        "provider": resp.get("provider", ""),
        "api_key": resp["api_key"],
        "base_url": resp.get("base_url", ""),
        "model": resp.get("model", "")
    }

def _generate_summary(client, model: str, content: str, max_words: int) -> str:
    """调用大模型生成摘要。"""
    system_prompt = (
        f"你是一个学术论文摘要专家。请根据提供的论文内容，生成不超过{max_words}字的中文摘要。"
        "要求语言简洁、准确，抓住核心观点与结论。如果论文内容较长，请提取其中最核心的部分。"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            temperature=0.3,
            max_tokens=4096  # 足够长的输出
        )
        summary = response.choices[0].message.content.strip()
        # 不强制截断，仅记录超字数警告
        actual_len = len(summary)
        if actual_len > max_words:
            print(f"警告: 生成的摘要长度{actual_len}字，超出限制{max_words}字，已保留原文。")
        return summary
    except Exception as e:
        raise RuntimeError(f"调用DeepSeek API失败: {e}")

def execute(**kwargs) -> dict[str, Any]:
    # 1. 获取输入参数
    dataset = kwargs.get("dataset", "")
    n = kwargs.get("n", 200)
    if not dataset:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必填参数：dataset",
            "data": {}
        }
    # 将 n 转换为整数，兼容字符串类型的输入
    try:
        n = int(n)
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为整数",
            "data": {}
        }
    if n <= 0:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为正整数",
            "data": {}
        }

    try:
        # 2. 获取数据集目录
        data_path = _get_dataset_path(dataset)
        pdf_dir = Path(data_path)
        if not pdf_dir.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"数据集目录不存在: {data_path}",
                "data": {}
            }

        # 3. 收集PDF文件并按名称排序
        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            # 无PDF文件，仍生成摘要文件，内含提示
            summary_content = f"# 论文摘要\n\n数据集 `{dataset}` 目录下未找到PDF文件。\n"
            summary_file = pdf_dir / "summary.md"
            summary_file.write_text(summary_content, encoding="utf-8")
            return {
                "status": "success",
                "output_format": "file",
                "message": f"数据集 `{dataset}` 下无 PDF 文件，已生成空摘要。",
                "data": {
                    "processed_count": 0,
                    "saved_dataset": dataset,
                    "file_path": str(summary_file.resolve())
                }
            }

        # 4. 获取DeepSeek API配置
        credentials = _get_deepseek_credentials()
        if OpenAI is None:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "缺少依赖库 openai，请安装 openai>=1.0.0",
                "data": {}
            }
        client = OpenAI(
            api_key=credentials["api_key"],
            base_url=credentials["base_url"]
        )
        model = credentials["model"]

        # 5. 逐个处理PDF
        summaries = []
        processed_count = 0
        warnings = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                text = _extract_text_from_pdf(str(pdf_file))
                if not text:
                    warnings.append(f"文件 {pdf_file.name} 提取文本为空，已跳过。")
                    continue
                # 调用大模型生成摘要
                summary = _generate_summary(client, model, text, n)
                summaries.append((pdf_file.name, summary))
                processed_count += 1
            except Exception as e:
                # PDF提取失败或API调用失败
                error_msg = f"处理文件 {pdf_file.name} 时出错: {e}"
                # 如果是DeepSeek API属于关键错误，终止整个任务
                if "调用DeepSeek API失败" in str(e):
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": error_msg,
                        "data": {"processed_count": processed_count, "saved_dataset": dataset}
                    }
                else:
                    warnings.append(error_msg)
                    continue

        # 6. 生成合并Markdown
        md_lines = ["# 论文摘要\n"]
        for fname, summary in summaries:
            md_lines.append(f"## {fname}\n\n{summary}\n")
        if warnings:
            md_lines.append("\n## 处理警告\n")
            for w in warnings:
                md_lines.append(f"- {w}\n")
        summary_content = "\n".join(md_lines)

        summary_file = pdf_dir / "summary.md"
        summary_file.write_text(summary_content, encoding="utf-8")

        # 7. 返回结果
        return {
            "status": "success",
            "output_format": "file",
            "message": f"成功处理 {processed_count} 篇论文，摘要已保存至 {summary_file.name}",
            "data": {
                "processed_count": processed_count,
                "saved_dataset": dataset,
                "file_path": str(summary_file.resolve()),
                "warnings": warnings if warnings else None
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"任务执行失败: {e}",
            "data": {}
        }
=== END CODE ===

=== TEST INPUT ===
{
  "dataset": "memmem",
  "n": "500"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "output_format": "text", "message": "任务执行失败: 获取数据集目录失败: 未知错误", "data": {}}
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

# ── 可选依赖导入 ──
try:
    import PyPDF2
    PDF_READER = "PyPDF2"
except ImportError:
    try:
        import pdfplumber
        PDF_READER = "pdfplumber"
    except ImportError:
        PDF_READER = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _extract_text_from_pdf(file_path: str) -> str:
    """从PDF中提取文本内容，优先使用PyPDF2，其次pdfplumber。"""
    if PDF_READER == "PyPDF2":
        text = ""
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"PyPDF2提取失败: {e}")
    elif PDF_READER == "pdfplumber":
        try:
            with pdfplumber.open(file_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"pdfplumber提取失败: {e}")
    else:
        raise ImportError("未找到PDF解析库（PyPDF2或pdfplumber），请安装依赖。")

def _get_dataset_path(dataset_name: str) -> str:
    """通过API获取数据集目录路径。"""
    resp = _call_api("api-data-get", name=dataset_name)
    if not isinstance(resp, dict):
        msg = str(resp)
        raise ValueError(f"获取数据集目录失败: 响应格式错误: {msg}")
    # 处理常见 API 返回结构 { 'status': 'success', 'data': {...} }
    if resp.get("status") == "success" and "data" in resp:
        data = resp["data"]
    else:
        data = resp
    path = None
    if isinstance(data, dict):
        # 尝试常见字段名
        for key in ("path", "folder", "directory", "dir"):
            if key in data:
                path = data[key]
                break
        # 也可能嵌套在 'dataset' 中
        if not path and "dataset" in data and isinstance(data["dataset"], dict):
            path = data["dataset"].get("path")
    if not path or not isinstance(path, str):
        raise ValueError(f"获取数据集目录失败: 无法解析路径，响应内容: {json.dumps(resp, default=str, ensure_ascii=False)[:500]}")
    return path

def _get_deepseek_credentials() -> dict:
    """获取DeepSeek API KEY及相关配置。"""
    resp = _call_api("api-deepseek-get-key")
    if not isinstance(resp, dict) or "api_key" not in resp:
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise RuntimeError(f"获取DeepSeek API KEY失败: {msg or '未知错误'}")
    return {
        "provider": resp.get("provider", ""),
        "api_key": resp["api_key"],
        "base_url": resp.get("base_url", ""),
        "model": resp.get("model", "")
    }

def _generate_summary(client, model: str, content: str, max_words: int) -> str:
    """调用大模型生成摘要。"""
    system_prompt = (
        f"你是一个学术论文摘要专家。请根据提供的论文内容，生成不超过{max_words}字的中文摘要。"
        "要求语言简洁、准确，抓住核心观点与结论。如果论文内容较长，请提取其中最核心的部分。"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            temperature=0.3,
            max_tokens=4096  # 足够长的输出
        )
        summary = response.choices[0].message.content.strip()
        # 不强制截断，仅记录超字数警告
        actual_len = len(summary)
        if actual_len > max_words:
            print(f"警告: 生成的摘要长度{actual_len}字，超出限制{max_words}字，已保留原文。")
        return summary
    except Exception as e:
        raise RuntimeError(f"调用DeepSeek API失败: {e}")

def execute(**kwargs) -> dict[str, Any]:
    # 1. 获取输入参数
    dataset = kwargs.get("dataset", "")
    n = kwargs.get("n", 200)
    if not dataset:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必填参数：dataset",
            "data": {}
        }
    # 将 n 转换为整数，兼容字符串类型的输入
    try:
        n = int(n)
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为整数",
            "data": {}
        }
    if n <= 0:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为正整数",
            "data": {}
        }

    try:
        # 2. 获取数据集目录
        data_path = _get_dataset_path(dataset)
        pdf_dir = Path(data_path)
        if not pdf_dir.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"数据集目录不存在: {data_path}",
                "data": {}
            }

        # 3. 收集PDF文件并按名称排序
        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            # 无PDF文件，仍生成摘要文件，内含提示
            summary_content = f"# 论文摘要\n\n数据集 `{dataset}` 目录下未找到PDF文件。\n"
            summary_file = pdf_dir / "summary.md"
            summary_file.write_text(summary_content, encoding="utf-8")
            return {
                "status": "success",
                "output_format": "file",
                "message": f"数据集 `{dataset}` 下无 PDF 文件，已生成空摘要。",
                "data": {
                    "processed_count": 0,
                    "saved_dataset": dataset,
                    "file_path": str(summary_file.resolve())
                }
            }

        # 4. 获取DeepSeek API配置
        credentials = _get_deepseek_credentials()
        if OpenAI is None:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "缺少依赖库 openai，请安装 openai>=1.0.0",
                "data": {}
            }
        client = OpenAI(
            api_key=credentials["api_key"],
            base_url=credentials["base_url"]
        )
        model = credentials["model"]

        # 5. 逐个处理PDF
        summaries = []
        processed_count = 0
        warnings = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                text = _extract_text_from_pdf(str(pdf_file))
                if not text:
                    warnings.append(f"文件 {pdf_file.name} 提取文本为空，已跳过。")
                    continue
                # 调用大模型生成摘要
                summary = _generate_summary(client, model, text, n)
                summaries.append((pdf_file.name, summary))
                processed_count += 1
            except Exception as e:
                # PDF提取失败或API调用失败
                error_msg = f"处理文件 {pdf_file.name} 时出错: {e}"
                # 如果是DeepSeek API属于关键错误，终止整个任务
                if "调用DeepSeek API失败" in str(e):
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": error_msg,
                        "data": {"processed_count": processed_count, "saved_dataset": dataset}
                    }
                else:
                    warnings.append(error_msg)
                    continue

        # 6. 生成合并Markdown
        md_lines = ["# 论文摘要\n"]
        for fname, summary in summaries:
            md_lines.append(f"## {fname}\n\n{summary}\n")
        if warnings:
            md_lines.append("\n## 处理警告\n")
            for w in warnings:
                md_lines.append(f"- {w}\n")
        summary_content = "\n".join(md_lines)

        summary_file = pdf_dir / "summary.md"
        summary_file.write_text(summary_content, encoding="utf-8")

        # 7. 返回结果
        return {
            "status": "success",
            "output_format": "file",
            "message": f"成功处理 {processed_count} 篇论文，摘要已保存至 {summary_file.name}",
            "data": {
                "processed_count": processed_count,
                "saved_dataset": dataset,
                "file_path": str(summary_file.resolve()),
                "warnings": warnings if warnings else None
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"任务执行失败: {e}",
            "data": {}
        }
```
```

======================

## 第 3 轮

### 执行结果

```
stdout:
{"status": "failed", "output_format": "text", "message": "任务执行失败: 获取数据集目录失败: 无法解析路径，响应内容: {\"dataset\": {\"id\": \"memmem\", \"name\": \"memmem\", \"version\": \"0.1.0\", \"type\": \"generic\", \"status\": \"active\", \"spec_path\": \"definitions/memmem.md\", \"data_path\": \"/Users/leo/Documents/1重要备份/project/2026SOTABand/SOTABand/data/download/20260726_170036\", \"file_count\": 4, \"total_size\": 4449989, \"formats\": [\"pdf\", \"csv\"], \"tags\": [], \"quality_score\": null, \"created_at\": \"2026-07-26T09:00:50Z\"}}", "data": {}}

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

# ── 可选依赖导入 ──
try:
    import PyPDF2
    PDF_READER = "PyPDF2"
except ImportError:
    try:
        import pdfplumber
        PDF_READER = "pdfplumber"
    except ImportError:
        PDF_READER = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _extract_text_from_pdf(file_path: str) -> str:
    """从PDF中提取文本内容，优先使用PyPDF2，其次pdfplumber。"""
    if PDF_READER == "PyPDF2":
        text = ""
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"PyPDF2提取失败: {e}")
    elif PDF_READER == "pdfplumber":
        try:
            with pdfplumber.open(file_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"pdfplumber提取失败: {e}")
    else:
        raise ImportError("未找到PDF解析库（PyPDF2或pdfplumber），请安装依赖。")

def _get_dataset_path(dataset_name: str) -> str:
    """通过API获取数据集目录路径。"""
    resp = _call_api("api-data-get", name=dataset_name)
    if not isinstance(resp, dict):
        msg = str(resp)
        raise ValueError(f"获取数据集目录失败: 响应格式错误: {msg}")
    # 处理常见 API 返回结构 { 'status': 'success', 'data': {...} }
    if resp.get("status") == "success" and "data" in resp:
        data = resp["data"]
    else:
        data = resp
    path = None
    if isinstance(data, dict):
        # 尝试常见字段名
        for key in ("path", "folder", "directory", "dir"):
            if key in data:
                path = data[key]
                break
        # 也可能嵌套在 'dataset' 中
        if not path and "dataset" in data and isinstance(data["dataset"], dict):
            path = data["dataset"].get("path")
    if not path or not isinstance(path, str):
        raise ValueError(f"获取数据集目录失败: 无法解析路径，响应内容: {json.dumps(resp, default=str, ensure_ascii=False)[:500]}")
    return path

def _get_deepseek_credentials() -> dict:
    """获取DeepSeek API KEY及相关配置。"""
    resp = _call_api("api-deepseek-get-key")
    if not isinstance(resp, dict) or "api_key" not in resp:
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise RuntimeError(f"获取DeepSeek API KEY失败: {msg or '未知错误'}")
    return {
        "provider": resp.get("provider", ""),
        "api_key": resp["api_key"],
        "base_url": resp.get("base_url", ""),
        "model": resp.get("model", "")
    }

def _generate_summary(client, model: str, content: str, max_words: int) -> str:
    """调用大模型生成摘要。"""
    system_prompt = (
        f"你是一个学术论文摘要专家。请根据提供的论文内容，生成不超过{max_words}字的中文摘要。"
        "要求语言简洁、准确，抓住核心观点与结论。如果论文内容较长，请提取其中最核心的部分。"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            temperature=0.3,
            max_tokens=4096  # 足够长的输出
        )
        summary = response.choices[0].message.content.strip()
        # 不强制截断，仅记录超字数警告
        actual_len = len(summary)
        if actual_len > max_words:
            print(f"警告: 生成的摘要长度{actual_len}字，超出限制{max_words}字，已保留原文。")
        return summary
    except Exception as e:
        raise RuntimeError(f"调用DeepSeek API失败: {e}")

def execute(**kwargs) -> dict[str, Any]:
    # 1. 获取输入参数
    dataset = kwargs.get("dataset", "")
    n = kwargs.get("n", 200)
    if not dataset:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必填参数：dataset",
            "data": {}
        }
    # 将 n 转换为整数，兼容字符串类型的输入
    try:
        n = int(n)
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为整数",
            "data": {}
        }
    if n <= 0:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为正整数",
            "data": {}
        }

    try:
        # 2. 获取数据集目录
        data_path = _get_dataset_path(dataset)
        pdf_dir = Path(data_path)
        if not pdf_dir.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"数据集目录不存在: {data_path}",
                "data": {}
            }

        # 3. 收集PDF文件并按名称排序
        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            # 无PDF文件，仍生成摘要文件，内含提示
            summary_content = f"# 论文摘要\n\n数据集 `{dataset}` 目录下未找到PDF文件。\n"
            summary_file = pdf_dir / "summary.md"
            summary_file.write_text(summary_content, encoding="utf-8")
            return {
                "status": "success",
                "output_format": "file",
                "message": f"数据集 `{dataset}` 下无 PDF 文件，已生成空摘要。",
                "data": {
                    "processed_count": 0,
                    "saved_dataset": dataset,
                    "file_path": str(summary_file.resolve())
                }
            }

        # 4. 获取DeepSeek API配置
        credentials = _get_deepseek_credentials()
        if OpenAI is None:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "缺少依赖库 openai，请安装 openai>=1.0.0",
                "data": {}
            }
        client = OpenAI(
            api_key=credentials["api_key"],
            base_url=credentials["base_url"]
        )
        model = credentials["model"]

        # 5. 逐个处理PDF
        summaries = []
        processed_count = 0
        warnings = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                text = _extract_text_from_pdf(str(pdf_file))
                if not text:
                    warnings.append(f"文件 {pdf_file.name} 提取文本为空，已跳过。")
                    continue
                # 调用大模型生成摘要
                summary = _generate_summary(client, model, text, n)
                summaries.append((pdf_file.name, summary))
                processed_count += 1
            except Exception as e:
                # PDF提取失败或API调用失败
                error_msg = f"处理文件 {pdf_file.name} 时出错: {e}"
                # 如果是DeepSeek API属于关键错误，终止整个任务
                if "调用DeepSeek API失败" in str(e):
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": error_msg,
                        "data": {"processed_count": processed_count, "saved_dataset": dataset}
                    }
                else:
                    warnings.append(error_msg)
                    continue

        # 6. 生成合并Markdown
        md_lines = ["# 论文摘要\n"]
        for fname, summary in summaries:
            md_lines.append(f"## {fname}\n\n{summary}\n")
        if warnings:
            md_lines.append("\n## 处理警告\n")
            for w in warnings:
                md_lines.append(f"- {w}\n")
        summary_content = "\n".join(md_lines)

        summary_file = pdf_dir / "summary.md"
        summary_file.write_text(summary_content, encoding="utf-8")

        # 7. 返回结果
        return {
            "status": "success",
            "output_format": "file",
            "message": f"成功处理 {processed_count} 篇论文，摘要已保存至 {summary_file.name}",
            "data": {
                "processed_count": processed_count,
                "saved_dataset": dataset,
                "file_path": str(summary_file.resolve()),
                "warnings": warnings if warnings else None
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"任务执行失败: {e}",
            "data": {}
        }
=== END CODE ===

=== TEST INPUT ===
{
  "dataset": "memmem",
  "n": "500"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "output_format": "text", "message": "任务执行失败: 获取数据集目录失败: 无法解析路径，响应内容: {\"dataset\": {\"id\": \"memmem\", \"name\": \"memmem\", \"version\": \"0.1.0\", \"type\": \"generic\", \"status\": \"active\", \"spec_path\": \"definitions/memmem.md\", \"data_path\": \"/Users/leo/Documents/1重要备份/project/2026SOTABand/SOTABand/data/download/20260726_170036\", \"file_count\": 4, \"total_size\": 4449989, \"formats\": [\"pdf\", \"csv\"], \"tags\": [], \"quality_score\": null, \"created_at\": \"2026-07-26T09:00:50Z\"}}", "data": {}}
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

# ── 可选依赖导入 ──
try:
    import PyPDF2
    PDF_READER = "PyPDF2"
except ImportError:
    try:
        import pdfplumber
        PDF_READER = "pdfplumber"
    except ImportError:
        PDF_READER = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def _extract_text_from_pdf(file_path: str) -> str:
    """从PDF中提取文本内容，优先使用PyPDF2，其次pdfplumber。"""
    if PDF_READER == "PyPDF2":
        text = ""
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"PyPDF2提取失败: {e}")
    elif PDF_READER == "pdfplumber":
        try:
            with pdfplumber.open(file_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            return text.strip()
        except Exception as e:
            raise RuntimeError(f"pdfplumber提取失败: {e}")
    else:
        raise ImportError("未找到PDF解析库（PyPDF2或pdfplumber），请安装依赖。")

def _get_dataset_path(dataset_name: str) -> str:
    """通过API获取数据集目录路径。"""
    resp = _call_api("api-data-get", name=dataset_name)
    if not isinstance(resp, dict):
        msg = str(resp)
        raise ValueError(f"获取数据集目录失败: 响应格式错误: {msg}")
    
    # 提取数据集信息，兼容多种API返回结构
    dataset_info = None
    if "dataset" in resp and isinstance(resp["dataset"], dict):
        dataset_info = resp["dataset"]
    elif resp.get("status") == "success" and "data" in resp:
        data = resp["data"]
        if isinstance(data, dict):
            if "dataset" in data and isinstance(data["dataset"], dict):
                dataset_info = data["dataset"]
            else:
                dataset_info = data
        else:
            raise ValueError(f"获取数据集目录失败: data字段不是字典，响应内容: {json.dumps(resp, default=str, ensure_ascii=False)[:500]}")
    else:
        dataset_info = resp
    
    # 提取路径，扩展支持 data_path 等常见字段
    path = None
    if isinstance(dataset_info, dict):
        for key in ("data_path", "path", "folder", "directory", "dir"):
            if key in dataset_info:
                path = dataset_info[key]
                break
    if not path or not isinstance(path, str):
        raise ValueError(f"获取数据集目录失败: 无法解析路径，响应内容: {json.dumps(resp, default=str, ensure_ascii=False)[:500]}")
    return path

def _get_deepseek_credentials() -> dict:
    """获取DeepSeek API KEY及相关配置。"""
    resp = _call_api("api-deepseek-get-key")
    if not isinstance(resp, dict) or "api_key" not in resp:
        msg = resp.get("message", "") if isinstance(resp, dict) else str(resp)
        raise RuntimeError(f"获取DeepSeek API KEY失败: {msg or '未知错误'}")
    return {
        "provider": resp.get("provider", ""),
        "api_key": resp["api_key"],
        "base_url": resp.get("base_url", ""),
        "model": resp.get("model", "")
    }

def _generate_summary(client, model: str, content: str, max_words: int) -> str:
    """调用大模型生成摘要。"""
    system_prompt = (
        f"你是一个学术论文摘要专家。请根据提供的论文内容，生成不超过{max_words}字的中文摘要。"
        "要求语言简洁、准确，抓住核心观点与结论。如果论文内容较长，请提取其中最核心的部分。"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            temperature=0.3,
            max_tokens=4096  # 足够长的输出
        )
        summary = response.choices[0].message.content.strip()
        # 不强制截断，仅记录超字数警告
        actual_len = len(summary)
        if actual_len > max_words:
            print(f"警告: 生成的摘要长度{actual_len}字，超出限制{max_words}字，已保留原文。")
        return summary
    except Exception as e:
        raise RuntimeError(f"调用DeepSeek API失败: {e}")

def execute(**kwargs) -> dict[str, Any]:
    # 1. 获取输入参数
    dataset = kwargs.get("dataset", "")
    n = kwargs.get("n", 200)
    if not dataset:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "缺少必填参数：dataset",
            "data": {}
        }
    # 将 n 转换为整数，兼容字符串类型的输入
    try:
        n = int(n)
    except (ValueError, TypeError):
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为整数",
            "data": {}
        }
    if n <= 0:
        return {
            "status": "failed",
            "output_format": "text",
            "message": "参数 n 必须为正整数",
            "data": {}
        }

    try:
        # 2. 获取数据集目录
        data_path = _get_dataset_path(dataset)
        pdf_dir = Path(data_path)
        if not pdf_dir.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"数据集目录不存在: {data_path}",
                "data": {}
            }

        # 3. 收集PDF文件并按名称排序
        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            # 无PDF文件，仍生成摘要文件，内含提示
            summary_content = f"# 论文摘要\n\n数据集 `{dataset}` 目录下未找到PDF文件。\n"
            summary_file = pdf_dir / "summary.md"
            summary_file.write_text(summary_content, encoding="utf-8")
            return {
                "status": "success",
                "output_format": "file",
                "message": f"数据集 `{dataset}` 下无 PDF 文件，已生成空摘要。",
                "data": {
                    "processed_count": 0,
                    "saved_dataset": dataset,
                    "file_path": str(summary_file.resolve())
                }
            }

        # 4. 获取DeepSeek API配置
        credentials = _get_deepseek_credentials()
        if OpenAI is None:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "缺少依赖库 openai，请安装 openai>=1.0.0",
                "data": {}
            }
        client = OpenAI(
            api_key=credentials["api_key"],
            base_url=credentials["base_url"]
        )
        model = credentials["model"]

        # 5. 逐个处理PDF
        summaries = []
        processed_count = 0
        warnings = []

        for pdf_file in pdf_files:
            try:
                # 提取文本
                text = _extract_text_from_pdf(str(pdf_file))
                if not text:
                    warnings.append(f"文件 {pdf_file.name} 提取文本为空，已跳过。")
                    continue
                # 调用大模型生成摘要
                summary = _generate_summary(client, model, text, n)
                summaries.append((pdf_file.name, summary))
                processed_count += 1
            except Exception as e:
                # PDF提取失败或API调用失败
                error_msg = f"处理文件 {pdf_file.name} 时出错: {e}"
                # 如果是DeepSeek API属于关键错误，终止整个任务
                if "调用DeepSeek API失败" in str(e):
                    return {
                        "status": "failed",
                        "output_format": "text",
                        "message": error_msg,
                        "data": {"processed_count": processed_count, "saved_dataset": dataset}
                    }
                else:
                    warnings.append(error_msg)
                    continue

        # 6. 生成合并Markdown
        md_lines = ["# 论文摘要\n"]
        for fname, summary in summaries:
            md_lines.append(f"## {fname}\n\n{summary}\n")
        if warnings:
            md_lines.append("\n## 处理警告\n")
            for w in warnings:
                md_lines.append(f"- {w}\n")
        summary_content = "\n".join(md_lines)

        summary_file = pdf_dir / "summary.md"
        summary_file.write_text(summary_content, encoding="utf-8")

        # 7. 返回结果
        return {
            "status": "success",
            "output_format": "file",
            "message": f"成功处理 {processed_count} 篇论文，摘要已保存至 {summary_file.name}",
            "data": {
                "processed_count": processed_count,
                "saved_dataset": dataset,
                "file_path": str(summary_file.resolve()),
                "warnings": warnings if warnings else None
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"任务执行失败: {e}",
            "data": {}
        }
```
```

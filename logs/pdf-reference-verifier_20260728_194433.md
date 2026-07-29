# 自动调试日志

- **工具**: pdf-reference-verifier
- **时间**: 20260728_194433
- **结果**: 成功（共 5 轮）
- **日志条目**: 4 轮

---

## 第 1 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "Missing module: tools", "error": "ModuleNotFoundError", "missing_module": "tools"}

stderr:

```

### 依赖反馈


[系统已自动安装 tools，请保留现有 import，修复其他代码问题]

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

import fitz  # PyMuPDF
import traceback
import urllib.parse
from googlesearch import search as google_search
from bs4 import BeautifulSoup

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 Google 搜索判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    query = f"{title} {authors}"
    try:
        # advanced=True 返回包含 url, title, description 的 SearchResult 对象
        results = list(google_search(
            query, num_results=5, advanced=True, timeout=10
        ))
        if results:
            # 简单判断：第一个结果的标题或描述中包含标题的主要部分
            main_title = title.strip().lower()
            first = results[0]
            result_title = first.title.lower() if first.title else ""
            result_desc = first.description.lower() if first.description else ""
            # 检查是否包含标题中的关键词（至少包含 3 个词，或包含前三个长单词）
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]  # 如果都很短，取前三个
            match_count = sum(1 for w in title_words if w in result_title or w in result_desc)
            if match_count >= min(2, len(title_words)):
                return "是", first.url
            else:
                # 不够相似，仍然返回第一个 URL 但标记为“否”
                return "否", first.url
        return "否", ""
    except (TimeoutError, ConnectionError, Exception) as e:
        # 网络问题或搜索失败，标记为“无法验证”
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    path = kwargs.get("path", "")
    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
=== END CODE ===

=== TEST INPUT ===
{
  "`path`": "/tmp/sotaband-uploads/2602.04789v3.pdf"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "Missing module: tools", "error": "ModuleNotFoundError", "missing_module": "tools"}
stderr: 
=== END RESULT ===

[系统已自动安装 tools，请保留现有 import，修复其他代码问题]

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

import fitz  # PyMuPDF
import traceback
import urllib.parse
from googlesearch import search as google_search

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 Google 搜索判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    query = f"{title} {authors}"
    try:
        # 修复：移除不支持的 num_results 和 timeout 参数，改用 num
        results = list(google_search(
            query, num=5, advanced=True
        ))
        if results:
            # 简单判断：第一个结果的标题或描述中包含标题的主要部分
            main_title = title.strip().lower()
            first = results[0]
            result_title = first.title.lower() if first.title else ""
            result_desc = first.description.lower() if first.description else ""
            # 检查是否包含标题中的关键词（至少包含 3 个词，或包含前三个长单词）
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]  # 如果都很短，取前三个
            match_count = sum(1 for w in title_words if w in result_title or w in result_desc)
            if match_count >= min(2, len(title_words)):
                return "是", first.url
            else:
                # 不够相似，仍然返回第一个 URL 但标记为“否”
                return "否", first.url
        return "否", ""
    except Exception as e:
        # 网络问题或搜索失败，标记为“无法验证”
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    path = kwargs.get("path", "")
    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
```
```

======================

## 第 2 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "Missing module: googlesearch", "error": "ModuleNotFoundError", "missing_module": "googlesearch"}

stderr:

```

### 依赖反馈


[依赖 googlesearch 安装失败: ERROR: Invalid requirement: 'pip install googlesearch-python': Expected semicolon (after name with no version specifier) or end
    pip install googlesearch-python
        ^
。该依赖不可用，请换替代方案]

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

import fitz  # PyMuPDF
import traceback
import urllib.parse
from googlesearch import search as google_search

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 Google 搜索判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    query = f"{title} {authors}"
    try:
        # 修复：移除不支持的 num_results 和 timeout 参数，改用 num
        results = list(google_search(
            query, num=5, advanced=True
        ))
        if results:
            # 简单判断：第一个结果的标题或描述中包含标题的主要部分
            main_title = title.strip().lower()
            first = results[0]
            result_title = first.title.lower() if first.title else ""
            result_desc = first.description.lower() if first.description else ""
            # 检查是否包含标题中的关键词（至少包含 3 个词，或包含前三个长单词）
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]  # 如果都很短，取前三个
            match_count = sum(1 for w in title_words if w in result_title or w in result_desc)
            if match_count >= min(2, len(title_words)):
                return "是", first.url
            else:
                # 不够相似，仍然返回第一个 URL 但标记为“否”
                return "否", first.url
        return "否", ""
    except Exception as e:
        # 网络问题或搜索失败，标记为“无法验证”
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    path = kwargs.get("path", "")
    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
=== END CODE ===

=== TEST INPUT ===
{
  "`path`": "/tmp/sotaband-uploads/2602.04789v3.pdf"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "Missing module: googlesearch", "error": "ModuleNotFoundError", "missing_module": "googlesearch"}
stderr: 
=== END RESULT ===

[依赖 googlesearch 安装失败: ERROR: Invalid requirement: 'pip install googlesearch-python': Expected semicolon (after name with no version specifier) or end
    pip install googlesearch-python
        ^
。该依赖不可用，请换替代方案]

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

import fitz  # PyMuPDF
import traceback
import urllib.parse

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 CrossRef API 判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    try:
        query = f"{title} {authors}"
        params = {
            "query": query,
            "rows": 1,
        }
        url = "https://api.crossref.org/works"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if items:
            item = items[0]
            doi = item.get("DOI")
            if doi:
                item_url = f"https://doi.org/{doi}"
            else:
                item_url = item.get("URL", "")
            # 简单相似度校验
            main_title = title.strip().lower()
            item_title = ""
            if "title" in item and item["title"]:
                item_title = item["title"][0].lower() if isinstance(item["title"], list) else item["title"].lower()
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]
            match_count = sum(1 for w in title_words if w in item_title)
            if match_count >= min(2, len(title_words)):
                return "是", item_url
            else:
                return "否", item_url
        return "否", ""
    except Exception:
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    path = kwargs.get("path", "")
    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
```
```

======================

## 第 3 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

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

import fitz  # PyMuPDF
import traceback
import urllib.parse

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 CrossRef API 判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    try:
        query = f"{title} {authors}"
        params = {
            "query": query,
            "rows": 1,
        }
        url = "https://api.crossref.org/works"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if items:
            item = items[0]
            doi = item.get("DOI")
            if doi:
                item_url = f"https://doi.org/{doi}"
            else:
                item_url = item.get("URL", "")
            # 简单相似度校验
            main_title = title.strip().lower()
            item_title = ""
            if "title" in item and item["title"]:
                item_title = item["title"][0].lower() if isinstance(item["title"], list) else item["title"].lower()
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]
            match_count = sum(1 for w in title_words if w in item_title)
            if match_count >= min(2, len(title_words)):
                return "是", item_url
            else:
                return "否", item_url
        return "否", ""
    except Exception:
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    path = kwargs.get("path", "")
    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
=== END CODE ===

=== TEST INPUT ===
{
  "`path`": "/tmp/sotaband-uploads/2602.04789v3.pdf"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}
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

import fitz  # PyMuPDF
import traceback
import urllib.parse

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 CrossRef API 判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    try:
        query = f"{title} {authors}"
        params = {
            "query": query,
            "rows": 1,
        }
        url = "https://api.crossref.org/works"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if items:
            item = items[0]
            doi = item.get("DOI")
            if doi:
                item_url = f"https://doi.org/{doi}"
            else:
                item_url = item.get("URL", "")
            # 简单相似度校验
            main_title = title.strip().lower()
            item_title = ""
            if "title" in item and item["title"]:
                item_title = item["title"][0].lower() if isinstance(item["title"], list) else item["title"].lower()
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]
            match_count = sum(1 for w in title_words if w in item_title)
            if match_count >= min(2, len(title_words)):
                return "是", item_url
            else:
                return "否", item_url
        return "否", ""
    except Exception:
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    # 兼容参数名被包裹在反引号中的情况（如 `path`）
    path = kwargs.get("path", "")
    if not path:
        for key in kwargs:
            if key.strip('`"\'') == 'path':
                path = kwargs[key]
                break

    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
```
```

======================

## 第 4 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "PDF 文本提取失败: module 'fitz' has no attribute 'open'", "output_format": "table", "data": {}}

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

import fitz  # PyMuPDF
import traceback
import urllib.parse

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 CrossRef API 判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    try:
        query = f"{title} {authors}"
        params = {
            "query": query,
            "rows": 1,
        }
        url = "https://api.crossref.org/works"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if items:
            item = items[0]
            doi = item.get("DOI")
            if doi:
                item_url = f"https://doi.org/{doi}"
            else:
                item_url = item.get("URL", "")
            # 简单相似度校验
            main_title = title.strip().lower()
            item_title = ""
            if "title" in item and item["title"]:
                item_title = item["title"][0].lower() if isinstance(item["title"], list) else item["title"].lower()
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]
            match_count = sum(1 for w in title_words if w in item_title)
            if match_count >= min(2, len(title_words)):
                return "是", item_url
            else:
                return "否", item_url
        return "否", ""
    except Exception:
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    # 兼容参数名被包裹在反引号中的情况（如 `path`）
    path = kwargs.get("path", "")
    if not path:
        for key in kwargs:
            if key.strip('`"\'') == 'path':
                path = kwargs[key]
                break

    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
=== END CODE ===

=== TEST INPUT ===
{
  "`path`": "/tmp/sotaband-uploads/2602.04789v3.pdf"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "PDF 文本提取失败: module 'fitz' has no attribute 'open'", "output_format": "table", "data": {}}
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

import traceback
import urllib.parse

# ── 常量定义 ──
MAX_PDF_TEXT_LENGTH = 50000  # 最多发送给大模型的字符数，避免超长

def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本，优先使用 PyMuPDF，回退到 pypdf"""
    # 尝试 PyMuPDF (fitz)
    try:
        import fitz
        if hasattr(fitz, 'open'):
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text
        else:
            raise ImportError("fitz.open 不可用")
    except ImportError:
        pass

    # 回退到 pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text
        return text
    except ImportError:
        raise RuntimeError("没有可用的 PDF 库，请安装 PyMuPDF 或 pypdf")

def _extract_references(text: str, api_key: str, base_url: str, model: str) -> list:
    """
    通过 DeepSeek API 解析参考文献列表。
    返回：list[dict]，每个 dict 包含 title, authors, journal/conference 等。
    """
    if not text.strip():
        raise ValueError("PDF 文本为空，无法提取参考文献")

    # 截断过长文本
    if len(text) > MAX_PDF_TEXT_LENGTH:
        text = text[-MAX_PDF_TEXT_LENGTH:]  # 参考文献通常在末尾

    system_prompt = (
        "你是一个学术论文分析助手。请从以下论文全文中提取出参考文献列表。"
        "每条参考文献应该包含标题、所有作者、期刊/会议名称。"
        "请严格按照 JSON 数组格式输出，每个元素是一个对象，包含以下字段："
        "title (字符串), authors (字符串), journal (字符串)。"
        "如果某个字段缺失，请用空字符串填充。不要输出任何其他内容。"
    )
    user_prompt = f"论文全文：\n{text}\n\n请提取参考文献列表。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # 期望 JSON 输出 (OpenAI 兼容)
    }
    api_url = f"{base_url}/chat/completions"
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        # 尝试解析 JSON，可能被包裹在 markdown 代码块内
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        refs = json.loads(content)
        if isinstance(refs, dict):
            # 有时返回的是 {"references": [...]}
            if "references" in refs:
                refs = refs["references"]
            elif "list" in refs:
                refs = refs["list"]
        if not isinstance(refs, list):
            raise ValueError("大模型返回格式不是数组")
        return refs
    except Exception as e:
        raise RuntimeError(f"参考文献解析失败: {str(e)}")

def _search_reference(title: str, authors: str) -> tuple:
    """
    使用 CrossRef API 判断参考文献是否存在。
    返回 (是否存在: str, URL: str)
    """
    try:
        query = f"{title} {authors}"
        params = {
            "query": query,
            "rows": 1,
        }
        url = "https://api.crossref.org/works"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if items:
            item = items[0]
            doi = item.get("DOI")
            if doi:
                item_url = f"https://doi.org/{doi}"
            else:
                item_url = item.get("URL", "")
            # 简单相似度校验
            main_title = title.strip().lower()
            item_title = ""
            if "title" in item and item["title"]:
                item_title = item["title"][0].lower() if isinstance(item["title"], list) else item["title"].lower()
            title_words = [w for w in main_title.split() if len(w) > 3]
            if not title_words:
                title_words = main_title.split()[:3]
            match_count = sum(1 for w in title_words if w in item_title)
            if match_count >= min(2, len(title_words)):
                return "是", item_url
            else:
                return "否", item_url
        return "否", ""
    except Exception:
        return "无法验证", ""

def execute(**kwargs) -> dict[str, Any]:
    """
    主执行函数：验证 PDF 参考文献真实性。
    """
    # 兼容参数名被包裹在反引号中的情况（如 `path`）
    path = kwargs.get("path", "")
    if not path:
        for key in kwargs:
            if key.strip('`"\'') == 'path':
                path = kwargs[key]
                break

    if not path:
        return {"status": "failed", "message": "缺少必填参数：path", "output_format": "table", "data": {}}

    # 解析路径
    try:
        pdf_path = _resolve_path(path)
    except Exception as e:
        return {"status": "failed", "message": f"路径解析失败: {e}", "output_format": "table", "data": {}}

    if not Path(pdf_path).is_file():
        return {"status": "failed", "message": f"文件不存在: {pdf_path}", "output_format": "table", "data": {}}

    # 1. 提取 PDF 全文
    try:
        full_text = _extract_text_from_pdf(pdf_path)
        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件没有可提取的文本内容", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}", "output_format": "table", "data": {}}

    # 2. 获取 DeepSeek API KEY
    try:
        api_resp = _call_api("api-deepseek-get-key")
        if not isinstance(api_resp, dict) or api_resp.get("status") == "failed":
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败", "output_format": "table", "data": {}}
        api_key = api_resp.get("api_key")
        base_url = api_resp.get("base_url")
        model = api_resp.get("model")
        if not all([api_key, base_url, model]):
            return {"status": "failed", "message": "DeepSeek API 凭证不完整", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"调用 API 获取密钥异常: {str(e)}", "output_format": "table", "data": {}}

    # 3. 解析参考文献列表
    try:
        refs = _extract_references(full_text, api_key, base_url, model)
        if not refs:
            return {"status": "failed", "message": "未能从论文中解析出任何参考文献", "output_format": "table", "data": {}}
    except Exception as e:
        return {"status": "failed", "message": f"参考文献解析错误: {str(e)}", "output_format": "table", "data": {}}

    # 4. 逐条验证真实性
    rows = []
    total = len(refs)
    warning_messages = []
    for i, ref in enumerate(refs):
        title = ref.get("title", "").strip()
        authors = ref.get("authors", "").strip()
        journal = ref.get("journal", "").strip()

        # 构造引文信息字符串
        citation_parts = []
        if title:
            citation_parts.append(title)
        if authors:
            citation_parts.append(authors)
        if journal:
            citation_parts.append(journal)
        citation_str = ", ".join(citation_parts)
        if not citation_str:
            citation_str = "(无具体信息)"

        exists, url = _search_reference(title, authors)
        if exists == "无法验证":
            warning_messages.append(f"引文“{citation_str}”搜索验证失败，已标记为无法验证。")

        rows.append([citation_str, exists, url])

    # 构建表格数据
    data = {
        "columns": ["引文信息", "是否存在", "URL"],
        "rows": rows
    }

    message = f"共验证了 {total} 条参考文献。"
    if warning_messages:
        message += " 但部分条目因网络或搜索问题无法验证，详见条目状态。"

    return {
        "status": "success",
        "output_format": "table",
        "message": message,
        "data": data
    }

# 如果直接运行此脚本，可进行简单的本地测试（需替换路径）
if __name__ == "__main__":
    test_path = "./test.pdf"
    res = execute(path=test_path)
    print(json.dumps(res, ensure_ascii=False, indent=2))
```
```

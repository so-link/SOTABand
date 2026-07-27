# 自动调试日志

- **工具**: paper-summarizer
- **时间**: 20260727_195638
- **结果**: 成功（共 3 轮）
- **日志条目**: 2 轮

---

## 第 1 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "参数 n 必须为正整数", "output_format": "text", "data": {}}

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

import logging
from datetime import datetime

# 尝试导入 PDF 库
try:
    import pdfplumber
    USE_PDF_PLUMBER = True
except ImportError:
    USE_PDF_PLUMBER = False
    try:
        from PyPDF2 import PdfReader
        USE_PYPDF2 = True
    except ImportError:
        raise ImportError("需要安装 pdfplumber 或 PyPDF2 来提取 PDF 文本。")

# 尝试导入 OpenAI 库
try:
    from openai import OpenAI
except ImportError:
    raise ImportError("需要安装 openai 库来调用大模型 API。")

# 配置日志输出到 stderr（供调试用）
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """从 PDF 文件中提取全文本"""
    if USE_PDF_PLUMBER:
        with pdfplumber.open(file_path) as pdf:
            text_parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n".join(text_parts)
    else:
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                text_parts.append(txt)
        return "\n".join(text_parts)


def call_llm(client: OpenAI, model: str, messages: list, max_retries: int = 3) -> str:
    """带重试的大模型调用"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=4000,   # 为保证输出完整性，设置较大值
            )
            return response.choices[0].message.content
        except Exception as e:
            last_exc = e
            logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            time.sleep(1 * (attempt + 1))
    raise RuntimeError(f"LLM 调用在 {max_retries} 次重试后仍失败: {last_exc}")


def generate_summary(client: OpenAI, model: str, text: str, n: int) -> str:
    """生成单篇论文的结构化报告"""
    prompt = (
        "你是一个专业的学术论文分析助手。请根据下面的论文全文，生成一份结构化的分析报告（使用中文），"
        f"报告总字数（中文字符数）不超过 {n} 个。"
        "报告必须包含以下部分，并使用严格以下 Markdown 格式输出：\n\n"
        "# 标题\n"
        "- 作者：作者姓名（如果论文中未明确给出，请填写“未知”）\n\n"
        "## 总体内容\n"
        "...\n\n"
        "## 主要贡献\n"
        "...\n\n"
        "## 科研价值\n"
        "...\n\n"
        "## 实验过程及结果\n"
        "...\n\n"
        "请严格按照字数限制，不要超出。\n\n"
        f"论文全文如下：\n\n{text}"
    )
    messages = [
        {"role": "system", "content": "你是一个专业的学术论文分析助手，擅长从论文全文中提炼关键信息。"},
        {"role": "user", "content": prompt}
    ]
    return call_llm(client, model, messages)


def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主执行函数"""
    dataset = kwargs.get("dataset", "")
    n = kwargs.get("n", 0)

    # 参数验证
    if not dataset:
        return {"status": "failed", "message": "缺少必要参数：dataset", "output_format": "text", "data": {}}
    if not isinstance(n, int) or n <= 0:
        return {"status": "failed", "message": "参数 n 必须为正整数", "output_format": "text", "data": {}}

    try:
        # 1. 获取数据集目录
        ds_response = _call_api("api-data-get", name=dataset)
        ds_info = ds_response.get("dataset")
        if not ds_info:
            return {
                "status": "failed",
                "message": f"数据集未找到：{dataset}（API 返回为空）",
                "output_format": "text",
                "data": {}
            }
        data_path = ds_info.get("path")
        if not data_path:
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 未提供有效的存储路径",
                "output_format": "text",
                "data": {}
            }
        data_dir = Path(data_path)
        if not data_dir.exists():
            return {
                "status": "failed",
                "message": f"数据集目录不存在：{data_path}",
                "output_format": "text",
                "data": {}
            }

        # 2. 获取所有 PDF 文件
        pdf_files = sorted(data_dir.rglob("*.pdf"))
        if not pdf_files:
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 中没有找到任何 PDF 文件",
                "output_format": "text",
                "data": {}
            }

        # 3. 获取 DeepSeek API KEY
        key_response = _call_api("api-deepseek-get-key")
        api_key = key_response.get("api_key")
        base_url = key_response.get("base_url", "https://api.deepseek.com/v1")
        model = key_response.get("model", "deepseek-chat")  # 默认值
        if not api_key:
            return {
                "status": "failed",
                "message": "无法获取DeepSeek API KEY",
                "output_format": "text",
                "data": {}
            }

        # 初始化 OpenAI 客户端
        client = OpenAI(api_key=api_key, base_url=base_url)

        reports = []
        success_count = 0
        skipped_files = []

        # 4. 逐个处理 PDF
        for pdf_file in pdf_files:
            logger.info(f"正在处理: {pdf_file.name}")
            try:
                # 提取文本
                pdf_text = extract_text_from_pdf(str(pdf_file))
                if not pdf_text.strip():
                    logger.warning(f"文件 {pdf_file.name} 提取文本为空，跳过。")
                    skipped_files.append(f"{pdf_file.name} (空文本)")
                    continue

                # 生成报告
                report = generate_summary(client, model, pdf_text, n)
                reports.append(report)
                success_count += 1
                logger.info(f"成功生成报告: {pdf_file.name}")

            except Exception as e:
                error_msg = f"{pdf_file.name}: {str(e)}"
                skipped_files.append(error_msg)
                logger.error(f"处理失败: {error_msg}")

        # 5. 合并报告
        merged_markdown = "\n\n".join(reports)

        # 6. 保存到数据集目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"paper_summaries_{dataset}_{timestamp}.md"
        output_file = data_dir / output_filename
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(merged_markdown)

        # 构造返回信息
        message_parts = [f"处理完成，共处理 {len(pdf_files)} 篇论文，成功 {success_count} 篇"]
        if skipped_files:
            message_parts.append(f"，跳过 {len(skipped_files)} 篇（{'; '.join(skipped_files)}）")
        message_parts.append(f"，结果保存在 {output_file}")

        text_output = "".join(message_parts)
        result_data = {
            "text": text_output,
            "file_count": success_count,
            "data_path": str(output_file)
        }

        return {
            "status": "success" if success_count > 0 else "partial_success",
            "message": "部分论文处理失败" if skipped_files and success_count else "全部论文处理成功",
            "output_format": "text",
            "data": result_data
        }

    except Exception as e:
        logger.exception("执行过程发生未预期错误")
        return {
            "status": "failed",
            "message": f"处理失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }
=== END CODE ===

=== TEST INPUT ===
{
  "dataset": "LLM-Memory",
  "n": "600"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "参数 n 必须为正整数", "output_format": "text", "data": {}}
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

import logging
from datetime import datetime

# 尝试导入 PDF 库
try:
    import pdfplumber
    USE_PDF_PLUMBER = True
except ImportError:
    USE_PDF_PLUMBER = False
    try:
        from PyPDF2 import PdfReader
        USE_PYPDF2 = True
    except ImportError:
        raise ImportError("需要安装 pdfplumber 或 PyPDF2 来提取 PDF 文本。")

# 尝试导入 OpenAI 库
try:
    from openai import OpenAI
except ImportError:
    raise ImportError("需要安装 openai 库来调用大模型 API。")

# 配置日志输出到 stderr（供调试用）
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """从 PDF 文件中提取全文本"""
    if USE_PDF_PLUMBER:
        with pdfplumber.open(file_path) as pdf:
            text_parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n".join(text_parts)
    else:
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                text_parts.append(txt)
        return "\n".join(text_parts)


def call_llm(client: OpenAI, model: str, messages: list, max_retries: int = 3) -> str:
    """带重试的大模型调用"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=4000,   # 为保证输出完整性，设置较大值
            )
            return response.choices[0].message.content
        except Exception as e:
            last_exc = e
            logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            time.sleep(1 * (attempt + 1))
    raise RuntimeError(f"LLM 调用在 {max_retries} 次重试后仍失败: {last_exc}")


def generate_summary(client: OpenAI, model: str, text: str, n: int) -> str:
    """生成单篇论文的结构化报告"""
    prompt = (
        "你是一个专业的学术论文分析助手。请根据下面的论文全文，生成一份结构化的分析报告（使用中文），"
        f"报告总字数（中文字符数）不超过 {n} 个。"
        "报告必须包含以下部分，并使用严格以下 Markdown 格式输出：\n\n"
        "# 标题\n"
        "- 作者：作者姓名（如果论文中未明确给出，请填写“未知”）\n\n"
        "## 总体内容\n"
        "...\n\n"
        "## 主要贡献\n"
        "...\n\n"
        "## 科研价值\n"
        "...\n\n"
        "## 实验过程及结果\n"
        "...\n\n"
        "请严格按照字数限制，不要超出。\n\n"
        f"论文全文如下：\n\n{text}"
    )
    messages = [
        {"role": "system", "content": "你是一个专业的学术论文分析助手，擅长从论文全文中提炼关键信息。"},
        {"role": "user", "content": prompt}
    ]
    return call_llm(client, model, messages)


def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主执行函数"""
    dataset = kwargs.get("dataset", "")
    n_raw = kwargs.get("n", 0)

    # 参数验证 - 确保 n 转换为整数
    try:
        n = int(n_raw)
    except (TypeError, ValueError):
        return {"status": "failed", "message": "参数 n 必须为整数", "output_format": "text", "data": {}}
    
    if n <= 0:
        return {"status": "failed", "message": "参数 n 必须为正整数", "output_format": "text", "data": {}}
    
    if not dataset:
        return {"status": "failed", "message": "缺少必要参数：dataset", "output_format": "text", "data": {}}

    try:
        # 1. 获取数据集目录
        ds_response = _call_api("api-data-get", name=dataset)
        ds_info = ds_response.get("dataset")
        if not ds_info:
            return {
                "status": "failed",
                "message": f"数据集未找到：{dataset}（API 返回为空）",
                "output_format": "text",
                "data": {}
            }
        data_path = ds_info.get("path")
        if not data_path:
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 未提供有效的存储路径",
                "output_format": "text",
                "data": {}
            }
        data_dir = Path(data_path)
        if not data_dir.exists():
            return {
                "status": "failed",
                "message": f"数据集目录不存在：{data_path}",
                "output_format": "text",
                "data": {}
            }

        # 2. 获取所有 PDF 文件
        pdf_files = sorted(data_dir.rglob("*.pdf"))
        if not pdf_files:
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 中没有找到任何 PDF 文件",
                "output_format": "text",
                "data": {}
            }

        # 3. 获取 DeepSeek API KEY
        key_response = _call_api("api-deepseek-get-key")
        api_key = key_response.get("api_key")
        base_url = key_response.get("base_url", "https://api.deepseek.com/v1")
        model = key_response.get("model", "deepseek-chat")  # 默认值
        if not api_key:
            return {
                "status": "failed",
                "message": "无法获取DeepSeek API KEY",
                "output_format": "text",
                "data": {}
            }

        # 初始化 OpenAI 客户端
        client = OpenAI(api_key=api_key, base_url=base_url)

        reports = []
        success_count = 0
        skipped_files = []

        # 4. 逐个处理 PDF
        for pdf_file in pdf_files:
            logger.info(f"正在处理: {pdf_file.name}")
            try:
                # 提取文本
                pdf_text = extract_text_from_pdf(str(pdf_file))
                if not pdf_text.strip():
                    logger.warning(f"文件 {pdf_file.name} 提取文本为空，跳过。")
                    skipped_files.append(f"{pdf_file.name} (空文本)")
                    continue

                # 生成报告
                report = generate_summary(client, model, pdf_text, n)
                reports.append(report)
                success_count += 1
                logger.info(f"成功生成报告: {pdf_file.name}")

            except Exception as e:
                error_msg = f"{pdf_file.name}: {str(e)}"
                skipped_files.append(error_msg)
                logger.error(f"处理失败: {error_msg}")

        # 5. 合并报告
        merged_markdown = "\n\n".join(reports)

        # 6. 保存到数据集目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"paper_summaries_{dataset}_{timestamp}.md"
        output_file = data_dir / output_filename
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(merged_markdown)

        # 构造返回信息
        message_parts = [f"处理完成，共处理 {len(pdf_files)} 篇论文，成功 {success_count} 篇"]
        if skipped_files:
            message_parts.append(f"，跳过 {len(skipped_files)} 篇（{'; '.join(skipped_files)}）")
        message_parts.append(f"，结果保存在 {output_file}")

        text_output = "".join(message_parts)
        result_data = {
            "text": text_output,
            "file_count": success_count,
            "data_path": str(output_file)
        }

        return {
            "status": "success" if success_count > 0 else "partial_success",
            "message": "部分论文处理失败" if skipped_files and success_count else "全部论文处理成功",
            "output_format": "text",
            "data": result_data
        }

    except Exception as e:
        logger.exception("执行过程发生未预期错误")
        return {
            "status": "failed",
            "message": f"处理失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }
```
```

======================

## 第 2 轮

### 执行结果

```
stdout:
{"status": "failed", "message": "数据集 'LLM-Memory' 未提供有效的存储路径", "output_format": "text", "data": {}}

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

import logging
from datetime import datetime

# 尝试导入 PDF 库
try:
    import pdfplumber
    USE_PDF_PLUMBER = True
except ImportError:
    USE_PDF_PLUMBER = False
    try:
        from PyPDF2 import PdfReader
        USE_PYPDF2 = True
    except ImportError:
        raise ImportError("需要安装 pdfplumber 或 PyPDF2 来提取 PDF 文本。")

# 尝试导入 OpenAI 库
try:
    from openai import OpenAI
except ImportError:
    raise ImportError("需要安装 openai 库来调用大模型 API。")

# 配置日志输出到 stderr（供调试用）
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """从 PDF 文件中提取全文本"""
    if USE_PDF_PLUMBER:
        with pdfplumber.open(file_path) as pdf:
            text_parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n".join(text_parts)
    else:
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                text_parts.append(txt)
        return "\n".join(text_parts)


def call_llm(client: OpenAI, model: str, messages: list, max_retries: int = 3) -> str:
    """带重试的大模型调用"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=4000,   # 为保证输出完整性，设置较大值
            )
            return response.choices[0].message.content
        except Exception as e:
            last_exc = e
            logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            time.sleep(1 * (attempt + 1))
    raise RuntimeError(f"LLM 调用在 {max_retries} 次重试后仍失败: {last_exc}")


def generate_summary(client: OpenAI, model: str, text: str, n: int) -> str:
    """生成单篇论文的结构化报告"""
    prompt = (
        "你是一个专业的学术论文分析助手。请根据下面的论文全文，生成一份结构化的分析报告（使用中文），"
        f"报告总字数（中文字符数）不超过 {n} 个。"
        "报告必须包含以下部分，并使用严格以下 Markdown 格式输出：\n\n"
        "# 标题\n"
        "- 作者：作者姓名（如果论文中未明确给出，请填写“未知”）\n\n"
        "## 总体内容\n"
        "...\n\n"
        "## 主要贡献\n"
        "...\n\n"
        "## 科研价值\n"
        "...\n\n"
        "## 实验过程及结果\n"
        "...\n\n"
        "请严格按照字数限制，不要超出。\n\n"
        f"论文全文如下：\n\n{text}"
    )
    messages = [
        {"role": "system", "content": "你是一个专业的学术论文分析助手，擅长从论文全文中提炼关键信息。"},
        {"role": "user", "content": prompt}
    ]
    return call_llm(client, model, messages)


def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主执行函数"""
    dataset = kwargs.get("dataset", "")
    n_raw = kwargs.get("n", 0)

    # 参数验证 - 确保 n 转换为整数
    try:
        n = int(n_raw)
    except (TypeError, ValueError):
        return {"status": "failed", "message": "参数 n 必须为整数", "output_format": "text", "data": {}}
    
    if n <= 0:
        return {"status": "failed", "message": "参数 n 必须为正整数", "output_format": "text", "data": {}}
    
    if not dataset:
        return {"status": "failed", "message": "缺少必要参数：dataset", "output_format": "text", "data": {}}

    try:
        # 1. 获取数据集目录
        ds_response = _call_api("api-data-get", name=dataset)
        ds_info = ds_response.get("dataset")
        if not ds_info:
            return {
                "status": "failed",
                "message": f"数据集未找到：{dataset}（API 返回为空）",
                "output_format": "text",
                "data": {}
            }
        data_path = ds_info.get("path")
        if not data_path:
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 未提供有效的存储路径",
                "output_format": "text",
                "data": {}
            }
        data_dir = Path(data_path)
        if not data_dir.exists():
            return {
                "status": "failed",
                "message": f"数据集目录不存在：{data_path}",
                "output_format": "text",
                "data": {}
            }

        # 2. 获取所有 PDF 文件
        pdf_files = sorted(data_dir.rglob("*.pdf"))
        if not pdf_files:
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 中没有找到任何 PDF 文件",
                "output_format": "text",
                "data": {}
            }

        # 3. 获取 DeepSeek API KEY
        key_response = _call_api("api-deepseek-get-key")
        api_key = key_response.get("api_key")
        base_url = key_response.get("base_url", "https://api.deepseek.com/v1")
        model = key_response.get("model", "deepseek-chat")  # 默认值
        if not api_key:
            return {
                "status": "failed",
                "message": "无法获取DeepSeek API KEY",
                "output_format": "text",
                "data": {}
            }

        # 初始化 OpenAI 客户端
        client = OpenAI(api_key=api_key, base_url=base_url)

        reports = []
        success_count = 0
        skipped_files = []

        # 4. 逐个处理 PDF
        for pdf_file in pdf_files:
            logger.info(f"正在处理: {pdf_file.name}")
            try:
                # 提取文本
                pdf_text = extract_text_from_pdf(str(pdf_file))
                if not pdf_text.strip():
                    logger.warning(f"文件 {pdf_file.name} 提取文本为空，跳过。")
                    skipped_files.append(f"{pdf_file.name} (空文本)")
                    continue

                # 生成报告
                report = generate_summary(client, model, pdf_text, n)
                reports.append(report)
                success_count += 1
                logger.info(f"成功生成报告: {pdf_file.name}")

            except Exception as e:
                error_msg = f"{pdf_file.name}: {str(e)}"
                skipped_files.append(error_msg)
                logger.error(f"处理失败: {error_msg}")

        # 5. 合并报告
        merged_markdown = "\n\n".join(reports)

        # 6. 保存到数据集目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"paper_summaries_{dataset}_{timestamp}.md"
        output_file = data_dir / output_filename
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(merged_markdown)

        # 构造返回信息
        message_parts = [f"处理完成，共处理 {len(pdf_files)} 篇论文，成功 {success_count} 篇"]
        if skipped_files:
            message_parts.append(f"，跳过 {len(skipped_files)} 篇（{'; '.join(skipped_files)}）")
        message_parts.append(f"，结果保存在 {output_file}")

        text_output = "".join(message_parts)
        result_data = {
            "text": text_output,
            "file_count": success_count,
            "data_path": str(output_file)
        }

        return {
            "status": "success" if success_count > 0 else "partial_success",
            "message": "部分论文处理失败" if skipped_files and success_count else "全部论文处理成功",
            "output_format": "text",
            "data": result_data
        }

    except Exception as e:
        logger.exception("执行过程发生未预期错误")
        return {
            "status": "failed",
            "message": f"处理失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }
=== END CODE ===

=== TEST INPUT ===
{
  "dataset": "LLM-Memory",
  "n": "600"
}
=== END INPUT ===

=== EXECUTION RESULT ===
stdout: {"status": "failed", "message": "数据集 'LLM-Memory' 未提供有效的存储路径", "output_format": "text", "data": {}}
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

import logging
from datetime import datetime

# 尝试导入 PDF 库
try:
    import pdfplumber
    USE_PDF_PLUMBER = True
except ImportError:
    USE_PDF_PLUMBER = False
    try:
        from PyPDF2 import PdfReader
        USE_PYPDF2 = True
    except ImportError:
        raise ImportError("需要安装 pdfplumber 或 PyPDF2 来提取 PDF 文本。")

# 尝试导入 OpenAI 库
try:
    from openai import OpenAI
except ImportError:
    raise ImportError("需要安装 openai 库来调用大模型 API。")

# 配置日志输出到 stderr（供调试用）
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """从 PDF 文件中提取全文本"""
    if USE_PDF_PLUMBER:
        with pdfplumber.open(file_path) as pdf:
            text_parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n".join(text_parts)
    else:
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                text_parts.append(txt)
        return "\n".join(text_parts)


def call_llm(client: OpenAI, model: str, messages: list, max_retries: int = 3) -> str:
    """带重试的大模型调用"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=4000,   # 为保证输出完整性，设置较大值
            )
            return response.choices[0].message.content
        except Exception as e:
            last_exc = e
            logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            time.sleep(1 * (attempt + 1))
    raise RuntimeError(f"LLM 调用在 {max_retries} 次重试后仍失败: {last_exc}")


def generate_summary(client: OpenAI, model: str, text: str, n: int) -> str:
    """生成单篇论文的结构化报告"""
    prompt = (
        "你是一个专业的学术论文分析助手。请根据下面的论文全文，生成一份结构化的分析报告（使用中文），"
        f"报告总字数（中文字符数）不超过 {n} 个。"
        "报告必须包含以下部分，并使用严格以下 Markdown 格式输出：\n\n"
        "# 标题\n"
        "- 作者：作者姓名（如果论文中未明确给出，请填写“未知”）\n\n"
        "## 总体内容\n"
        "...\n\n"
        "## 主要贡献\n"
        "...\n\n"
        "## 科研价值\n"
        "...\n\n"
        "## 实验过程及结果\n"
        "...\n\n"
        "请严格按照字数限制，不要超出。\n\n"
        f"论文全文如下：\n\n{text}"
    )
    messages = [
        {"role": "system", "content": "你是一个专业的学术论文分析助手，擅长从论文全文中提炼关键信息。"},
        {"role": "user", "content": prompt}
    ]
    return call_llm(client, model, messages)


def execute(**kwargs) -> dict[str, Any]:
    """论文摘要工具主执行函数"""
    dataset = kwargs.get("dataset", "")
    n_raw = kwargs.get("n", 0)

    # 参数验证 - 确保 n 转换为整数
    try:
        n = int(n_raw)
    except (TypeError, ValueError):
        return {"status": "failed", "message": "参数 n 必须为整数", "output_format": "text", "data": {}}
    
    if n <= 0:
        return {"status": "failed", "message": "参数 n 必须为正整数", "output_format": "text", "data": {}}
    
    if not dataset:
        return {"status": "failed", "message": "缺少必要参数：dataset", "output_format": "text", "data": {}}

    try:
        # 1. 获取数据集目录（增加健壮性，适配多种 API 返回格式）
        ds_response = _call_api("api-data-get", name=dataset)
        logger.info(f"API 返回数据: {ds_response}")  # 调试日志

        # 尝试多种方式提取路径
        data_path = None
        if isinstance(ds_response, dict):
            # 优先从 'dataset' 子字段中提取
            ds_info = ds_response.get("dataset")
            if isinstance(ds_info, dict):
                data_path = ds_info.get("path") or ds_info.get("data_path") or ds_info.get("directory") or ds_info.get("storage_path")
            # 如果上述未成功，直接检查顶层字段
            if not data_path:
                data_path = ds_response.get("path") or ds_response.get("data_path") or ds_response.get("directory") or ds_response.get("storage_path")
        
        # 最终 fallback：使用默认 data/<dataset> 目录
        if not data_path:
            data_path = str(_DATA_DIR / dataset)
            logger.warning(f"未从API获取到有效路径，使用默认路径: {data_path}")
        
        data_dir = Path(data_path)
        # 如果路径不存在，再尝试一次默认目录
        if not data_dir.exists():
            fallback_path = str(_DATA_DIR / dataset)
            if fallback_path != data_path:
                logger.warning(f"给定的数据集路径 {data_path} 不存在，尝试使用默认路径: {fallback_path}")
                data_path = fallback_path
                data_dir = Path(data_path)
        
        if not data_dir.exists():
            return {
                "status": "failed",
                "message": f"数据集目录不存在：{data_path}",
                "output_format": "text",
                "data": {}
            }

        # 2. 获取所有 PDF 文件
        pdf_files = sorted(data_dir.rglob("*.pdf"))
        if not pdf_files:
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 中没有找到任何 PDF 文件",
                "output_format": "text",
                "data": {}
            }

        # 3. 获取 DeepSeek API KEY
        key_response = _call_api("api-deepseek-get-key")
        api_key = key_response.get("api_key")
        base_url = key_response.get("base_url", "https://api.deepseek.com/v1")
        model = key_response.get("model", "deepseek-chat")  # 默认值
        if not api_key:
            return {
                "status": "failed",
                "message": "无法获取DeepSeek API KEY",
                "output_format": "text",
                "data": {}
            }

        # 初始化 OpenAI 客户端
        client = OpenAI(api_key=api_key, base_url=base_url)

        reports = []
        success_count = 0
        skipped_files = []

        # 4. 逐个处理 PDF
        for pdf_file in pdf_files:
            logger.info(f"正在处理: {pdf_file.name}")
            try:
                # 提取文本
                pdf_text = extract_text_from_pdf(str(pdf_file))
                if not pdf_text.strip():
                    logger.warning(f"文件 {pdf_file.name} 提取文本为空，跳过。")
                    skipped_files.append(f"{pdf_file.name} (空文本)")
                    continue

                # 生成报告
                report = generate_summary(client, model, pdf_text, n)
                reports.append(report)
                success_count += 1
                logger.info(f"成功生成报告: {pdf_file.name}")

            except Exception as e:
                error_msg = f"{pdf_file.name}: {str(e)}"
                skipped_files.append(error_msg)
                logger.error(f"处理失败: {error_msg}")

        # 5. 合并报告
        merged_markdown = "\n\n".join(reports)

        # 6. 保存到数据集目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"paper_summaries_{dataset}_{timestamp}.md"
        output_file = data_dir / output_filename
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(merged_markdown)

        # 构造返回信息
        message_parts = [f"处理完成，共处理 {len(pdf_files)} 篇论文，成功 {success_count} 篇"]
        if skipped_files:
            message_parts.append(f"，跳过 {len(skipped_files)} 篇（{'; '.join(skipped_files)}）")
        message_parts.append(f"，结果保存在 {output_file}")

        text_output = "".join(message_parts)
        result_data = {
            "text": text_output,
            "file_count": success_count,
            "data_path": str(output_file)
        }

        return {
            "status": "success" if success_count > 0 else "partial_success",
            "message": "部分论文处理失败" if skipped_files and success_count else "全部论文处理成功",
            "output_format": "text",
            "data": result_data
        }

    except Exception as e:
        logger.exception("执行过程发生未预期错误")
        return {
            "status": "failed",
            "message": f"处理失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }
```
```

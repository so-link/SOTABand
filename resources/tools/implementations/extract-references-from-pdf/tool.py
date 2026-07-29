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

# 尝试导入 PDF 文本提取库（优先 pdfplumber，其次 PyPDF2）
try:
    import pdfplumber
    _PDF_BACKEND = "pdfplumber"
except ImportError:
    pdfplumber = None
    try:
        import PyPDF2
        _PDF_BACKEND = "PyPDF2"
    except ImportError:
        PyPDF2 = None
        _PDF_BACKEND = None


def _extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 文件中提取全部文本"""
    full_text = ""
    if _PDF_BACKEND == "pdfplumber" and pdfplumber is not None:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
    elif _PDF_BACKEND == "PyPDF2" and PyPDF2 is not None:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
    else:
        raise RuntimeError("未安装任何 PDF 解析库 (pdfplumber 或 PyPDF2)")
    return full_text.strip()


def execute(**kwargs) -> dict[str, Any]:
    """提取论文 PDF 中的参考文献"""
    try:
        # 1. 获取并校验输入路径
        path = kwargs.get("path", "")
        if not path:
            return {"status": "failed", "message": "参数 'path' 不能为空"}
        file_path = _resolve_path(path)
        if not os.path.isfile(file_path):
            return {"status": "failed", "message": f"文件 {file_path} 未找到"}

        # 2. 提取 PDF 文本
        try:
            full_text = _extract_text_from_pdf(file_path)
        except Exception as e:
            return {"status": "failed", "message": f"PDF 文本提取失败: {str(e)}"}

        if not full_text.strip():
            return {"status": "failed", "message": "PDF 文件内容为空或无法读取"}

        # 3. 获取 DeepSeek API KEY 及配置
        try:
            api_config = _call_api("api-deepseek-get-key")
        except Exception as e:
            return {"status": "failed", "message": f"无法获取 DeepSeek API KEY: {str(e)}"}

        api_key = api_config.get("api_key")
        base_url = api_config.get("base_url", "")
        model = api_config.get("model", "deepseek-chat")  # 回退默认模型

        if not api_key:
            return {"status": "failed", "message": "获取到的 API KEY 为空，请检查系统配置"}
        if not base_url:
            return {"status": "failed", "message": "未获取到 DeepSeek base_url"}

        # 4. 构造 API 请求（OpenAI 兼容端点）
        # 智能处理 base_url，兼容两种常见形式
        base_url = base_url.rstrip("/")
        if base_url.endswith("/v1"):
            url = base_url + "/chat/completions"
        else:
            url = base_url + "/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        prompt = (
            "你是一个专业的文献提取助手。请从以下论文文本中提取所有的参考文献条目。\n"
            "要求：\n"
            "1. 每条参考文献单独一行，保持原始格式（包括编号、作者、标题、期刊、年份等）。\n"
            "2. 不要添加任何多余的解释、评论或额外文字。\n"
            "3. 如果原文没有参考文献，直接返回空内容。"
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"论文全文如下：\n\n{full_text}"}
            ],
            "temperature": 0,
            "max_tokens": 4096
        }

        # 5. 调用大模型
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code != 200:
                error_detail = resp.text[:500]
                return {"status": "failed",
                        "message": f"DeepSeek 模型调用失败 (HTTP {resp.status_code}): {error_detail}"}
            resp_data = resp.json()
        except requests.exceptions.Timeout:
            return {"status": "failed", "message": "DeepSeek API 请求超时"}
        except requests.exceptions.RequestException as e:
            return {"status": "failed", "message": f"DeepSeek API 网络异常: {str(e)}"}
        except Exception as e:
            return {"status": "failed", "message": f"解析 DeepSeek API 响应失败: {str(e)}"}

        # 6. 提取模型返回的参考文献文本
        try:
            content = resp_data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            return {"status": "failed", "message": f"DeepSeek 返回数据结构异常: {str(e)}"}

        if not isinstance(content, str):
            content = str(content)

        # 7. 解析每一条参考文献
        raw_lines = [line.strip() for line in content.split("\n") if line.strip()]
        # 过滤掉明显不是参考文献的说明性文字（如模型有时会附加“参考文献列表：”之类的头）
        references = [line for line in raw_lines
                      if not line.lower().startswith(("参考文献", "references", "ref：", "ref:"))]
        if not references:
            # 如果 content 本身短且可能包含换行，但过滤后没了，则尝试直接使用原始行
            references = raw_lines

        # 8. 构造表格输出
        rows = [[str(idx), ref] for idx, ref in enumerate(references, start=1)]
        if rows:
            message = f"成功提取 {len(rows)} 条参考文献"
        else:
            message = "未检测到参考文献条目"

        return {
            "status": "success",
            "message": message,
            "output_format": "table",
            "data": {
                "columns": ["序号", "参考文献"],
                "rows": rows
            }
        }

    except Exception as e:
        return {"status": "failed", "message": f"未知错误: {str(e)}"}
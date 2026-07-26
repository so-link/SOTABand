
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
from openai import OpenAI

def execute(**kwargs) -> dict[str, Any]:
    """
    论文摘要工具：
    1. 根据数据集名称获取数据集路径
    2. 获取DeepSeek API KEY
    3. 扫描目录下所有PDF，提取文本并生成中文摘要（字数不超过n）
    4. 合并所有摘要返回
    """
    dataset = kwargs.get("dataset", "")
    try:
        n = int(kwargs.get("n", 200))
    except (TypeError, ValueError):
        n = 200

    if not dataset:
        return {
            "status": "failed",
            "message": "参数 dataset 不能为空",
            "output_format": "text",
            "data": {}
        }

    # 1. 获取数据集信息
    try:
        api_result = _call_api("api-data-get", name=dataset)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"调用数据集API失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }

    if api_result.get("status") == "failed":
        return {
            "status": "failed",
            "message": f"数据集获取失败: {api_result.get('message', '未知错误')}",
            "output_format": "text",
            "data": {}
        }

    # ── 健壮的路径提取函数 ──
    def _extract_path_from_result(obj, depth=0):
        """递归提取可能的路径字符串，兼容任何结构"""
        if depth > 10:  # 防止无限递归
            return None
        # 直接字符串返回（可能是路径）
        if isinstance(obj, str) and obj.strip():
            return obj.strip()
        # 字典
        if isinstance(obj, dict):
            # 1. 精确匹配候选键（高优先级）
            exact_keys = [
                "path", "location", "file_path", "dir", "root",
                "folder", "storage_path", "data_path", "absolute_path",
                "full_path", "base_path", "dataset_path"
            ]
            for k in exact_keys:
                val = obj.get(k)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            # 2. 模糊匹配包含 "path" 的键（不区分大小写）
            for k, v in obj.items():
                if isinstance(k, str) and "path" in k.lower() and isinstance(v, str) and v.strip():
                    # 过滤掉明显的URL路径（简单判断）
                    if not v.startswith("http://") and not v.startswith("https://"):
                        return v.strip()
            # 3. 递归搜索子对象
            for v in obj.values():
                extracted = _extract_path_from_result(v, depth+1)
                if extracted:
                    return extracted
        # 列表
        if isinstance(obj, list):
            for item in obj:
                extracted = _extract_path_from_result(item, depth+1)
                if extracted:
                    return extracted
        return None

    data_path = _extract_path_from_result(api_result)
    
    if not data_path:
        return {
            "status": "failed",
            "message": "未能从数据集信息中提取路径",
            "output_format": "text",
            "data": {}
        }

    # 确保路径为绝对路径
    data_path = _resolve_path(data_path)
    pdf_dir = Path(data_path)

    if not pdf_dir.exists():
        return {
            "status": "failed",
            "message": f"数据集目录不存在: {data_path}",
            "output_format": "text",
            "data": {}
        }

    # 2. 扫描PDF文件
    pdf_files = sorted(pdf_dir.rglob("*.pdf"))
    if not pdf_files:
        return {
            "status": "failed",
            "message": f"目录 {data_path} 下没有找到PDF文件",
            "output_format": "text",
            "data": {}
        }

    # 3. 获取DeepSeek API KEY
    try:
        key_result = _call_api("api-deepseek-get-key")
    except Exception as e:
        return {
            "status": "failed",
            "message": f"获取DeepSeek API KEY失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }

    if key_result.get("status") == "failed":
        return {
            "status": "failed",
            "message": f"API KEY获取失败: {key_result.get('message', '')}",
            "output_format": "text",
            "data": {}
        }

    api_key = key_result.get("api_key", "")
    base_url = key_result.get("base_url", "https://api.deepseek.com")
    model = key_result.get("model", "deepseek-chat")

    if not api_key:
        return {
            "status": "failed",
            "message": "未获取到有效的API KEY",
            "output_format": "text",
            "data": {}
        }

    # 初始化OpenAI客户端
    client = OpenAI(api_key=api_key, base_url=base_url)

    summaries = []
    # 4. 处理每个PDF
    for idx, pdf_file in enumerate(pdf_files, 1):
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_file))
            full_text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"

            if not full_text.strip():
                logging.warning(f"文件 {pdf_file.name} 未能提取到文本内容，跳过")
                continue

            # 限制输入长度，避免token溢出（简单截断）
            max_chars = 12000  # 适当限制，根据模型上下文调整
            if len(full_text) > max_chars:
                full_text = full_text[:max_chars]

            # 构造prompt
            prompt = f"""请为以下论文生成一段中文摘要，字数不超过{n}字。要求内容准确、简洁，涵盖核心观点和主要结论。

论文内容：
{full_text}

中文摘要："""

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一位专业的学术论文摘要撰写助手。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2048
            )
            summary = response.choices[0].message.content.strip()
            summaries.append(f"【论文{idx}：{pdf_file.name}】\n{summary}\n")

        except Exception as e:
            logging.warning(f"处理文件 {pdf_file.name} 时出错: {str(e)}")
            summaries.append(f"【论文{idx}：{pdf_file.name}】\n（摘要生成失败：{str(e)}）\n")

    if not summaries:
        return {
            "status": "failed",
            "message": "所有PDF文件处理失败，未能生成任何摘要",
            "output_format": "text",
            "data": {}
        }

    merged_text = "\n\n".join(summaries)

    return {
        "status": "success",
        "message": f"成功处理 {len(pdf_files)} 个PDF文件，生成摘要 {len(summaries)} 篇",
        "output_format": "text",
        "data": {"text": merged_text}
    }

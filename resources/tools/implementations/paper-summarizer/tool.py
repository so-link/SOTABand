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

import re
import logging
from PyPDF2 import PdfReader
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_fixed

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def execute(**kwargs) -> dict[str, Any]:
    """
    论文摘要工具：对数据集中的PDF论文生成分析报告并汇总。
    输入：dataset (string, 必填), n (integer, 必填)
    输出：{"status", "output_format", "message", "data": {"text", "processed_count", "dataset"}}
    """
    try:
        # 1. 参数验证
        dataset = kwargs.get("dataset")
        n_raw = kwargs.get("n")

        if not dataset or not isinstance(dataset, str) or not dataset.strip():
            return {
                "status": "failed",
                "message": "参数 'dataset' 必须是非空字符串",
                "output_format": "text",
                "data": {}
            }
        dataset = dataset.strip()

        try:
            n = int(n_raw)
        except (TypeError, ValueError):
            return {
                "status": "failed",
                "message": "参数 'n' 必须是一个整数",
                "output_format": "text",
                "data": {}
            }
        if n <= 0:
            return {
                "status": "failed",
                "message": "参数 'n' 必须是正整数",
                "output_format": "text",
                "data": {}
            }

        # 2. 获取数据集目录
        logger.info(f"正在获取数据集 '{dataset}' 的信息...")
        ds_result = _call_api("api-data-get", name=dataset)

        if not isinstance(ds_result, dict):
            return {
                "status": "failed",
                "message": f"数据集 '{dataset}' 不存在或接口返回格式异常",
                "output_format": "text",
                "data": {}
            }

        # 检查API返回状态
        if ds_result.get("status") == "failed":
            err_msg = ds_result.get("message", "未知错误")
            return {
                "status": "failed",
                "message": f"获取数据集 '{dataset}' 信息失败: {err_msg}",
                "output_format": "text",
                "data": {}
            }

        # 解析数据集信息（兼容多种返回格式）
        ds_data = ds_result.get("data")
        if isinstance(ds_data, dict) and ds_data:
            ds_info = ds_data
        else:
            ds_info = ds_result.get("dataset") or ds_result

        if not isinstance(ds_info, dict):
            raw_preview = str(ds_result)[:300]
            return {
                "status": "failed",
                "message": (
                    f"数据集 '{dataset}' 的接口返回数据格式异常，"
                    f"期望字典类型。原始返回片段: {raw_preview}"
                ),
                "output_format": "text",
                "data": {}
            }

        # 尝试多种可能的路径字段名（分别从 ds_info 和 ds_result 中查找）
        # 添加了 "data_path" 以支持实际 API 返回的字段名
        path_keys = [
            "path", "storage_path", "directory", "location",
            "folder", "dir", "data_dir", "absolute_path", "filepath",
            "data_path"
        ]
        data_path = ""
        for key in path_keys:
            data_path = ds_info.get(key) or ds_result.get(key)
            if data_path:
                break

        # 如果得到相对路径，转为绝对路径
        if data_path:
            data_path = _resolve_path(data_path)

        # 如果API未提供路径，则尝试推测常见数据存放位置
        if not data_path:
            candidates = [
                _DATA_DIR / "datasets" / dataset,
                _DATA_DIR / dataset,
                _DOWNLOADS_DIR / dataset,
                _PROJECT_ROOT / "downloads" / dataset,
                _PROJECT_ROOT / "datasets" / dataset,
                _PROJECT_ROOT / "data" / "papers" / dataset,
                _PROJECT_ROOT / "papers" / dataset,
            ]
            for cand in candidates:
                if cand.is_dir():
                    data_path = str(cand)
                    logger.info(f"找到数据集路径: {data_path}")
                    break

        if not data_path or not os.path.isdir(data_path):
            api_info_preview = json.dumps(ds_info, default=str, ensure_ascii=False)[:300]
            return {
                "status": "failed",
                "message": (
                    f"数据集 '{dataset}' 的存储路径不存在或无法访问。"
                    f"已尝试默认位置: {_DATA_DIR}/datasets/{dataset}, {_DATA_DIR}/{dataset}, "
                    f"{_DOWNLOADS_DIR}/{dataset} 等，均未找到。"
                    f"API返回信息: {api_info_preview}"
                ),
                "output_format": "text",
                "data": {}
            }

        # 3. 获取DeepSeek API KEY
        logger.info("正在获取DeepSeek API密钥...")
        key_result = _call_api("api-deepseek-get-key")
        if not isinstance(key_result, dict) or "api_key" not in key_result:
            return {
                "status": "failed",
                "message": "无法获取DeepSeek API KEY，请检查配置",
                "output_format": "text",
                "data": {}
            }
        api_key = key_result["api_key"]
        base_url = key_result.get("base_url", "https://api.deepseek.com")
        model = key_result.get("model", "deepseek-chat")

        # 初始化OpenAI客户端
        client = OpenAI(api_key=api_key, base_url=base_url)

        # 4. 扫描PDF文件
        pdf_files = [f for f in os.listdir(data_path) if f.lower().endswith(".pdf")]
        if not pdf_files:
            return {
                "status": "success",
                "message": f"数据集 '{dataset}' 目录下没有PDF文件，未处理任何论文",
                "output_format": "text",
                "data": {
                    "text": f"未找到PDF文件，处理0篇论文。",
                    "processed_count": 0,
                    "dataset": dataset
                }
            }

        # 5. 处理每篇论文
        processed_count = 0
        markdown_parts = []
        for pdf_file in sorted(pdf_files):
            pdf_path = os.path.join(data_path, pdf_file)
            logger.info(f"开始处理: {pdf_file}")
            try:
                text = extract_pdf_text(pdf_path)
                if not text.strip():
                    raise ValueError("提取到的文本为空")

                summary_md = generate_paper_summary(client, model, text, n)
                markdown_parts.append(summary_md)
                processed_count += 1
                logger.info(f"成功生成摘要: {pdf_file}")
            except Exception as e:
                logger.warning(f"处理 {pdf_file} 失败: {e}")
                title_from_name = os.path.splitext(pdf_file)[0]
                markdown_parts.append(
                    f"# {title_from_name}\n\n"
                    f"**生成失败**：{str(e)[:200]}\n\n---\n"
                )

        # 6. 合并写入 summary.md
        summary_path = os.path.join(data_path, "summary.md")
        full_md = "\n\n".join(markdown_parts)
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(full_md)

        # 7. 返回结果
        msg = f"成功处理 {processed_count} 篇论文，汇总报告已保存至数据集目录"
        return {
            "status": "success",
            "output_format": "text",
            "message": msg,
            "data": {
                "text": msg,
                "processed_count": processed_count,
                "dataset": dataset
            }
        }

    except Exception as e:
        logger.exception("工具执行异常")
        return {
            "status": "failed",
            "message": f"工具执行出错: {str(e)}",
            "output_format": "text",
            "data": {}
        }


def extract_pdf_text(file_path: str) -> str:
    """从PDF文件中提取文本内容"""
    text_parts = []
    try:
        reader = PdfReader(file_path)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    except Exception as e:
        logger.error(f"PyPDF2提取失败: {e}")
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
        except ImportError:
            raise RuntimeError("PDF提取失败，且未安装pdfplumber库") from e
        except Exception as e2:
            raise RuntimeError(f"PDF提取失败（PyPDF2和pdfplumber均失败）: {e2}") from e2
    return "\n".join(text_parts)


def generate_paper_summary(client: OpenAI, model: str, paper_text: str, max_words: int) -> str:
    """
    调用DeepSeek生成论文分析报告。
    返回Markdown格式的章节内容，包含论文标题作为一级标题，各分点作为二级标题。
    """
    truncated_text = paper_text[:8000]

    prompt = f"""你是一位资深的学术论文分析专家。请仔细阅读以下论文内容，生成一份详细的结构化分析报告，要求如下：
1. 报告总字数不超过{max_words}个汉字。
2. 报告使用Markdown格式，首先提取论文的标题和作者，将其作为一级标题（# 标题）。
3. 然后依次分析以下方面，每个方面作为一个二级标题（##）：
   - 总体内容
   - 主要贡献
   - 科研价值
   - 实验过程与结果
   在每个二级标题下，简明扼要地阐述相关内容。
4. 如果无法判断某个方面的信息，请注明“未提及”。
5. 最终返回完整的Markdown文本，不要包含其他无关内容。

论文内容如下：
---
{truncated_text}
---
"""
    @retry(stop=stop_after_attempt(2), wait=wait_fixed(2))
    def _call_with_retry():
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个专业的学术论文分析助手，输出纯Markdown格式文本。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("DeepSeek返回内容为空")
        return content

    return _call_with_retry()
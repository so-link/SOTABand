# === SOTABand 工具标准模板 ===
import os, sys, json, time, base64, uuid
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

# ── LLM 调用辅助（统一走系统配置的 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。"""
    import asyncio
    from core.llm.client import create_llm_client
    client = create_llm_client()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.chat(messages, **kwargs))
        loop.run_until_complete(client.aclose())
        return result
    finally:
        loop.close()

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

from datetime import datetime

# ── PDF 文本提取 ──
def _extract_pdf_text(filepath: str) -> str:
    """尝试使用可用的库提取 PDF 文本内容"""
    # 优先使用 PyPDF2
    try:
        import PyPDF2
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            texts = [page.extract_text() or '' for page in reader.pages]
        return '\n'.join(texts)
    except ImportError:
        pass
    except Exception:
        pass

    # 其次尝试 pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            texts = [page.extract_text() or '' for page in pdf.pages]
        return '\n'.join(texts)
    except ImportError:
        pass
    except Exception:
        pass

    # 没有可用的 PDF 解析库
    return None


def execute(**kwargs) -> dict[str, Any]:
    # ── 1. 获取参数 ──
    path_input = kwargs.get("path", "")
    conf = kwargs.get("conf", "")
    dataset_name = kwargs.get("dataset", "")

    if not path_input or not conf or not dataset_name:
        return {
            "status": "failed",
            "message": "缺少必要参数: path, conf, dataset 均不能为空"
        }

    # ── 2. 解析并校验论文路径 ──
    pdf_path_str = _resolve_path(path_input)
    pdf_path = Path(pdf_path_str)
    if not pdf_path.exists():
        return {
            "status": "failed",
            "message": f"论文文件不存在: {pdf_path}"
        }

    # ── 3. 提取 PDF 文本 ──
    pdf_text = _extract_pdf_text(str(pdf_path))
    if not pdf_text:
        # 如果提取失败，提示用户安装依赖或使用文本方式
        return {
            "status": "failed",
            "message": (
                "无法提取PDF文本，请安装 PyPDF2 或 pdfplumber 库，"
                "或提供纯文本版本的论文。"
            )
        }

    # ── 4. 构造评审提示 ──
    system_prompt = f"""你是一位经验丰富的{conf}会议审稿人。请对下面提供的论文内容进行详细、严格、建设性的评审。
评审过程必须使用中文，并涵盖以下10个方面：

1. 总体创新性评估  
2. 结构、形式与实验完整性评估  
3. 摘要、引言与章节安排的一致性及衔接连贯性  
4. 总体思路章节中的技术点与贡献、与后续章节的对应关系  
5. 每个公式的目的说明与符号解释的清晰度  
6. 消融实验完整性及核心参数敏感性分析  
7. 实验对比baseline的先进性、完备性  
8. 可读性与可复现性评估  
9. 针对上述重点提供详细的修改建议和指引  
10. 按照{conf}会议的评审标准进行最终综合评分，并给出判定（如强力接受、接受、弱接受、拒绝等）

请以Markdown格式输出完整的评审意见，包含清晰的标题、分段和评分信息。"""

    # ── 5. 清理 PDF 文本中的 surrogate 字符，避免 JSON 序列化报错 ──
    pdf_text = pdf_text.encode('utf-8', errors='replace').decode('utf-8')

    # ── 6. 调用系统统一 LLM 生成评审意见（跟随全局 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下是待审论文的文本内容：\n\n{pdf_text}"}
        ]
        review_text = _llm_chat(messages, temperature=0.3, max_tokens=16384)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"LLM 调用失败: {str(e)}"
        }

    # ── 7. 保存评审报告 ──
    paper_stem = pdf_path.stem
    safe_stem = "".join(c if c.isalnum() or c in "._- " else "_" for c in paper_stem)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_id = f"{safe_stem}_{timestamp_str}" if safe_stem else timestamp_str
    report_dir = _DATA_DIR / "papers" / dir_id
    report_dir.mkdir(parents=True, exist_ok=True)

    review_filename = f"review_{timestamp_str}.md"
    review_filepath = report_dir / review_filename

    try:
        with open(review_filepath, "w", encoding="utf-8") as f:
            f.write(review_text)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"评审报告保存失败: {str(e)}"
        }

    # ── 8. 数据集注册 ──
    reg_status_msg = ""
    try:
        get_result = _call_api("api-data-get", name=dataset_name)
        dataset_exists = bool(get_result.get("dataset"))
        if not dataset_exists:
            dataset_id = f"papers-{dir_id}"
            data_path_abs = str(report_dir.resolve())
            file_count = 1
            total_size = os.path.getsize(review_filepath)
            formats = ["md"]
            raw_md = f"论文 `{pdf_path.name}` 的修改建议数据集，目标会议：{conf}"

            register_result = _call_api("api-data-register",
                id=dataset_id,
                name=dataset_name,
                raw_md=raw_md,
                data_path=data_path_abs,
                file_count=file_count,
                total_size=total_size,
                formats=formats
            )
            if not register_result.get("dataset_id"):
                reg_status_msg = f"数据集注册失败：{register_result.get('message', '未知错误')}"
            else:
                reg_status_msg = "数据集注册成功"
        else:
            reg_status_msg = "数据集已存在，无需注册"
    except Exception as e:
        reg_status_msg = f"数据集注册异常: {str(e)}"

    # ── 9. 构造返回结果 ──
    final_message = f"评审完成并保存至 {review_filepath}"
    if reg_status_msg:
        final_message += f"；{reg_status_msg}"

    return {
        "status": "success",
        "message": final_message,
        "output_format": "text",
        "data": {
            "text": review_text
        }
    }


# 仅用于本地测试
if __name__ == "__main__":
    res = execute(path="/path/to/paper.pdf", conf="NeurIPS", dataset="paper_reviews")
    print(json.dumps(res, ensure_ascii=False, indent=2))
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

# === 头部结束，以下由实现生成 ===

def execute(**kwargs) -> dict[str, Any]:
    """
    论文评审工具

    输入参数:
        path (str): 待评审论文PDF文件路径
        conf (str): 投稿会议名称
        dataset (str): 评审结果输出数据集名称

    返回:
        dict: 包含 status, message, output_format, data 的结果
    """
    pdf_path_raw = kwargs.get("path", "")
    conf = kwargs.get("conf", "")
    dataset_name = kwargs.get("dataset", "")

    if not pdf_path_raw or not conf or not dataset_name:
        return {
            "status": "failed",
            "message": "输入参数不完整，需要 path, conf, dataset",
            "output_format": "text",
            "data": {}
        }

    # 1. 解析论文路径，检查文件是否存在
    try:
        pdf_path = _resolve_path(pdf_path_raw)
        if not os.path.isfile(pdf_path):
            return {
                "status": "failed",
                "message": f"论文文件不存在: {pdf_path}",
                "output_format": "text",
                "data": {}
            }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"路径解析错误: {str(e)}",
            "output_format": "text",
            "data": {}
        }

    # 2. 获取DeepSeek API KEY
    try:
        api_info = _call_api("api-deepseek-get-key")
        api_key = api_info.get("api_key")
        base_url = api_info.get("base_url")
        model = api_info.get("model")
        if not api_key or not base_url or not model:
            return {
                "status": "failed",
                "message": "DeepSeek API KEY 获取不完整",
                "output_format": "text",
                "data": {}
            }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"获取API KEY失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }

    # 3. 提取 PDF 文本（替代文件上传，避免 404 错误）
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        return {
            "status": "failed",
            "message": "缺少 PyPDF2 库，无法提取 PDF 文本。请安装 pip install PyPDF2",
            "output_format": "text",
            "data": {}
        }

    try:
        reader = PdfReader(pdf_path)
        pdf_text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pdf_text += page_text + "\n"
        if not pdf_text.strip():
            return {
                "status": "failed",
                "message": "PDF 文件无法提取文本内容，可能为扫描件或图片型PDF",
                "output_format": "text",
                "data": {}
            }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"PDF 文本提取失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }

    # 4. 构建评审提示词（包含论文文本）
    system_prompt = "你是一位严格的学术论文评审专家。请按照用户要求对论文进行细致评审，使用中文输出评审意见。"
    user_prompt = f"""请对提供的论文进行详细评审，会议为 {conf}。评审要点包括：
（1）总体创新性评估；
（2）结构、形式、实验完整性评估；
（3）摘要、简介与章节安排的一致性和连贯性；
（4）总体思路章节与前后文的对应关系；
（5）公式目的及符号解释的清晰度；
（6）消融实验与核心参数敏感性分析；
（7）实验充分性及对比基线方法的先进性与完备性；
（8）论文可读性与可复现性；
（9）针对上述要点给出详细批改建议与指引；
（10）按 {conf} 的评审标准进行最终评议与打分判定。"""
    full_user_message = f"{user_prompt}\n\n论文全文如下：\n{pdf_text}"

    review_content = ""
    report_path = ""

    try:
        # 5. 调用 DeepSeek 文本补全（不再上传文件）
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_user_message}
        ]
        # 处理 base_url：确保路径以 /v1 结尾，避免重复拼接导致 404
        api_base = base_url.rstrip('/')
        if not api_base.endswith('/v1'):
            api_base += '/v1'
        chat_url = f"{api_base}/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 8000,
            "stream": False
        }
        resp = requests.post(
            chat_url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=300
        )
        if resp.status_code != 200:
            raise Exception(f"DeepSeek 模型调用失败: {resp.status_code} {resp.text[:500]}")
        resp_data = resp.json()
        choices = resp_data.get("choices", [])
        if not choices:
            raise Exception("模型返回空响应")
        review_content = choices[0].get("message", {}).get("content", "")
        if not review_content:
            raise Exception("评审内容为空")

    except Exception as e:
        return {
            "status": "failed",
            "message": f"DeepSeek API 请求失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }

    # 6. 保存评审报告到 ./data/papers/{XXXX}/
    try:
        paper_name = Path(pdf_path).stem
        ts = int(time.time())
        folder_name = f"{paper_name}_{ts}"
        report_dir = _DATA_DIR / "papers" / folder_name
        report_dir.mkdir(parents=True, exist_ok=True)

        report_file = report_dir / "report.md"
        full_report = f"# 论文评审报告\n\n**会议**: {conf}\n**论文**: {os.path.basename(pdf_path)}\n\n{review_content}\n"
        report_file.write_text(full_report, encoding="utf-8")
        report_path = str(report_file)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"保存报告文件失败: {str(e)}",
            "output_format": "text",
            "data": {}
        }

    # 7. 注册/检查数据集
    try:
        # 先查询数据集是否存在
        ds_info = _call_api("api-data-get", name=dataset_name)
        ds_exists = bool(ds_info.get("dataset"))

        if not ds_exists:
            # 统计目录信息
            total_files = 0
            total_size = 0
            formats = set()
            for root, dirs, files in os.walk(report_dir):
                for file in files:
                    fpath = os.path.join(root, file)
                    total_files += 1
                    total_size += os.path.getsize(fpath)
                    ext = os.path.splitext(file)[1].lower()
                    if ext:
                        formats.add(ext)
            formats_list = sorted(list(formats))

            # 生成数据集id（简单使用名称）
            ds_id = dataset_name
            # 调用注册API
            reg_result = _call_api(
                "api-data-register",
                id=ds_id,
                name=dataset_name,
                raw_md=f"# {dataset_name}\n\n论文评审结果数据集，会议：{conf}",
                data_path=str(report_dir),
                file_count=total_files,
                total_size=total_size,
                formats=formats_list
            )
            # 忽略注册错误的详细处理，仅记录
    except Exception as e:
        # 数据集操作失败不影响主流程，但返回警告信息
        return {
            "status": "success",
            "message": f"评审完成，但数据集注册失败: {str(e)}",
            "output_format": "text",
            "data": {
                "text": review_content,
                "report_path": report_path
            }
        }

    return {
        "status": "success",
        "message": "论文评审完成，报告已保存",
        "output_format": "text",
        "data": {
            "text": review_content,
            "report_path": report_path
        }
    }
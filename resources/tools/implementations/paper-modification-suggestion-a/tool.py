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

import hashlib
import base64
from datetime import datetime
from openai import OpenAI

def execute(**kwargs) -> dict[str, Any]:
    try:
        # 1. 获取输入参数
        path = kwargs.get("path")
        conf = kwargs.get("conf")
        dataset = kwargs.get("dataset")
        if not path or not conf or not dataset:
            return {"status": "failed", "message": "缺少必填参数 path / conf / dataset"}

        # 解析论文绝对路径
        paper_path = Path(_resolve_path(path))
        if not paper_path.exists():
            return {"status": "failed", "message": f"论文文件不存在: {paper_path}"}
        if not paper_path.suffix.lower() == ".pdf":
            return {"status": "failed", "message": "仅支持PDF格式的论文文件"}

        # 2. 前置工具调用
        # 调用专利检索与翻译工具（忽略返回值，仅完成前置处理）
        _call_tool("专利检索与翻译工具", path=str(paper_path))

        # 3. 获取 DeepSeek API KEY
        key_info = _call_api("api-deepseek-get-key")
        api_key = key_info.get("api_key")
        base_url = key_info.get("base_url")
        model = key_info.get("model")
        if not api_key or not model:
            return {"status": "failed", "message": "获取DeepSeek API KEY失败，缺少必要字段"}

        # 4. 论文文本提取
        extracted_text = ""
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(paper_path))
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    extracted_text += page_text + "\n"
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(str(paper_path))
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        extracted_text += page_text + "\n"
            except ImportError:
                return {"status": "failed", "message": "缺少PDF解析库(pypdf/PyPDF2)依赖，无法提取论文文本"}
        if not extracted_text.strip():
            return {"status": "failed", "message": "无法从PDF中提取文本内容，文件可能为扫描版或受保护"}

        # 5. 构建评审 prompts
        system_prompt = (
            "你是一位资深的学术论文审稿专家，请以中文对以下论文进行全面、详细的评审。"
            "请严格覆盖以下10个评估方面：\n"
            "(1) 总体创新性评估\n"
            "(2) 结构、形式、实验完整性评估\n"
            "(3) 摘要、简介与章节安排的一致性与连贯性\n"
            "(4) 总体思路章节与技术点的一致性\n"
            "(5) 公式目的与符号解释\n"
            "(6) 消融实验与核心参数敏感性分析\n"
            "(7) 实验充分性与 baseline 先进完备性\n"
            "(8) 可读性与可复现性\n"
            "(9) 针对上述重点的详细批改建议和指引\n"
            f"(10) 按{conf}评审标准进行评议和打分\n"
            "最后请给出总体意见和最终分数（百分制）。输出格式使用 Markdown，结构清晰。"
        )
        user_prompt = f"会议/期刊：{conf}\n论文内容如下：\n\n{extracted_text}"

        # 6. 调用 DeepSeek 模型
        client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=8000
        )
        review_text = response.choices[0].message.content
        if not review_text:
            return {"status": "failed", "message": "模型返回的评审意见为空"}

        # 7. 保存评审报告
        # 基于论文文件内容计算哈希（前8位）
        file_content = paper_path.read_bytes()
        hash_hex = hashlib.sha256(file_content).hexdigest()[:8]
        paper_subdir = _DATA_DIR / "papers" / hash_hex
        paper_subdir.mkdir(parents=True, exist_ok=True)

        # 生成时间戳
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        review_filename = f"review_{timestamp}.md"
        review_path = paper_subdir / review_filename
        review_path.write_text(review_text, encoding="utf-8")

        # 8. 数据集注册
        # 先查询数据集是否存在
        dataset_info = _call_api("api-data-get", name=dataset)
        dataset_exists = bool(dataset_info and dataset_info.get("dataset"))

        if not dataset_exists:
            # 准备注册参数
            file_count = 1  # 当前只写入了这一个评审文件
            total_size = review_path.stat().st_size
            formats = ["md"]
            raw_md = f"本数据集包含 {conf} 论文评审报告，原始论文路径: {paper_path}，评审时间: {timestamp}"

            reg_result = _call_api(
                "api-data-register",
                id=dataset,                   # 使用数据集名称作为 ID（可自行调整）
                name=dataset,
                raw_md=raw_md,
                data_path=str(paper_subdir),
                file_count=file_count,
                total_size=total_size,
                formats=formats
            )
            dataset_reg_ok = bool(reg_result and reg_result.get("dataset_id"))
            dataset_msg = "数据集注册成功" if dataset_reg_ok else "数据集注册失败"
        else:
            dataset_msg = "数据集已存在，跳过注册"

        # 9. 返回结果
        final_message = f"评审完成并成功保存报告；{dataset_msg}"
        return {
            "status": "success",
            "output_format": "text",
            "message": final_message,
            "data": {"review": review_text}
        }

    except Exception as e:
        return {"status": "failed", "message": f"工具执行异常: {str(e)}"}
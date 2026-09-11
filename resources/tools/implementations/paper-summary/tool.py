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

def _extract_text_from_pdf(pdf_path: Path) -> str:
    """提取 PDF 全文文本"""
    import pdfplumber
    text_parts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts).strip()


def _build_prompt(paper_text: str, filename: str) -> str:
    """构建发送给 DeepSeek 的分析 prompt"""
    # 避免超出上下文，截取前 100000 字符
    truncated_text = paper_text[:100000]
    return f"""你是资深的学术论文分析专家。请对以下论文内容进行系统分析，并严格输出 JSON 格式，不要包含任何 Markdown 代码块或额外说明。

JSON 必须包含以下字段：
- title: 论文标题（字符串）
- abstract: 中文摘要，200-300字（字符串）
- keywords: 关键词列表（数组，3-6个）
- core_innovation: 核心创新点（字符串）
- main_results: 主要实验结果（字符串）
- potential: 发展潜力与应用前景（字符串）

论文文件名：{filename}

论文内容：
{truncated_text}
"""


def _parse_llm_json(content: str) -> dict:
    """从 LLM 返回内容中解析 JSON"""
    text = content.strip()
    if text.startswith("```"):
        # 去除代码块标记
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        elif text.lower().startswith("json\n"):
            text = text[5:]
        text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        # 查找第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _analyze_single_paper(client, model: str, text: str, filename: str) -> dict:
    """调用 DeepSeek 分析单篇论文，失败自动重试 2 次"""
    prompt = _build_prompt(text, filename)
    messages = [
        {"role": "system", "content": "你是一个学术论文分析助手，始终输出合法 JSON。"},
        {"role": "user", "content": prompt},
    ]

    last_error = None
    max_retries = 2  # 初始尝试失败后再重试 2 次，共 3 次
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=100000,
                temperature=0.2,
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("DeepSeek 返回空内容")
            return _parse_llm_json(content)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"DeepSeek API 调用失败（已重试 {max_retries} 次）: {last_error}")


def execute(**kwargs) -> dict[str, Any]:
    try:
        # 1. 获取输入参数
        dir_path_input = kwargs.get("dir_path", "")
        if not dir_path_input:
            return {"status": "failed", "message": "参数 dir_path 不能为空", "output_format": "text", "data": {}}

        dir_path = Path(_resolve_path(dir_path_input))
        if not dir_path.exists() or not dir_path.is_dir():
            return {"status": "failed", "message": f"目录不存在: {dir_path}", "output_format": "text", "data": {}}

        # 2. 扫描 PDF 文件
        pdf_files = sorted(dir_path.glob("*.pdf"))
        if not pdf_files:
            return {"status": "failed", "message": f"目录下未找到 PDF 文件: {dir_path}", "output_format": "text", "data": {}}

        # 3. 获取 DeepSeek API KEY
        try:
            api_result = _call_api("api-deepseek-get-key")
        except Exception as e:
            return {"status": "failed", "message": f"获取 DeepSeek API KEY 失败: {e}", "output_format": "text", "data": {}}

        # 兼容 API 返回可能嵌套在 data 中的情况
        api_data = api_result.get("data", api_result) if isinstance(api_result, dict) else {}
        api_key = api_data.get("api_key", "") or api_result.get("api_key", "")
        base_url = api_data.get("base_url", "") or api_result.get("base_url", "https://api.deepseek.com/v1")
        model = api_data.get("model", "") or api_result.get("model", "deepseek-v4-pro")

        if not api_key:
            return {"status": "failed", "message": "获取 DeepSeek API KEY 失败：api_key 为空", "output_format": "text", "data": {}}

        # 4. 初始化 OpenAI 兼容客户端
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)

        # 5. 逐篇分析
        table_rows = []
        report_sections = []
        report_sections.append("# 论文摘要报告\n")
        report_sections.append(f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report_sections.append(f"- 论文目录: {dir_path}")
        report_sections.append(f"- 论文总数: {len(pdf_files)}\n")

        success_count = 0
        failed_files = []

        for idx, pdf_file in enumerate(pdf_files, start=1):
            filename = pdf_file.name
            try:
                # 提取文本
                paper_text = _extract_text_from_pdf(pdf_file)
                if not paper_text:
                    raise ValueError("PDF 文本提取为空")

                # 调用模型分析
                analysis = _analyze_single_paper(client, model, paper_text, filename)

                title = analysis.get("title", "未知标题")
                abstract = analysis.get("abstract", "")
                keywords_list = analysis.get("keywords", [])
                keywords = ", ".join(keywords_list) if isinstance(keywords_list, list) else str(keywords_list)
                core_innovation = analysis.get("core_innovation", "")
                main_results = analysis.get("main_results", "")
                potential = analysis.get("potential", "")

                table_rows.append([str(idx), filename, title, abstract, keywords])
                success_count += 1

                report_sections.append(f"## {idx}. {filename}\n")
                report_sections.append(f"- **标题**: {title}")
                report_sections.append(f"- **摘要**: {abstract}")
                report_sections.append(f"- **关键词**: {keywords}")
                report_sections.append(f"- **核心创新点**: {core_innovation}")
                report_sections.append(f"- **主要实验结果**: {main_results}")
                report_sections.append(f"- **发展潜力**: {potential}\n")

            except Exception as e:
                # 单篇处理失败：跳过并在报告中标注
                reason = str(e)
                failed_files.append({"filename": filename, "reason": reason})
                table_rows.append([str(idx), filename, "摘要失败", reason, ""])
                report_sections.append(f"## {idx}. {filename}\n")
                report_sections.append(f"- **状态**: 摘要失败")
                report_sections.append(f"- **原因**: {reason}\n")

        # 6. 生成 Markdown 报告
        report_path = dir_path / "paper_summary_report.md"
        report_path.write_text("\n".join(report_sections), encoding="utf-8")

        # 7. 组织返回结果
        data = {
            "columns": ["序号", "文件名", "标题", "摘要", "关键词"],
            "rows": table_rows,
        }
        message = f"报告已保存: {report_path}；成功分析 {success_count}/{len(pdf_files)} 篇论文"
        if failed_files:
            message += f"，失败 {len(failed_files)} 篇"

        return {
            "status": "success",
            "output_format": "table",
            "message": message,
            "data": data,
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": str(e),
            "output_format": "text",
            "data": {},
        }
# === SOTABand 工具标准模板 ===
import os, sys, json, time, re
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

def _fail(msg: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "message": msg,
        "output_format": "text",
        "data": {"text": ""},
    }


def _extract_pdf_text(file_path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("缺少 pdfplumber 依赖") from exc

    parts = []
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text)
    except Exception as exc:
        raise RuntimeError(f"PDF 解析失败: {exc}") from exc
    return "\n".join(parts).strip()


def _extract_docx_text(file_path: Path) -> str:
    try:
        import zipfile
        from xml.etree import ElementTree as ET

        with zipfile.ZipFile(str(file_path)) as z:
            xml_content = z.read("word/document.xml")
        root = ET.fromstring(xml_content)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = root.findall(".//w:p", ns)
        lines = []
        for p in paragraphs:
            texts = [t.text or "" for t in p.findall(".//w:t", ns)]
            lines.append("".join(texts))
        return "\n".join(lines).strip()
    except Exception:
        return ""


def _extract_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(file_path)
    if suffix == ".docx":
        return _extract_docx_text(file_path)
    return file_path.read_text(encoding="utf-8", errors="ignore").strip()


def _extract_json_response(text: str):
    text = text.strip()
    # Remove code fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    start = min(starts)
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i != -1]
    if not ends:
        return None
    end = max(ends)
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def _call_deepseek(api_key: str, base_url: str, model: str, system_prompt: str, user_prompt: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("缺少 openai 依赖") from exc

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=100000,
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise RuntimeError("DeepSeek API 返回空内容")
    return content.strip()


def _build_benchmark_prompt(text: str) -> str:
    return f"""请从以下评估基准文件中提取五大核心基准维度：

1. 原料来源基准
2. 生产工艺流程基准
3. 关键质量指标阈值（纯度/含量/水分/重金属/塑化剂等）
4. 动物源性管控基准
5. 食品专项安全基准（农残、塑化剂合规要求）

请对每个维度提取具体的比对字段及对应的基准要求（一个维度可以包含多个字段）。
输出 JSON，格式如下：
{{"dimensions":[{{"dimension":"原料来源基准","fields":[{{"field":"原料产地","requirement":"..."}}]}}]}}
只输出 JSON，不要输出任何额外解释。

评估基准文件内容：
\"\"\"
{text}
\"\"\"
"""


def _build_assessment_prompt(benchmark_info: dict, supplier_text: str, supplier_name: str) -> str:
    benchmark_json = json.dumps(benchmark_info, ensure_ascii=False, indent=2)
    return f"""请根据以下五大核心基准维度及字段要求，评估供应商原材料资料与基准的吻合程度。

供应商名称：{supplier_name}

要求：
1. 对每个维度给出 1~100 分。
2. 对每个维度给出详细分析。
3. 给出总体评分和总体摘要。

输出 JSON，格式如下：
{{
  "supplier": "供应商名称",
  "overall_score": 85,
  "summary": "总体评价",
  "dimensions": [
    {{"dimension": "原料来源基准", "score": 90, "analysis": "该供应商原料来源符合..."}}
  ]
}}
只输出 JSON，不要输出任何额外解释。

基准要求 JSON：
{benchmark_json}

供应商资料文本：
\"\"\"
{supplier_text}
\"\"\"
"""


def _score_level(score) -> str:
    try:
        s = int(score)
    except Exception:
        return "未知"
    if s >= 90:
        return "优秀"
    if s >= 75:
        return "良好"
    if s >= 60:
        return "合格"
    return "不合格"


def _generate_markdown(results: list, errors: list, csv_name: str) -> str:
    lines = []
    lines.append("# 供应商原材料基准吻合度评估报告")
    lines.append("")
    lines.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 基准比对字段 CSV：{csv_name}")
    lines.append("")

    sorted_results = sorted(results, key=lambda x: x.get("overall_score", 0), reverse=True)

    lines.append("## 一、供应商评估分数列表")
    lines.append("")
    lines.append("| 排名 | 供应商 | 评分 | 等级 |")
    lines.append("|------|--------|------|------|")
    for i, r in enumerate(sorted_results, 1):
        score = r.get("overall_score", 0)
        lines.append(f"| {i} | {r.get('supplier', '')} | {score} | {_score_level(score)} |")

    if errors:
        lines.append("")
        lines.append("## 二、解析失败供应商说明")
        lines.append("")
        for e in errors:
            lines.append(f"- **{e['file']}**：{e['error']}")

    lines.append("")
    lines.append("## 三、详细分析报告")
    lines.append("")

    for i, r in enumerate(sorted_results, 1):
        supplier = r.get("supplier", "")
        score = r.get("overall_score", 0)
        lines.append(f"### {i}. {supplier} (评分: {score})")
        lines.append("")
        if r.get("summary"):
            lines.append(f"**总体评价**：{r['summary']}")
            lines.append("")
        dimensions = r.get("dimensions", [])
        if dimensions:
            lines.append("**五大核心基准维度对照分析**")
            lines.append("")
            lines.append("| 维度 | 评分 | 分析 |")
            lines.append("|------|------|------|")
            for d in dimensions:
                dim_name = d.get("dimension", "")
                dim_score = d.get("score", "")
                analysis = d.get("analysis", "")
                lines.append(f"| {dim_name} | {dim_score} | {analysis} |")
            lines.append("")
    return "\n".join(lines)


def execute(**kwargs) -> dict[str, Any]:
    try:
        filepath = kwargs.get("filepath", "")
        dirpath = kwargs.get("dirpath", "")

        if not filepath:
            return _fail("filepath 参数缺失")
        if not dirpath:
            return _fail("dirpath 参数缺失")

        benchmark_path = Path(filepath)
        if not benchmark_path.exists() or not benchmark_path.is_file():
            return _fail(f"filepath 不存在: {filepath}")

        supplier_dir = Path(dirpath)
        if not supplier_dir.exists() or not supplier_dir.is_dir():
            return _fail(f"目录路径不存在或无权限: {dirpath}")

        benchmark_text = _extract_text(benchmark_path)
        if not benchmark_text:
            return _fail("基准文件解析失败：未提取到文本")

        # 1. 获取 DeepSeek API Key
        key_result = _call_api("api-deepseek-get-key")
        api_key = key_result.get("api_key")
        base_url = key_result.get("base_url")
        model = key_result.get("model") or "deepseek-chat"
        if not api_key or not base_url:
            return _fail("获取 DeepSeek API Key 失败：API 返回不完整")

        # 2. 解析基准文件，生成基准比对字段
        benchmark_response = _call_deepseek(
            api_key,
            base_url,
            model,
            "你是食品供应链合规与质量评估专家，擅长从标准文件中提取结构化基准要求。",
            _build_benchmark_prompt(benchmark_text),
        )
        benchmark_data = _extract_json_response(benchmark_response)
        if not benchmark_data or not isinstance(benchmark_data.get("dimensions"), list) or not benchmark_data["dimensions"]:
            return _fail("基准文件解析失败：未提取到有效基准字段")

        dimensions = benchmark_data["dimensions"]
        rows = []
        for dim in dimensions:
            dim_name = dim.get("dimension", "")
            fields = dim.get("fields", [])
            if isinstance(fields, list):
                for f in fields:
                    if isinstance(f, dict):
                        rows.append({
                            "dimension": dim_name,
                            "field": f.get("field", ""),
                            "requirement": f.get("requirement", ""),
                        })
        if not rows:
            return _fail("基准文件解析失败：未提取到有效基准字段")

        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("缺少 pandas 依赖") from exc

        csv_path = supplier_dir / f"benchmark_comparison_fields_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        # 3. 扫描供应商 PDF
        pdf_files = sorted([
            p for p in supplier_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf"
        ])
        if not pdf_files:
            return _fail("目录下无供应商 PDF 文件")

        # 4. 评估每个供应商
        evaluation_results = []
        parse_errors = []

        for pdf_file in pdf_files:
            try:
                supplier_text = _extract_pdf_text(pdf_file)
                if not supplier_text:
                    raise RuntimeError("PDF 文本提取为空")

                assessment_response = _call_deepseek(
                    api_key,
                    base_url,
                    model,
                    "你是供应商原材料合规评估专家。请基于基准要求，对供应商资料进行吻合度评估，严格输出 JSON。",
                    _build_assessment_prompt(
                        {"dimensions": dimensions},
                        supplier_text,
                        pdf_file.stem,
                    ),
                )
                assessment_data = _extract_json_response(assessment_response)
                if not assessment_data:
                    raise RuntimeError("评估结果 JSON 解析失败")

                assessment_data["file"] = pdf_file.name
                assessment_data["supplier"] = pdf_file.stem
                evaluation_results.append(assessment_data)
            except Exception as exc:
                parse_errors.append({"file": pdf_file.name, "error": str(exc)})

        if not evaluation_results:
            return _fail("所有供应商 PDF 解析或评估失败")

        # 5. 生成 Markdown 报告
        markdown_content = _generate_markdown(
            evaluation_results,
            parse_errors,
            csv_path.name,
        )
        report_path = supplier_dir / f"supplier_benchmark_evaluation_report_{time.strftime('%Y%m%d_%H%M%S')}.md"
        report_path.write_text(markdown_content, encoding="utf-8")

        return {
            "status": "success",
            "message": f"评估完成，共 {len(evaluation_results)} 个供应商，报告已保存至 {report_path}，基准 CSV：{csv_path}",
            "output_format": "text",
            "data": {"text": markdown_content},
        }

    except Exception as exc:
        return {"status": "failed", "message": str(exc), "output_format": "text", "data": {"text": ""}}
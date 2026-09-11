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

import base64


def _extract_pdf_text(pdf_path: Path) -> str:
    """尝试从 PDF 中提取文本，失败则返回空字符串"""
    # 尝试 PyPDF2
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(str(pdf_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text
    except Exception:
        pass

    # 尝试 pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text
    except Exception:
        pass

    # 尝试 pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if text.strip():
            return text
    except Exception:
        pass

    return ""


def _call_deepseek_chat(base_url: str, api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    """调用 DeepSeek 兼容接口，优先使用 openai SDK，失败时回退 requests"""
    # 优先尝试 openai SDK
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=100000,
            temperature=0.3
        )
        if resp and resp.choices and resp.choices[0].message:
            content = resp.choices[0].message.content
            if content:
                return content
    except Exception:
        pass

    # 回退到 requests 调用 OpenAI 兼容接口
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url
    elif url.endswith("/v1"):
        url = url + "/chat/completions"
    else:
        url = url + "/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 100000,
        "temperature": 0.3
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _get_deepseek_config() -> dict:
    """获取 DeepSeek API 配置"""
    result = _call_api("api-deepseek-get-key")
    if not isinstance(result, dict):
        return {}

    if result.get("status") == "failed":
        return {}

    # 兼容 result.data 嵌套与直接字段两种返回形式
    data = result.get("data", result)
    if not isinstance(data, dict):
        data = result

    return {
        "api_key": data.get("api_key"),
        "base_url": data.get("base_url"),
        "model": data.get("model")
    }


def execute(**kwargs) -> dict[str, Any]:
    try:
        # 1. 读取输入
        path = kwargs.get("path", "")
        conf = kwargs.get("conf", "")

        # 2. 参数校验
        if not conf or not str(conf).strip():
            return {
                "status": "failed",
                "output_format": "text",
                "message": "请提供投稿会议名称",
                "data": {}
            }

        path_str = str(path).strip() if path else ""
        if not path_str:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "论文文件不存在或路径无效",
                "data": {}
            }

        pdf_path = Path(_resolve_path(path_str))
        if not pdf_path.exists() or not pdf_path.is_file():
            return {
                "status": "failed",
                "output_format": "text",
                "message": "论文文件不存在或路径无效",
                "data": {}
            }

        if pdf_path.suffix.lower() != ".pdf":
            return {
                "status": "failed",
                "output_format": "text",
                "message": "仅支持 PDF 格式论文",
                "data": {}
            }

        # 3. 获取 DeepSeek API KEY
        config = _get_deepseek_config()
        api_key = config.get("api_key")
        base_url = config.get("base_url") or "https://api.deepseek.com/v1"
        model = config.get("model") or "deepseek-v4-pro"

        if not api_key:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "获取 DeepSeek API KEY 失败",
                "data": {}
            }

        # 4. 尝试提取 PDF 文本；若失败则使用 Base64 编码作为论文内容
        # 大模型单次请求的输入长度有限，对超大 PDF 做大小限制，避免触发 API 400 错误
        MAX_PDF_BYTES = 5 * 1024 * 1024  # 5MB
        MAX_CONTENT_CHARS = 400000  # 论文内容最大字符数（约 40 万字符，预留推理空间）

        file_size = pdf_path.stat().st_size
        paper_text = _extract_pdf_text(pdf_path)
        if paper_text:
            paper_content = paper_text
        else:
            # 无文本层（扫描版 PDF）→ 使用 Base64，但需限制大小
            if file_size > MAX_PDF_BYTES:
                return {
                    "status": "failed",
                    "output_format": "text",
                    "message": (
                        f"PDF 文件过大（{file_size / 1024 / 1024:.1f}MB），且无法提取文本层"
                        f"（可能是扫描版 PDF）。请先通过 OCR 工具将 PDF 转为可提取文本的版本，"
                        f"或压缩 PDF 后再试。"
                    ),
                    "data": {}
                }
            pdf_bytes = pdf_path.read_bytes()
            pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
            paper_content = (
                f"[PDF 文件 {pdf_path.name} 的 Base64 编码如下，请解码后评审]\n"
                f"{pdf_b64}"
            )

        # 文本内容过长时截断，避免超出模型上下文限制
        if len(paper_content) > MAX_CONTENT_CHARS:
            paper_content = paper_content[:MAX_CONTENT_CHARS] + "\n\n[内容过长，已截断]"

        # 5. 构建提示词
        system_prompt = (
            "你是一位资深学术会议审稿专家和论文修改导师。"
            "请使用中文，根据用户提供的投稿会议评审标准，对论文进行详细评审并给出修改建议。"
        )

        user_prompt = f"""
投稿会议：{conf}

请针对以下方面进行评审：
1. 总体创新性评估
2. 论文结构、形式、实验完整性评估
3. 摘要、简介与章节安排的一致性、衔接与呼应
4. 总体思路章节与贡献点、后续章节的对应关系
5. 公式目的解释与符号说明的清晰度
6. 消融实验完整性与核心参数敏感性分析
7. 实验充分性、baseline 方法先进性与完备性
8. 论文可读性与可复现性
9. 针对上述评估重点给出详细批改建议和指引
10. 按 {conf} 评审标准进行评议并给出最终打分判定

请以结构化 Markdown 输出，包含每个方面的评分/评价、问题描述与具体修改建议，最后给出综合评分和是否建议接收。

论文内容如下：
{paper_content}
"""

        # 6. 调用模型
        review_text = _call_deepseek_chat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt
        )

        if not review_text or not str(review_text).strip():
            return {
                "status": "failed",
                "output_format": "text",
                "message": "模型调用失败：返回内容为空",
                "data": {}
            }

        # 7. 保存评审报告
        report_path = pdf_path.with_name(f"{pdf_path.stem}_评审报告.md")
        review_time = time.strftime("%Y-%m-%d %H:%M:%S")
        report_content = f"""# 论文评审报告

- **论文文件**：{pdf_path.name}
- **投稿会议**：{conf}
- **评审时间**：{review_time}

## 评审意见

{review_text}
"""
        report_path.write_text(report_content, encoding="utf-8")

        # 8. 返回结果
        return {
            "status": "success",
            "output_format": "text",
            "message": f"评审完成，报告已保存至 {report_path}",
            "data": {
                "text": review_text,
                "report_path": str(report_path)
            }
        }

    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": str(e),
            "data": {}
        }
# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests

# ── 运行目录（由平台注入，禁止写死绝对路径或按 __file__ 层级反推）──
# SOTABAND_ROOT      系统根：代码 + 内置资源，视为只读
# SOTABAND_USER_HOME 当前用户目录：本工具的一切产物写这里
# TOOL_DIR           本工具实现目录
def _env_path(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None

def _find_up(path, marker):
    """兜底：向上找 marker 所在目录。只按特征文件定位，不假定固定层级。"""
    p = path
    for _ in range(12):
        if (p / marker).exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None

_tool_dir = _env_path("TOOL_DIR") or (Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())
_PROJECT_ROOT = _env_path("SOTABAND_ROOT") or _find_up(_tool_dir, "core") or Path.cwd()
# 用户目录：env 缺失时按「SOTABAND_HOME 根 + uid」还原，绝不退回系统根
_USER_HOME = _env_path("SOTABAND_USER_HOME") or (
    (_env_path("SOTABAND_HOME") or _PROJECT_ROOT.parent / "SB-USER")
    / (os.environ.get("SOTABAND_USER_ID", "").strip() or "0")
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 数据目录（产物一律落用户目录，系统根随 git 分发不可写脏）──
_DATA_DIR = _USER_HOME / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

def _out_dir(*parts) -> Path:
    """本工具产物目录：USER_HOME/data/<工具或子目录>/<...>，自动创建。"""
    d = _DATA_DIR.joinpath(*parts) if parts else _DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d

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
    # 资源根读取顺序：私有优先（用户「复制为副本」后的修改生效），系统公共层兜底
    _roots = [_USER_HOME, _env_path("SOTABAND_HOME") or (_PROJECT_ROOT.parent / "SB-USER")
              / (os.environ.get("SOTABAND_SYSTEM_UID", "100").strip() or "100")]
    # 从「私有 + 内置」registry.json 查找工具 ID（目录名），私有优先
    tool_id = tool_name  # 默认用名称作为 ID
    for _r in _roots:
        reg_path = _r / "tools" / "registry.json"
        if not reg_path.exists():
            continue
        try:
            for t in json.loads(reg_path.read_text(encoding="utf-8")):
                if t.get("id") == tool_name or t.get("name") == tool_name:
                    tool_id = t["id"]
                    break
        except Exception:
            continue
        if tool_id != tool_name:
            break
    tool_dir = next((r / "tools" / "implementations" / tool_id for r in _roots
                         if (r / "tools" / "implementations" / tool_id / "tool.py").exists()),
                    _roots[0] / "tools" / "implementations" / tool_id)
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


def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型通道，密钥与端点跟随平台全局配置。"""
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


def _call_llm_chat(system_prompt: str, user_prompt: str) -> str:
    """经平台统一 LLM 通道对话（密钥不进工具进程）"""
    return _llm_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=100000,
        temperature=0.3,
    )


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

        # 3. 尝试提取 PDF 文本；若失败则使用 Base64 编码作为论文内容
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

        # 6. 调用模型（平台统一 LLM 通道）
        review_text = _call_llm_chat(
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
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

# ── LLM 调用辅助（统一走系统配置的 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL） ──
def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。

    跟随全局配置（config/settings.py 的 PROVIDER_PRESETS）自动选择服务商：
    DeepSeek / OpenAI / Kimi / 智谱 / 通义 / 硅基流动 / MiniMax / MiMo / 豆包 等。
    禁止在本工具内直连任何具体服务商端点或硬编码模型名。
    """
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
    script = f"import json, sys; sys.path.insert(0, {str(_PROJECT_ROOT)!r}); exec(open({str(tool_file)!r}, encoding='utf-8').read()); print(json.dumps(execute(**{params!r}), default=str, ensure_ascii=False))"
    # encoding/errors 必须显式：工具输出是含中文的 UTF-8 JSON，
    # Windows 下 text=True 默认按 GBK 解码会直接 UnicodeDecodeError
    proc = _sp.run([py_exe, "-c", script], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=30)
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

import traceback
import re
from datetime import datetime
from pathlib import Path

# Try to import PDF reader — use pypdf (modern, actively maintained)
try:
    from pypdf import PdfReader
except ImportError:
    try:
        import PyPDF2
        PdfReader = PyPDF2.PdfReader
    except ImportError:
        PdfReader = None

def _extract_text_from_pdf(pdf_path: str) -> str:
    """Extract plain text from PDF using pypdf (fallback to PyPDF2)"""
    if PdfReader is None:
        raise RuntimeError("Neither pypdf nor PyPDF2 is available for PDF parsing")
    
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())
        return "\n\n".join(text_parts)
    except Exception as e:
        raise RuntimeError(f"Failed to extract text from PDF: {str(e)}")

def _extract_technical_description(file_path: str) -> str:
    """Read and normalize technical description from .txt, .md, or .pdf"""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    suffix = p.suffix.lower()
    if suffix not in [".txt", ".md", ".pdf"]:
        raise ValueError(f"Unsupported file type '{suffix}'. Only .txt, .md, .pdf are supported.")
    
    try:
        if suffix in [".txt", ".md"]:
            content = p.read_text(encoding="utf-8").strip()
        elif suffix == ".pdf":
            content = _extract_text_from_pdf(str(p))
        else:
            content = ""
        
        # Normalize whitespace: collapse multiple newlines & spaces, but preserve paragraph breaks
        content = re.sub(r'\n\s*\n', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        content = re.sub(r'\n+', '\n', content)
        return content.strip()
    except UnicodeDecodeError:
        # Fallback to utf-8 with error replacement
        content = p.read_text(encoding="utf-8", errors="replace").strip()
        content = re.sub(r'\n\s*\n', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        content = re.sub(r'\n+', '\n', content)
        return content.strip()
    except Exception as e:
        raise RuntimeError(f"Failed to read file '{file_path}': {str(e)}")

def _extract_short_title(filename: str) -> str:
    """Extract short title (≤8 chars, alphanumeric only) from filename stem"""
    stem = Path(filename).stem
    # Remove non-alphanumeric and collapse spaces
    clean = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]+', ' ', stem)
    # Take first word or first Chinese sequence (max 8 chars)
    words = clean.split()
    if not words:
        return "专利"
    first = words[0]
    # If contains Chinese, take up to 8 chars total
    if re.search(r'[\u4e00-\u9fff]', first):
        return first[:8]
    # Else take first 8 alphanumeric chars
    alnum = re.sub(r'[^a-zA-Z0-9]', '', first)
    return alnum[:8] or "专利"

def _validate_llm_output(llm_output: str) -> bool:
    """Check if LLM output contains all four required sections"""
    if not isinstance(llm_output, str) or not llm_output.strip():
        return False
    
    lines = [line.strip() for line in llm_output.split('\n') if line.strip()]
    has_technical_field = any(re.match(r'^##\s*技术领域', line) for line in lines)
    has_background = any(re.match(r'^##\s*背景技术', line) for line in lines)
    has_invention_content = any(re.match(r'^##\s*发明内容', line) for line in lines)
    has_implementation = any(re.match(r'^##\s*具体实施方式', line) for line in lines)
    
    return has_technical_field and has_background and has_invention_content and has_implementation

def execute(**kwargs) -> dict[str, Any]:
    """Main entry point for patent drafting tool"""
    try:
        # --- Input validation ---
        path = kwargs.get("path")
        if not path:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "Missing required parameter: 'path'",
                "data": {}
            }
        
        file_path = _resolve_path(path)
        input_p = Path(file_path)
        if not input_p.exists():
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"技术方案文件不存在，请检查路径：{file_path}",
                "data": {}
            }
        
        # --- Step 1: Extract technical description ---
        try:
            tech_desc_text = _extract_technical_description(file_path)
        except ValueError as e:
            return {
                "status": "failed",
                "output_format": "text",
                "message": str(e),
                "data": {}
            }
        except Exception as e:
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"文件解析失败：{str(e)}",
                "data": {}
            }
        
        if not tech_desc_text.strip():
            return {
                "status": "failed",
                "output_format": "text",
                "message": "技术方案描述文件为空或无法提取有效文本",
                "data": {}
            }
        
        # --- Step 2: Construct LLM prompt ---
        # Use strict structural instruction + legal compliance guardrails
        prompt = f"""你是一名资深中国专利代理师，严格遵循《专利审查指南》第二部分第二章及《专利法实施细则》第十七条要求，为申请人撰写符合授权要件的发明专利说明书初稿。

请基于以下技术方案描述，生成结构完整、术语规范、逻辑严谨、无贬低性表述、具备充分支持性实施例的中文专利说明书（Markdown格式），必须严格包含且仅包含以下四个一级标题（## 开头），顺序不可更改，不得添加其他章节：

## 技术领域
用一句话明确指出本发明所属的技术领域，应准确、简明，一般不超过两行。

## 背景技术
客观陈述与本发明最接近的现有技术（至少一项），说明其结构/原理/效果，并指出其存在的技术问题（不使用主观贬义词如“落后”“缺陷”，而用“存在XX不足”“难以满足XX需求”等中性表述）。

## 发明内容
分三段写：
1. 本发明要解决的技术问题（对应背景技术中指出的问题）；
2. 本发明的技术方案（以“本发明提供一种...”开头，清晰描述整体结构/步骤/组成，突出区别技术特征，避免功能性限定，不出现“优选”“最好”等模糊用语）；
3. 本发明的有益效果（基于技术方案直接推导，避免空泛宣传，如“提高效率”需说明“通过XX结构减少XX步骤，使处理时间缩短X%”）。

## 具体实施方式
提供至少一个完整、可实施的实施例。包括：附图简要说明（若涉及附图，写“图1为本发明的结构示意图”；若无图则省略）、详细结构/步骤描述（带标号部件如“1-壳体，2-处理器...”）、工作过程、效果验证数据（如有）。确保该实施例能完全支持权利要求书中的全部技术特征。

禁止行为：
- 不得虚构未提及的技术特征或效果；
- 不得引入外部常识性知识替代方案细节；
- 不得使用“本领域技术人员可知”等模糊表述；
- 不得出现“本发明的优点是...”等非说明书标准句式；
- 所有术语必须前后一致，首次出现时给出全称+简称（如“卷积神经网络（CNN）”）。

技术方案描述如下：
```
{tech_desc_text[:10000]}  # Truncate to avoid exceeding context (but max_tokens will handle long ones)
```

请直接输出纯 Markdown 内容，不要加任何解释、前缀、后缀或代码块标记（如 ```markdown）。"""

        messages = [
            {"role": "system", "content": "你是一名专业、严谨、合规的中国专利代理师，只输出符合《专利审查指南》要求的说明书正文。"},
            {"role": "user", "content": prompt}
        ]
        
        # --- Step 3: Call LLM ---
        try:
            llm_response = _llm_chat(
                messages=messages,
                max_tokens=100000,
                temperature=0.3
            )
        except Exception as e:
            return {
                "status": "failed",
                "output_format": "text",
                "message": "大模型服务不可用，请稍后重试",
                "data": {}
            }
        
        if not isinstance(llm_response, str) or not llm_response.strip():
            return {
                "status": "failed",
                "output_format": "text",
                "message": "大模型返回空响应，请重试或优化原始描述",
                "data": {}
            }
        
        # Strip markdown code fences if present
        cleaned_response = re.sub(r'^```(?:markdown)?\n|```$', '', llm_response, flags=re.MULTILINE).strip()
        
        # --- Step 4: Validate structure ---
        if not _validate_llm_output(cleaned_response):
            return {
                "status": "failed",
                "output_format": "text",
                "message": "模型未按要求生成完整结构，请重试或优化原始描述",
                "data": {}
            }
        
        # --- Step 5: Generate filename & save ---
        short_title = _extract_short_title(input_p.name)
        timestamp = datetime.now().strftime("%Y%m%d%H%M")
        output_filename = f"专利说明书_{short_title}_{timestamp}.md"
        output_dir = input_p.parent
        output_path = output_dir / output_filename
        
        try:
            output_path.write_text(cleaned_response, encoding="utf-8")
        except Exception as e:
            return {
                "status": "failed",
                "output_format": "text",
                "message": f"无法保存说明书文件，请检查目标目录权限与空间：{str(e)}",
                "data": {}
            }
        
        # --- Success ---
        return {
            "status": "success",
            "output_format": "file",
            "message": f"专利说明书已生成并保存至 {output_path}",
            "data": {
                "file_path": str(output_path)
            }
        }
    
    except Exception as e:
        return {
            "status": "failed",
            "output_format": "text",
            "message": f"工具执行失败: {str(e)}\n\nTraceback:\n{traceback.format_exc()}",
            "data": {}
        }
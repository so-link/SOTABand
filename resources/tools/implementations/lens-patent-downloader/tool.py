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

import urllib.parse
import pandas as pd
from datetime import datetime

# 内部配置项
_API_KEY = "V5zdc1XJa3cFq8OUkbCJgtZmtdXivRb9NbM37SVQloUahXWDUEK1"
_BASE_URL = "https://api.lens.org/patent/search"
_DOWNLOAD_DIR = _DATA_DIR / "download"  # 规范要求 ./data/download/

# 翻译缓存
_TRANSLATE_CACHE = {}

# 英文月份映射，用于日期转换
_EN_MONTHS = {
    "january": "1", "february": "2", "march": "3", "april": "4",
    "may": "5", "june": "6", "july": "7", "august": "8",
    "september": "9", "october": "10", "november": "11", "december": "12",
    "jan": "1", "feb": "2", "mar": "3", "apr": "4",
    "jun": "6", "jul": "7", "aug": "8", "sep": "9",
    "oct": "10", "nov": "11", "dec": "12"
}

def _safe_str(val: Any, default: str = "") -> str:
    """安全地将字段转换为字符串，处理 list 等情况"""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        items = []
        for item in val:
            if isinstance(item, dict):
                items.append(item.get("name") or item.get("title") or str(item))
            else:
                items.append(str(item))
        return "; ".join(items)
    if val is None:
        return default
    return str(val)

def _split_long_text(text: str, max_len: int = 4000) -> list:
    """将长文本按句子边界切分成不大于 max_len 的片段，尽量保持语义完整"""
    if len(text) <= max_len:
        return [text]

    paragraphs = re.split(r'\n\s*\n', text)
    chunks = []
    current = ""
    for para in paragraphs:
        if len(para) > max_len:
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sent in sentences:
                if len(current) + len(sent) + 1 <= max_len:
                    if current:
                        current += " " + sent
                    else:
                        current = sent
                else:
                    if current:
                        chunks.append(current)
                    current = sent
        else:
            if len(current) + len(para) + 2 <= max_len:
                if current:
                    current += "\n\n" + para
                else:
                    current = para
            else:
                if current:
                    chunks.append(current)
                current = para
    if current:
        chunks.append(current)
    return chunks if chunks else [text]

def _translate_text(text: str, source: str = "en", target: str = "zh") -> str:
    """使用系统统一 LLM 翻译文本，支持长文本分段翻译，失败时返回原文"""
    if not text or not text.strip():
        return text
    text = text.strip()
    # 如果是纯中文或仅包含标点、数字，则跳过翻译
    if all('\u4e00' <= c <= '\u9fff' or c.isspace() or c in ',.;!?()[]{}:：，。！？；、' for c in text):
        return text

    cache_key = f"{source}:{target}:{text}"
    if cache_key in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[cache_key]

    if len(text) > 5000:
        segments = _split_long_text(text, max_len=4000)
        translated_segments = []
        for seg in segments:
            seg_cache_key = f"{source}:{target}:{seg}"
            if seg_cache_key in _TRANSLATE_CACHE:
                translated_segments.append(_TRANSLATE_CACHE[seg_cache_key])
            else:
                t = _translate_single(seg, source, target)
                _TRANSLATE_CACHE[seg_cache_key] = t
                translated_segments.append(t)
        combined = "\n\n".join(translated_segments)
        _TRANSLATE_CACHE[cache_key] = combined
        return combined
    else:
        result = _translate_single(text, source, target)
        _TRANSLATE_CACHE[cache_key] = result
        return result

def _translate_single(text: str, source: str = "en", target: str = "zh") -> str:
    """翻译单个文本段（调用系统统一 LLM），失败返回原文"""
    try:
        # 明确要求自动检测源语言并翻译为目标语言
        prompt = (
            "Translate the following text to Chinese. "
            "Automatically detect the source language. "
            "Only output the translation without any extra text.\n\n"
            + text
        )
        messages = [{"role": "user", "content": prompt}]
        translated = _llm_chat(messages, temperature=0.0, max_tokens=8000).strip()
        if translated.startswith('"') and translated.endswith('"'):
            translated = translated[1:-1]
        return translated
    except Exception:
        return text

def _translate_date(date_str: str) -> str:
    """将英文日期字符串转换为中文日期格式；无法识别时返回原字符串"""
    if not date_str or not isinstance(date_str, str):
        return date_str
    # 模式1: Month dd, yyyy  或  Month dd yyyy
    m = re.match(r'([a-zA-Z]+)\s+(\d{1,2}),?\s*(\d{4})', date_str)
    if m:
        mon = m.group(1).lower()
        day = m.group(2)
        year = m.group(3)
        if mon in _EN_MONTHS:
            return f"{year}年{_EN_MONTHS[mon]}月{day}日"
    # 模式2: yyyy-mm-dd
    m2 = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date_str)
    if m2:
        return f"{m2.group(1)}年{m2.group(2)}月{m2.group(3)}日"
    # 其他格式不做转换，直接返回原字符串
    return date_str


def execute(**kwargs) -> dict[str, Any]:
    """
    Lens专利检索与注册工具的执行入口。

    参数:
        req (str): 检索关键词或自然语言需求描述
        n (int): 需要下载的专利数量
        year (int): 发表年份下限
        dataset (str): 注册数据集时使用的名称

    返回:
        dict: 标准返回格式
    """
    try:
        # ── 1. 参数读取与校验 ──
        req = kwargs.get("req", None)
        dataset_name = kwargs.get("dataset", None)

        try:
            n = int(kwargs.get("n", 0))
        except (TypeError, ValueError):
            return {"status": "failed", "message": "参数 'n' 必须为整数"}
        try:
            year = int(kwargs.get("year", 0))
        except (TypeError, ValueError):
            return {"status": "failed", "message": "参数 'year' 必须为整数"}

        if not req or not isinstance(req, str) or not req.strip():
            return {"status": "failed", "message": "参数 'req' 必须为非空字符串"}
        if n <= 0:
            return {"status": "failed", "message": "参数 'n' 必须为正整数"}
        if year < 1900:
            return {"status": "failed", "message": "参数 'year' 必须为 >= 1900 的整数"}
        if not dataset_name or not isinstance(dataset_name, str) or not dataset_name.strip():
            return {"status": "failed", "message": "参数 'dataset' 必须为非空字符串"}

        # ── 2. 创建存储目录 ──
        max_attempts = 10
        data_path = None
        for _ in range(max_attempts):
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            candidate = _DOWNLOAD_DIR / ts
            if not candidate.exists():
                data_path = candidate
                break
            time.sleep(0.1)
        if data_path is None:
            return {"status": "failed", "message": "无法创建唯一的时间戳目录"}

        os.makedirs(data_path, exist_ok=False)

        # ── 3. 构建 Lens API 搜索请求 ──
        encoded_req = urllib.parse.quote_plus(req.strip())
        query_str = f"{encoded_req}+pub_date:[{year}-01-01 TO *]"
        query_encoded = urllib.parse.quote(query_str, safe="[]*+")
        url = f"{_BASE_URL}?token={_API_KEY}&size={n}&query={query_encoded}&include=biblio,lens_id,abstract,claims,description&sort=desc(score)"

        # ── 4. 调用 API 并处理结果 ──
        try:
            resp = requests.get(url, timeout=30)
        except requests.RequestException as e:
            return {"status": "failed", "message": f"Lens API 请求失败: {str(e)}"}

        if resp.status_code != 200:
            error_msg = f"Lens API 返回状态码 {resp.status_code}"
            try:
                err_data = resp.json()
                if "message" in err_data:
                    error_msg += f": {err_data['message']}"
            except:
                error_msg += f": {resp.text[:200]}"
            return {"status": "failed", "message": error_msg}

        try:
            result_json = resp.json()
        except ValueError:
            return {"status": "failed", "message": "Lens API 返回非JSON格式数据"}

        patents = result_json.get("data", [])
        if not patents:
            patents = result_json.get("results", [])
        if not patents:
            columns = ["专利号", "标题", "公开日期", "发明人", "摘要"]
            return {
                "status": "success",
                "message": "检索完成，但未找到符合条件的专利",
                "output_format": "table",
                "data": {"columns": columns, "rows": []}
            }

        patents = patents[:n]

        # ── 5. 处理每篇专利，下载 MD 文件并收集信息 ──
        rows = []
        for patent in patents:
            biblio = patent.get("biblio", {})
            patent_number = _safe_str(biblio.get("publication_number") or patent.get("patent_number") or "")
            title_en = _safe_str(biblio.get("title") or "")
            date_pub_raw = _safe_str(biblio.get("date_published") or "")
            inventors_list = biblio.get("inventors", [])
            if isinstance(inventors_list, list):
                inventors_en = ", ".join(_safe_str(inv.get("name", "")) for inv in inventors_list if inv.get("name"))
            else:
                inventors_en = _safe_str(inventors_list)
            abstract_en = _safe_str(patent.get("abstract") or "")
            claims_en = _safe_str(patent.get("claims") or "")
            description_en = _safe_str(patent.get("description") or "")
            lens_id = _safe_str(patent.get("lens_id") or patent_number)

            # 翻译所有文本字段
            title_zh = _translate_text(title_en)
            abstract_zh = _translate_text(abstract_en)
            claims_zh = _translate_text(claims_en) if claims_en else ""
            description_zh = _translate_text(description_en) if description_en else ""
            inventors_zh = _translate_text(inventors_en) if inventors_en else ""
            date_pub_zh = _translate_date(date_pub_raw)  # 日期转换为中文

            safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in str(lens_id))
            safe_name = safe_name.strip().replace(" ", "_") or "patent"
            md_filename = f"{safe_name}.md"
            md_path = data_path / md_filename

            # 构建纯中文 Markdown 内容
            md_content = f"# {title_zh}\n\n"
            md_content += f"**专利号:** {patent_number}\n"
            md_content += f"**Lens ID:** {lens_id}\n"
            md_content += f"**公开日期:** {date_pub_zh}\n"
            md_content += f"**发明人:** {inventors_zh}\n\n"
            if abstract_zh:
                md_content += f"## 摘要\n\n{abstract_zh}\n\n"
            if claims_en:
                md_content += f"## 权利要求\n\n"
                md_content += f"{claims_zh}\n\n" if claims_zh else f"{claims_en}\n\n"
            if description_en:
                md_content += f"## 说明书\n\n"
                md_content += f"{description_zh}\n\n" if description_zh else f"{description_en}\n\n"

            try:
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
            except IOError as e:
                return {"status": "failed", "message": f"写入文件 {md_path} 失败: {str(e)}"}

            rows.append([
                patent_number,
                title_zh,
                date_pub_zh,
                inventors_zh,
                abstract_zh
            ])

        # ── 6. 生成 CSV 文件 ──
        csv_path = data_path / "patents_info.csv"
        df = pd.DataFrame(rows, columns=["专利号", "标题", "公开日期", "发明人", "摘要"])
        try:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        except Exception as e:
            return {"status": "failed", "message": f"生成 CSV 失败: {str(e)}"}

        # ── 7. 统计文件 ──
        all_files = [f for f in data_path.iterdir() if f.is_file()]
        file_count = len(all_files)
        total_size = sum(f.stat().st_size for f in all_files)

        # ── 8. 注册数据集 ──
        ts = data_path.name
        dataset_uid = f"lens-{dataset_name.replace(' ', '_')}-{ts}"

        raw_md = f"# Lens Patent Search: {dataset_name}\n\n"
        raw_md += f"- **Query:** {req}\n"
        raw_md += f"- **Year >=:** {year}\n"
        raw_md += f"- **Number of patents:** {len(rows)}\n\n"
        raw_md += "| 专利号 | 标题 | 公开日期 | 发明人 | 摘要 |\n"
        raw_md += "|--------|------|----------|--------|------|\n"
        for row in rows:
            abstract_short = (row[4][:100] + "...") if len(row[4]) > 100 else row[4]
            raw_md += f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {abstract_short} |\n"

        try:
            reg_result = _call_api(
                "api-data-register",
                id=dataset_uid,
                name=dataset_name,
                raw_md=raw_md,
                data_path=str(data_path),
                file_count=file_count,
                total_size=total_size,
                formats=["md", "csv"]
            )
        except Exception as e:
            return {"status": "failed", "message": f"数据集注册 API 调用异常: {str(e)}"}

        if reg_result.get("status") == "failed":
            return {"status": "failed", "message": f"数据集注册失败: {reg_result.get('message', '未知错误')}"}

        dataset_id = reg_result.get("dataset_id", dataset_uid)

        return {
            "status": "success",
            "message": f"已完成检索，下载 {len(rows)} 篇专利全文（已翻译为中文），数据集已注册 (ID: {dataset_id})",
            "output_format": "table",
            "data": {
                "columns": ["专利号", "标题", "公开日期", "发明人", "摘要"],
                "rows": rows
            }
        }

    except Exception as e:
        return {"status": "failed", "message": f"工具执行异常: {str(e)}"}
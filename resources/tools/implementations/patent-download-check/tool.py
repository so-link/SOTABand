# === SOTABand 工具标准模板 ===
import os, sys, json, time, re
from pathlib import Path
from typing import Any

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

# === 头部结束，以下由 LLM 生成 ===

# ───────────────────────────────────────────────
# 专利本地查重工具（技术方案对比分析）
#
# 职责：针对待申请专利的技术方案，在本地专利数据库中检索相关现有专利，
# 进行技术方案对比分析，评估新颖性与侵权风险。
#
# 流程：读技术方案文件 → 读专利数据库目录（pdf/txt/md/docx 混合）→
#       程序粗筛（0.6×实词覆盖度 + 0.4×4-gram 重合度）→
#       大模型语义对比（新颖性单独对比 + 侵权全面覆盖两维度）→ 生成报告。
#
# 本文件【完全自包含】：文件读取/语料读取/粗筛/复核/报告全部内联实现，
# 不依赖其它工具。移植时仅需复制本目录 + registry.json 条目。
# ───────────────────────────────────────────────

import datetime

# ── 可选依赖：PDF/docx 读取 ──
try:
    from pypdf import PdfReader
except Exception:
    try:
        from PyPDF2 import PdfReader
    except Exception:
        PdfReader = None

try:
    import docx
except Exception:
    docx = None


# ═══════════════════════════════════════════════════════════════
# 以下为内联的底层能力（原属 patent-retrieval-check，现内联实现自包含）
# ═══════════════════════════════════════════════════════════════

def _read_document(path: str) -> str:
    """读取 txt/md/pdf/docx 并返回全文文本。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    suffix = p.suffix.lower()
    if suffix in (".txt", ".md", ".text", ".markdown"):
        return p.read_text(encoding="utf-8", errors="ignore")
    elif suffix == ".pdf":
        if PdfReader is None:
            raise ImportError("未安装 pypdf / PyPDF2，无法读取 PDF")
        reader = PdfReader(str(p))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(pages)
    elif suffix == ".docx":
        if docx is None:
            raise ImportError("未安装 python-docx，无法读取 docx")
        d = docx.Document(str(p))
        return "\n".join(para.text for para in d.paragraphs)
    else:
        return p.read_text(encoding="utf-8", errors="ignore")


def _load_corpus_from_dir(source_dir: str):
    """从专利数据库目录读取专利文件（支持 pdf/txt/md/docx 混合格式）。

    对 .md 文件尝试解析 front-matter 元数据 + 摘要/权利要求/说明书分节；
    对其它格式（pdf/txt/docx）直接读取全文，标题取文件名。
    """
    src = Path(source_dir)
    if not src.exists() or not src.is_dir():
        return []

    supported = (".md", ".markdown", ".txt", ".text", ".pdf", ".docx")
    files = sorted(
        f for f in src.iterdir()
        if f.is_file() and f.suffix.lower() in supported
    )

    records = []
    for f in files:
        try:
            suffix = f.suffix.lower()
            if suffix in (".md", ".markdown"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                meta = {}
                body = text
                if text.startswith("---"):
                    parts = text.split("---", 2)
                    if len(parts) >= 3:
                        for line in parts[1].splitlines():
                            if ":" in line:
                                k, v = line.split(":", 1)
                                meta[k.strip()] = v.strip()
                        body = parts[2]

                sec_name = "description"
                h1_title = ""
                sections = {"abstract": "", "claims": "", "description": ""}
                for raw_line in body.splitlines():
                    line = raw_line.strip()
                    lower = line.lower()
                    if line.startswith("# "):
                        if not h1_title:
                            h1_title = line[2:].strip()
                        sec_name = "description"
                    elif lower.startswith("## 摘要") or lower.startswith("## abstract"):
                        sec_name = "abstract"
                    elif lower.startswith("## 权利要求") or lower.startswith("## claims"):
                        sec_name = "claims"
                    elif lower.startswith("## 说明书") or lower.startswith("## description"):
                        sec_name = "description"
                    else:
                        sections[sec_name] += raw_line + "\n"

                title = meta.get("title") or meta.get("名称") or h1_title or f.stem
                pub = meta.get("publication_number") or meta.get("publication") or f.stem
                records.append({
                    "id": f.stem,
                    "title": str(title),
                    "publication_number": str(pub),
                    "country": meta.get("country", ""),
                    "assignee": meta.get("assignee", ""),
                    "url": meta.get("url", ""),
                    "abstract": sections["abstract"].strip(),
                    "claims": sections["claims"].strip(),
                    "description": sections["description"].strip(),
                })
            else:
                # pdf / txt / docx：直接读全文，标题取文件名
                text = _read_document(str(f))
                if not text or not text.strip():
                    continue
                records.append({
                    "id": f.stem,
                    "title": f.stem,
                    "publication_number": f.stem,
                    "country": "",
                    "assignee": "",
                    "url": "",
                    "abstract": "",
                    "claims": "",
                    "description": text.strip(),
                })
        except Exception:
            continue
    return records


def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。"""
    import requests
    from config.settings import settings
    cfg = settings.llm
    url = (cfg.base_url or "").rstrip("/") + "/chat/completions"
    body = {
        "model": kwargs.get("model", cfg.model),
        "messages": messages,
        "stream": False,
        "max_tokens": kwargs.get("max_tokens", cfg.max_tokens),
        "temperature": kwargs.get("temperature", cfg.temperature),
    }
    if kwargs.get("thinking") is not None:
        body["thinking"] = kwargs["thinking"]
    if kwargs.get("response_format") is not None:
        body["response_format"] = kwargs["response_format"]
    if kwargs.get("extra_body"):
        body.update(kwargs["extra_body"])
    sess = requests.Session()
    sess.trust_env = False
    try:
        resp = sess.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {cfg.api_key}",
                     "Content-Type": "application/json"},
            timeout=kwargs.get("timeout", cfg.timeout),
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    finally:
        sess.close()


_STOP_WORDS = {
    "the", "and", "for", "with", "method", "apparatus", "said", "wherein",
    "this", "that", "from", "using", "comprising", "includes", "include",
}


def _extract_content_tokens(text: str):
    """提取内容特征：中文相邻字 bigram + 英文实词（去停用词）。"""
    text = text or ""
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    cjk_bigrams = set(zip(cjk_chars, cjk_chars[1:]))
    eng_words = set(re.findall(r"[A-Za-z]{2,}", text.lower())) - _STOP_WORDS
    return cjk_bigrams | eng_words


def _normalize_for_ngram(text: str):
    """归一化后用于 n-gram 提取：保留中英文数字，去其他。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", (text or "").lower())


def _ngrams(text: str, n: int = 4):
    s = _normalize_for_ngram(text)
    return set(s[i:i + n] for i in range(0, len(s) - n + 1))


def _rough_rank(target_text: str, records: list):
    """计算待检方案与每篇召回专利的相关度并排序。"""
    target_content = _extract_content_tokens(target_text)
    target_ngram = _ngrams(target_text, 4)

    scored = []
    for rec in records:
        try:
            candidate_text = " ".join([
                str(rec.get("abstract") or ""),
                str(rec.get("claims") or ""),
                str(rec.get("description") or ""),
            ])
            candidate_content = _extract_content_tokens(candidate_text)
            candidate_ngram = _ngrams(candidate_text, 4)

            coverage = (
                len(target_content & candidate_content) / len(target_content)
                if target_content else 0.0
            )
            ngram_union = target_ngram | candidate_ngram
            ngram_overlap = (
                len(target_ngram & candidate_ngram) / len(ngram_union)
                if ngram_union else 0.0
            )
            score = 0.6 * coverage + 0.4 * ngram_overlap
            score = float(score)
            if score != score or score in (float("inf"), float("-inf")):
                score = 0.0
            scored.append((rec, round(score, 6)))
        except Exception:
            scored.append((rec, 0.0))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _ai_review(target_text: str, top_candidates: list):
    """调用系统 LLM 对 Top-N 候选做语义级对比分析（新颖性 + 侵权风险两维度）。"""
    if not top_candidates:
        return "无候选专利，未进行对比分析。"

    excerpts = []
    for idx, (rec, score) in enumerate(top_candidates, 1):
        snippet = (rec.get("claims") or rec.get("abstract") or rec.get("description") or "")[:4000]
        abstract = (rec.get("abstract") or "")[:800]
        excerpts.append(
            f"### 候选 {idx}\n"
            f"- 公开号/文件名: {rec.get('publication_number','')}\n"
            f"- 标题: {rec.get('title','')}\n"
            f"- 相关度评分: {score:.4f}\n"
            f"- 摘要: {abstract}\n"
            f"- 权利要求/说明书片段:\n{snippet}\n"
        )

    system_prompt = (
        "你是资深专利代理师与知识产权律师，精通中国专利法（《专利法》第22条新颖性、"
        "第59条保护范围界定），擅长专利新颖性审查与侵权判定。"
        "输出使用中文，专业、客观、审慎，结论必须有具体技术特征依据，"
        "不得空泛下结论。对每一篇现有专利都要单独、完整地分析，不得遗漏或合并。"
    )
    user_prompt = (
        "请将「待申请的技术方案」与下列「现有专利」进行专利技术方案对比分析，"
        "严格按以下两个维度分别、逐篇展开：\n\n"
        "【维度一：新颖性影响分析】\n"
        "依据中国专利法第22条，新颖性采用「单独对比」原则——必须逐篇单独判断，"
        "看该篇现有专利是否已公开了与本技术方案「相同或实质相同」的技术特征。"
        "对每一篇，明确指出：本方案的技术特征 A/B/C… 分别被该专利的哪些特征覆盖，"
        "以及是否存在区别技术特征、区别是否属于惯用手段的直接置换或公知常识。"
        "最终给出该篇的新颖性影响等级：高（几乎完全公开，本方案丧失新颖性）/"
        "中（核心特征部分公开，需谨慎）/低（仅局部或非实质特征重合）/无（明显不同）。\n\n"
        "【维度二：侵权风险分析】\n"
        "依据中国专利法第59条，采用「全面覆盖」原则——判断本技术方案实施后，"
        "其全部技术特征是否被该现有专利的某一项权利要求所包含。"
        "对每一篇，逐一比对技术特征，说明是否落入其独立权利要求的保护范围，"
        "是否存在等同特征（等同侵权）的可能。"
        "最终给出该篇的侵权风险等级：高（全面覆盖，实施即侵权）/中（部分覆盖，"
        "存在等同侵权风险）/低（仅个别特征重合，风险较低）/无（不落入保护范围）。\n\n"
        "【对比分析结果】\n"
        "1. 用 Markdown 表格汇总所有候选专利的两个维度结论；\n"
        "2. 明确指出对新颖性威胁最大、侵权风险最高的专利（重点关注项）；\n"
        "3. 给出总体结论与行动建议（是否建议修改技术方案、规避方向、能否申请、"
        "是否需要做 FTO 自由实施分析等）。\n\n"
        "输出格式要求（Markdown，严格按此结构）：\n"
        "## 一、新颖性影响分析\n"
        "### 候选 1：公开号/标题\n- 技术特征对比：…\n- 新颖性影响等级：…\n- 理由：…\n\n"
        "（每个候选如此重复，逐个列出）\n\n"
        "## 二、侵权风险分析\n"
        "### 候选 1：公开号/标题\n- 权利要求覆盖比对：…\n- 侵权风险等级：…\n- 理由：…\n\n"
        "（每个候选如此重复，逐个列出）\n\n"
        "## 三、对比分析结果\n"
        "| 排名 | 公开号/文件名 | 标题 | 新颖性影响 | 侵权风险 |\n"
        "|---|---|---|---|---|\n"
        "| 1 | ... | ... | 高/中/低/无 | 高/中/低/无 |\n\n"
        "### 重点结论与建议\n（含重点关注专利、总体结论、修改/规避/申请建议）\n\n"
        "待申请技术方案全文（可能较长，已截断）：\n"
    ) + target_text[:12000] + "\n\n现有专利片段：\n" + "\n".join(excerpts)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        review = _llm_chat(messages, max_tokens=16384, temperature=0.3)
        return review
    except Exception as e:
        return f"LLM 对比分析调用失败: {str(e)}"


def _write_report(out_dir: Path, target_path, req, country, n, top,
                  records, top_candidates, corpus_path, ai_review, source_desc):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 专利技术方案对比分析报告",
        "",
        f"- 待申请技术方案: `{target_path}`",
        f"- 生成时间: {now_str}",
        f"- 专利数据库: {source_desc}",
        f"- 数据库专利总数: {len(records)}",
        f"- 进入大模型对比分析 Top-N: {top}",
        "",
        "---",
        "",
        "## 一、相关度 Top-N 候选",
        "",
        "> 下表为程序粗筛（实词覆盖度 60% + 4-gram 重合度 40%）排序出的最相关现有专利，",
        "> 进入后续大模型逐篇对比分析。",
        "",
        "| 排名 | 公开号/文件名 | 标题 | 相关度 |",
        "|---|---|---|---|",
    ]
    for i, (rec, score) in enumerate(top_candidates, 1):
        pub = rec.get("publication_number", "") or ""
        title = rec.get("title", "") or ""
        lines.append(f"| {i} | {pub} | {title} | {score:.4f} |")
    lines += [
        "",
        "---",
        "",
        "## 二、技术方案对比分析（新颖性 + 侵权风险两维度）",
        "",
        "> 以下为资深专利代理师视角的逐篇对比分析，先按「新颖性影响」与「侵权风险」",
        "> 两个维度分别展开，最后给出汇总对比结果与行动建议。",
        "",
        ai_review if ai_review else "未生成对比分析。",
        "",
    ]
    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _resolve_path(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)


def execute(**kwargs) -> dict[str, Any]:
    """
    专利本地查重（待申请技术方案 + 本地专利数据库 → 对比分析新颖性与侵权风险）

    输入:
        tech_doc  (str) 待申请专利技术方案的本地文件路径（txt/md/pdf/docx）
        patent_db (str) 专利数据库的本地目录路径（内含 pdf/txt/md/docx 等专利文件）
        top       (int) 进入大模型对比分析的候选专利数，默认 5（1~30）

    返回:
        dict: status / message / output_format(file) / data（报告路径 + 对比分析结论）
    """
    try:
        tech_doc = (kwargs.get("tech_doc") or kwargs.get("path") or "").strip()
        patent_db = (kwargs.get("patent_db") or kwargs.get("corpus") or "").strip()

        try:
            top = int(kwargs.get("top", 5))
        except Exception:
            top = 5
        top = max(1, min(top, 30))

        if not tech_doc:
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 tech_doc（待申请专利技术方案的本地文件路径）", "data": {}}
        if not patent_db:
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 patent_db（专利数据库的本地目录路径）", "data": {}}

        # ── 读待申请技术方案 ──
        target_path = _resolve_path(tech_doc)
        target_text = _read_document(target_path)
        if not target_text or not target_text.strip():
            return {"status": "failed", "output_format": "text",
                    "message": "技术方案文件内容为空，无法进行分析", "data": {}}

        # ── 读专利数据库目录 ──
        db_dir = _resolve_path(patent_db)
        records = _load_corpus_from_dir(db_dir)
        if not records:
            return {"status": "failed", "output_format": "text",
                    "message": f"专利数据库目录中未找到任何专利文件（pdf/txt/md/docx）: {patent_db}",
                    "data": {}}

        # ── 程序粗筛 ──
        ranked = _rough_rank(target_text, records)
        top_candidates = ranked[:top]

        # ── 大模型对比分析（新颖性 + 侵权风险）──
        ai_review_text = _ai_review(target_text, top_candidates)

        # ── 报告落盘 ──
        out_dir = _DATA_DIR / "patent-local-check" / (
            f"{Path(target_path).stem}_{int(time.time())}")
        out_dir.mkdir(parents=True, exist_ok=True)
        source_desc = f"本地专利数据库: {patent_db}"
        report_path = _write_report(
            out_dir, target_path, "", "", len(records), top,
            records, top_candidates, db_dir, ai_review_text, source_desc,
        )

        # ── 前端表格：相关度排序 ──
        columns = ["排名", "公开号/文件名", "标题", "相关度"]
        rows = [[str(i + 1), rec.get("publication_number", ""), rec.get("title", ""),
                 f"{score:.4f}"]
                for i, (rec, score) in enumerate(top_candidates, 1)]

        message = (f"已完成技术方案对比分析：与专利数据库 {len(records)} 篇现有专利比对，"
                   f"相关度 Top-{len(top_candidates)} 进入大模型新颖性与侵权风险分析。\n\n"
                   f"{ai_review_text}")

        return {"status": "success", "output_format": "file",
                "message": message,
                "data": {"file_path": str(report_path),
                         "patent_db": db_dir,
                         "columns": columns, "rows": rows,
                         "compared": len(records),
                         "top": len(top_candidates),
                         "ai_review": ai_review_text}}

    except Exception as e:
        import traceback
        return {"status": "failed", "output_format": "text",
                "message": f"专利本地查重失败: {str(e)}\n\n{traceback.format_exc()}",
                "data": {}}

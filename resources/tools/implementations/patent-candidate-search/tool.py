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
# 专利相关性检索下载工具（自动迭代检索 + 可选下载 PDF 原件）
#
# 职责：读取本地待检专利/技术文件 → 大模型提取关键词（主词 + 次级词）→
# 按用户国别/分类号/年份硬约束迭代搜索 Google Patents（5 次）：
#   第 1 次仅主词；第 2~5 次逐个追加次级词，若顶部相关性变差则换词。
# 每轮搜 100 篇（拆 2 次 × 50，page=1/2），大模型一次评估 100 篇、逐篇打 A/B/C 等级。
# 5 轮后合并去重（按公开号），按「等级→轮次(后轮优先)→序号」排序取前 n 篇，按需下载 PDF。
#
# 本文件【完全自包含】：Google Patents 检索/PDF 下载/LLM 调用全部内联实现，
# 不依赖 patent-retrieval-check 或其它工具。移植时仅需复制本目录 + registry.json 条目。
# ───────────────────────────────────────────────

import re
import urllib.parse
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


def _resolve_path(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(_PROJECT_ROOT / p)


# ═══════════════════════════════════════════════════════════════
# 以下为内联的底层能力（原属 patent-retrieval-check，现内联实现自包含）
# ═══════════════════════════════════════════════════════════════

def _llm_chat(messages: list, **kwargs) -> str:
    """同步调用系统统一大模型客户端，返回完整文本。

    用同步 requests 直连，跟随全局配置（config/settings.py 的 PROVIDER_PRESETS）。
    支持的额外 kwargs：model / thinking / response_format / extra_body / max_tokens / temperature。
    """
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


def _get_system_proxy():
    """读取 Windows 系统代理（注册表），返回 'http://host:port' 或 None。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
        if enabled and server:
            m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})", str(server))
            if m:
                return f"http://{m.group(1)}"
    except Exception:
        pass
    return None


# ── 请求节流 ──
_GP_MIN_INTERVAL = 1.0
_last_gp_req_ts = 0.0


def _throttle_gp():
    global _last_gp_req_ts
    now = time.monotonic()
    wait = _GP_MIN_INTERVAL - (now - _last_gp_req_ts)
    if wait > 0:
        time.sleep(wait)
    _last_gp_req_ts = time.monotonic()


def _candidate_chrome_paths():
    import glob as _glob
    candidates = []
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    for d in sorted(_glob.glob(str(base / "chromium-*")), reverse=True):
        exe = Path(d) / "chrome-win64" / "chrome.exe"
        if exe.exists():
            candidates.append(str(exe))
    for d in sorted(_glob.glob(str(base / "chromium-*")), reverse=True):
        for sub in ("chrome-headless-shell-win64", "chrome-win", "chrome-win64"):
            exe = Path(d) / sub / ("chrome-headless-shell.exe" if "headless" in sub else "chrome.exe")
            if exe.exists():
                candidates.append(str(exe))
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(p).exists():
            candidates.append(p)
    return candidates


# ── 模块级浏览器单例 ──
_browser = None
_page = None
_pw_instance = None
_playwright_ok = True
_playwright_error = ""


def _ensure_gp_browser(timeout: int = 30):
    global _browser, _page, _pw_instance, _playwright_ok, _playwright_error
    if _page is not None:
        return
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        _playwright_ok = False
        _playwright_error = f"{type(e).__name__}: {e}"
        raise ConnectionError(
            "Google Patents 浏览器通道不可用：未安装 playwright，请安装后重试。"
        ) from e
    try:
        _pw_instance = sync_playwright().start()
    except Exception as e:
        _playwright_ok = False
        _playwright_error = f"{type(e).__name__}: {e}"
        raise ConnectionError(
            "Google Patents 浏览器通道启动失败：Playwright 无法启动。详情："
            f"{type(e).__name__}: {e}"
        ) from e
    try:
        exe = None
        for cand in _candidate_chrome_paths():
            if Path(cand).exists():
                exe = cand
                break
        sys_proxy = _get_system_proxy()
        launch_kwargs = {"headless": True}
        if exe:
            launch_kwargs["executable_path"] = exe
        if sys_proxy:
            launch_kwargs["proxy"] = {"server": sys_proxy}
        try:
            _browser = _pw_instance.chromium.launch(**launch_kwargs)
        except Exception as e:
            if exe and "headless" in str(e).lower():
                _browser = _pw_instance.chromium.launch(
                    headless=True, proxy=launch_kwargs.get("proxy"))
            else:
                raise
        ctx = _browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36",
            locale="zh-CN",
        )
        _page = ctx.new_page()
        try:
            _page.goto("https://patents.google.com/", timeout=timeout * 1000,
                       wait_until="domcontentloaded")
        except Exception:
            pass
    except Exception as e:
        _playwright_error = f"{type(e).__name__}: {e}"
        try:
            _pw_instance.stop()
        except Exception:
            pass
        _browser = None
        _page = None
        _pw_instance = None
        raise ConnectionError(
            "Google Patents 浏览器通道启动失败：请确认系统能访问 Google（大陆需代理），"
            f"且 Chromium 内核可用。详情：{type(e).__name__}: {e}"
        ) from e


class _GPResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _reset_gp_browser():
    global _browser, _page, _pw_instance
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _page = None
    _pw_instance = None


def _gp_response(url: str, retries: int = 2, timeout: int = 30):
    global _browser, _page, _playwright_ok, _playwright_error
    _throttle_gp()
    if not _playwright_ok:
        raise ConnectionError(
            "Google Patents 浏览器通道不可用：未安装 playwright"
            f"（{_playwright_error}）。请安装 playwright 及浏览器内核后再试。"
        )
    if _page is None:
        _ensure_gp_browser(timeout)
    js = """
    async (u) => {
        try {
            const r = await fetch(u, {
                headers: {'Accept': 'application/json, text/plain, */*'},
                credentials: 'include',
            });
            const text = await r.text();
            return JSON.stringify({status: r.status, body: text});
        } catch (e) {
            return JSON.stringify({status: -1, body: String(e)});
        }
    }
    """
    for attempt in range(retries):
        if _page is None:
            _ensure_gp_browser(timeout)
        try:
            raw = _page.evaluate(js, url)
            data = json.loads(raw)
            status = data.get("status", -1)
            body = data.get("body", "")
        except Exception as e:
            _reset_gp_browser()
            if attempt == retries - 1:
                raise ConnectionError(
                    f"Google Patents 浏览器 fetch 失败：{type(e).__name__}: {e}"
                ) from e
            continue
        if status == 200:
            return _GPResponse(status, body)
        if status in (429, 503, 403):
            if attempt == retries - 1:
                raise RuntimeError(
                    f"Google Patents 限流（HTTP {status}），请稍后重试或减少请求频率"
                )
            time.sleep(2 * (attempt + 1))
            continue
        if status == -1:
            if attempt == retries - 1:
                raise ConnectionError(f"Google Patents 浏览器 fetch 异常：{body[:200]}")
            time.sleep(1.5)
            continue
        return _GPResponse(status, body)
    return None


def _gp_request(url: str, retries: int = 2, timeout: int = 30):
    return _gp_response(url, retries=retries, timeout=timeout)


def _gp_download_pdf(pid: str, out_dir: str, filename: str = "") -> dict:
    """下载专利官方 PDF 原件到指定目录，返回 {"ok", "path", "size_kb", "error"}。"""
    out_path = Path(out_dir)
    try:
        out_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "path": "", "size_kb": 0, "error": f"无法创建目录 {out_dir}: {e}"}
    try:
        resp = _gp_request(f"https://patents.google.com/xhr/result?id={pid}")
        if resp is None or resp.status_code != 200:
            return {"ok": False, "path": "", "size_kb": 0,
                    "error": f"获取详情失败（HTTP {getattr(resp, 'status_code', '?')}）"}
        raw = resp.text
    except Exception as e:
        return {"ok": False, "path": "", "size_kb": 0, "error": f"获取详情异常: {e}"}
    pdf_urls = re.findall(r'https?://[^"\'\s\\]+\.pdf', raw)
    if not pdf_urls:
        return {"ok": False, "path": "", "size_kb": 0, "error": "详情中未找到 PDF 链接"}
    pdf_url = pdf_urls[0]
    try:
        if _page is None:
            raise RuntimeError("浏览器未初始化")
        api_resp = _page.context.request.get(pdf_url, timeout=60000)
    except Exception as e:
        return {"ok": False, "path": "", "size_kb": 0, "error": f"下载请求失败: {e}"}
    if api_resp.status != 200:
        return {"ok": False, "path": "", "size_kb": 0,
                "error": f"PDF 下载失败（HTTP {api_resp.status}）"}
    body = api_resp.body()
    if not filename:
        m = re.search(r'/([^/]+\.pdf)$', pdf_url)
        filename = m.group(1) if m else f"{pid.replace('/', '_')}.pdf"
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    final_path = out_path / filename
    try:
        final_path.write_bytes(body)
    except Exception as e:
        return {"ok": False, "path": "", "size_kb": 0, "error": f"写入文件失败: {e}"}
    return {"ok": True, "path": str(final_path), "size_kb": round(len(body) / 1024, 1),
            "error": ""}


def _gp_search(req: str, country: str, num: int, after: str = "", before: str = "",
               page: int = 1):
    """通过 Google Patents 公开接口检索，返回 (records, total) 二元组。"""
    inner = [f"q={req}"]
    if country:
        inner.append(f"country={country}")
    if after:
        inner.append(f"after={after}")
    if before:
        inner.append(f"before={before}")
    if num:
        inner.append(f"num={num}")
    if page and page > 1:
        inner.append(f"page={page}")
    q = "&".join(inner)
    encoded = urllib.parse.quote(q, safe="")
    url = f"https://patents.google.com/xhr/query?url={encoded}"

    resp = _gp_request(url)
    if resp is None:
        return [], 0
    try:
        data = resp.json()
    except Exception:
        return [], 0

    total = 0
    cluster = []
    if isinstance(data, dict):
        res = data.get("results")
        if isinstance(res, dict):
            try:
                total = int(res.get("total_num_results") or 0)
            except Exception:
                total = 0
            cluster = res.get("cluster", []) or []
        elif isinstance(res, list):
            cluster = res
        if not cluster:
            cluster = data.get("cluster", []) or data.get("result", []) or []
    elif isinstance(data, list):
        cluster = data

    records = []
    for cl in cluster:
        if isinstance(cl, dict):
            items = cl.get("result", []) or cl.get("results", []) or []
        elif isinstance(cl, list):
            items = cl
        else:
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            p = it.get("patent", {}) or {}
            pid = (it.get("id") or p.get("publication_number")
                   or p.get("publicationNumber") or it.get("publication_number") or "")
            if not pid:
                continue
            title = re.sub(r"<[^>]+>", "", str(p.get("title") or it.get("title") or "")).strip()
            snippet = re.sub(r"<[^>]+>", "", str(p.get("snippet") or "")).strip()
            pub = (p.get("publication_number") or p.get("publicationNumber") or str(pid))
            assignee = (p.get("assignee") or it.get("assignee") or "")
            if isinstance(assignee, list):
                assignee = "; ".join(str(x) for x in assignee)
            records.append({
                "id": str(pid),
                "title": str(title).strip(),
                "publication_number": str(pub).strip(),
                "country": country or "",
                "assignee": str(assignee).strip(),
                "url": (f"https://patents.google.com/{pid}"
                        if str(pid).startswith("patent/")
                        else f"https://patents.google.com/patent/{pid}"),
                "snippet": str(snippet).strip(),
                "abstract": "",
                "claims": "",
                "description": "",
            })
            if len(records) >= num:
                break
        if len(records) >= num:
            break
    return records, total



# 国别中文/别名 → 两字母代码（与 patent-search 保持一致）
_COUNTRY_MAP = {
    "中国": "CN", "中华人民共和国": "CN", "中国大陆": "CN", "中国台湾": "TW", "中国香港": "HK",
    "美国": "US", "usa": "US", "united states": "US",
    "欧洲": "EP", "欧洲专利局": "EP", "欧专": "EP",
    "日本": "JP", "韩国": "KR", "德国": "DE", "英国": "GB", "法国": "FR",
    "世界": "WO", "国际": "WO", "pct": "WO", "wipo": "WO",
}


def _normalize_country(country: str) -> str:
    """把国别输入归一化为两字母代码；0/空/不限 → 空（不限国别）。"""
    c = (country or "").strip()
    if c in ("0", "", "不限", "不限定", "all", "ALL", "All"):
        return ""
    mapped = _COUNTRY_MAP.get(c.lower()) or _COUNTRY_MAP.get(c)
    if mapped:
        return mapped
    if re.fullmatch(r"[A-Za-z]{2}", c):
        return c.upper()
    return c


def _build_date_range(date_from: str, date_to: str):
    """把年份范围转成 Google Patents 的 after=/before= 内层参数值。"""
    after, before = "", ""
    if (date_from or "").strip() and (date_from or "").strip() != "0":
        y = (date_from or "").strip()[:4]
        after = f"priority:{y}0101"
    if (date_to or "").strip() and (date_to or "").strip() != "0":
        y = (date_to or "").strip()[:4]
        before = f"priority:{y}1231"
    return after, before


# ───────────────────────────────────────────────
# 快速模型（评估/提词环节专用，避免推理模型的慢思考）
# ───────────────────────────────────────────────
# deepseek-flash 默认非思考模式，速度快 40~80 倍且筛选质量与 v4-pro 基本一致；
# 用 thinking:disabled 显式关掉思考 + response_format:json_object 强约束 JSON。
# 这些参数由 _llm_chat 透传（见本文件顶部内联的 _llm_chat 实现）。
_FAST_MODEL = "deepseek-flash"
_FAST_THINKING = {"type": "disabled"}
_FAST_JSON_FMT = {"type": "json_object"}


# ───────────────────────────────────────────────
# 关键词提取（大模型）
# ───────────────────────────────────────────────
def _normalize_core(raw_core) -> list:
    """把大模型返回的 core 归一化为统一结构：
    [{"term": "主词", "synonyms": ["同义1", ...]}, ...]

    兼容三种输入：
    - 字符串 "无人机"                      → [{"term":"无人机","synonyms":[]}]
    - 纯列表 ["无人机","停机坪"]            → 逐项转 dict（无同义词）
    - 带同义词 [{term,synonyms}, ...] 或 [{"term":..,"synonyms":[..]}]
    """
    if raw_core is None:
        return []
    # 兼容单对象（dict）
    if isinstance(raw_core, dict):
        raw_core = [raw_core]
    if not isinstance(raw_core, list):
        raw_core = [raw_core]
    result = []
    for item in raw_core:
        if isinstance(item, dict):
            term = str(item.get("term") or "").strip()
            syns_raw = item.get("synonyms") or []
            if isinstance(syns_raw, str):
                syns = [syns_raw.strip()] if syns_raw.strip() else []
            else:
                syns = [str(s).strip() for s in syns_raw if str(s).strip()]
            if not term:
                continue
            result.append({"term": term, "synonyms": syns})
        else:
            term = str(item).strip()
            if term:
                result.append({"term": term, "synonyms": []})
    return result


def _core_to_display(core) -> str:
    """把归一化后的 core 转成用于展示的字符串（主词及其同义词）。"""
    if not core:
        return ""
    parts = []
    for c in core:
        term = c.get("term", "")
        syns = c.get("synonyms", [])
        if syns:
            parts.append(f"{term}({','.join(syns)})")
        else:
            parts.append(term)
    return "、".join(parts)


def _wrap_term(term: str) -> str:
    """把单个检索词元规范化：含空格（英文短语）的词用双引号包裹，
    避免 Google Patents 把短语内的空格误当 AND 分隔符。

    - "solid state battery" → '"solid state battery"'（短语精确匹配）
    - "无人机" / "UAV"（无空格）→ 原样
    """
    t = (term or "").strip()
    if not t:
        return t
    # 已带引号或括号的原样返回（避免重复包裹）
    if t.startswith('"') and t.endswith('"'):
        return t
    if " " in t:
        return f'"{t}"'
    return t


def _extract_keywords(target_text: str, rc) -> dict:
    """让大模型从待检文件提取检索关键词（核心词 + 次级词）。

    返回 {"core": [{"term":..., "synonyms":[...]}, ...], "secondary": [...]}
    主关键词可有多个，它们之间是【AND 关系】；每个主词可带同义/近义表达（组内 OR），
    用于扩大召回（避免「无人飞行器/UAV」这类同义但字面不同的专利被漏掉）。
    不再让大模型建议 CPC 分类号——大模型凭语义「猜」的分类号常与 Google Patents
    实际标注不符，作为硬过滤会直接把结果清空（首轮 0 结果）。
    """
    user = (
        "你是专利检索专家。请阅读下面这份待检技术方案/专利文件，提取用于"
        "在 Google Patents 上检索相关在先专利的关键词。\n\n"
        "要求：\n"
        "1. 提取 1~3 个【主关键词】：该技术方案的主题词，相关专利的标题或摘要中"
        "【必须同时包含】这些词（多个主词之间是 AND 关系，如[\"无人机\",\"停机坪\"]表示"
        "两者都出现）。主词应足够宽、能覆盖该技术方向，但不要宽到跨领域"
        "（如\"系统\"\"装置\"）。只有当技术方案确实有多个并列主题词时才提取多个，"
        "通常 1~2 个即可，避免过度收窄。\n"
        "2. 为每个主关键词补充 0~3 个【同义表达】（synonyms）：必须是「等价替换词」——"
        "与主词含义完全相同、在专利文献中可互相替换的词（如\"无人机\"→\"无人飞行器/UAV\"）。"
        "同义词用于扩大召回：多关键词 AND 组合时 Google 不做同义归一，"
        "字面不同但语义相同的专利会被漏掉，故同义词很重要。\n"
        "【严格约束】同义词必须是等价替换词，禁止放入：①更宽泛的上位词（如\"无人机\"的"
        "同义词不能是\"飞行器\"\"航空器\"）；②相关但不等的词（如\"停机坪\"不能写\"跑道\"）。"
        "若没有严格等价的替换词，synonyms 留空数组即可，宁可少、不可泛。\n"
        "3. 提取 4~6 个【次级关键词】：限定细分技术方向、方法、应用场景的词"
        "（如\"避障\"\"目标检测\"\"路径规划\"\"蜂群\"），相关专利【可能包含、不一定出现】。"
        "按「最能区分该技术细分方向」的重要性从高到低排列。\n\n"
        "只输出一个 JSON 对象，不要任何解释，格式如下：\n"
        '{"core":[{"term":"无人机","synonyms":["无人飞行器","UAV"]},'
        '{"term":"停机坪","synonyms":["起降平台"]}],'
        '"secondary":["避障","集群","巡查"]}'
        "\n\n待检文件全文（可能较长，已截断）：\n" + target_text[:8000]
    )
    messages = [
        {"role": "system", "content": "你是专利检索关键词提取助手，输出严格 JSON。"},
        {"role": "user", "content": user},
    ]
    try:
        # 提词用快速模型 deepseek-flash（关思考 + JSON 约束），快且稳；
        # 不再用推理模型（思考慢且易耗尽 max_tokens 导致 content 为空）
        reply = _llm_chat(messages, model=_FAST_MODEL, max_tokens=4000,
                             temperature=0.2, thinking=_FAST_THINKING,
                             response_format=_FAST_JSON_FMT)
    except Exception:
        return {}
    # 解析 JSON（容错：提取首个 {...}）
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    core = _normalize_core(data.get("core"))
    secondary = [str(x).strip() for x in (data.get("secondary") or []) if str(x).strip()]
    return {"core": core, "secondary": secondary}


# ───────────────────────────────────────────────
# 顶部相关性评估（大模型，用于迭代反馈调词 + 逐篇打相关度等级）
# ───────────────────────────────────────────────
ASSESS_TOP = 100  # 每轮只评估顶部 N 条（Google 相关性降序，顶部才真正相关）


def _ai_assess_relevance(rc, records: list, target_text: str, core,
                         secondary_used: list) -> dict:
    """让大模型判断本轮搜索顶部结果与待检专利的「技术方向」相关性，
    并对每一篇打 A/B/C 相关度等级。

    返回:
        {
          "grade": "A"|"B"|"C",          # 本轮整体方向相关程度（用于反馈调词）
          "rel_ids": [1,3,5,...],        # 「同一技术方向」相关专利的全局序号
          "rel_levels": {1:"A", 3:"B", 5:"C"},  # 逐篇相关度等级（A 高度相关/B 部分/C 弱）
          "reason": "..."
        }

    说明：
    - 一次评估 ASSESS_TOP（100）条，不拆批。deepseek-flash 关思考后一次 100 条仅 ~1.6s，
      且 100 条 ≈ 1.2 万 token，远低于 1M 上下文上限，不会破 token、也不会因拆批多耗推理
      （实测一次评估 100 篇 1.56s，拆成 2×50 篇反而要 2.95s，且长尾 50 篇基本无关）。
    - rel_levels 用于最终「按等级排序取前 n」，保证真正高相关的排在前面，
      而不是只看 Google 的返回顺序。
    """
    if not records:
        return {"grade": "C", "rel_ids": [], "rel_levels": {}, "reason": "本轮无结果"}
    # 只评估顶部 ASSESS_TOP 条（Google 相关性降序，顶部才真正相关）
    records = records[:ASSESS_TOP]

    lines = []
    for i, r in enumerate(records, 1):
        title = (r.get("title") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        pub = (r.get("publication_number") or "").strip()
        lines.append(f"{i}. [{pub}] {title}"
                     + (f"｜{snippet[:160]}" if snippet else ""))

    sec = "、".join(secondary_used) if secondary_used else "无"
    core_disp = _core_to_display(core)

    user = (
        f"你是专利技术方向判定专家。下面这份是【待检技术方案】的摘要：\n"
        f"---\n{target_text[:2000]}\n---\n\n"
        f"本轮检索用的主关键词：{core_disp}；已用次级词：{sec}。\n"
        f"下面 {len(records)} 篇是 Google Patents 返回的顶部结果（标题 + 摘要片段）。\n\n"
        f"注意：它们可能都含主关键词，但【技术方向】未必与待检方案相同"
        f"（例如都是无人机，但分别是送货、航拍、植保、巡检等不同方向）。"
        f"你要判断的是「是否与待检方案属于同一细分技术方向」，而非「是否含关键词」。\n\n"
        f"请完成三件事：\n"
        f"1. 逐条判断是否与待检方案同一技术方向，给出相关专利的序号（从 1 开始）；\n"
        f"2. 对每一条【相关的】专利打相关度等级：\n"
        f"   A = 高度相关（与待检方案核心功能高度吻合）；\n"
        f"   B = 部分相关（同方向但侧重点/应用场景有差异）；\n"
        f"   C = 弱相关（勉强沾边）。\n"
        f"   无关的专利不打等级。\n"
        f"3. 对【整体相关性】定级：A=顶部高度相关 / B=部分相关 / C=基本无关。\n\n"
        f"只输出一个 JSON 对象，不要任何解释：\n"
        '{"grade":"A|B|C","rel_ids":[1,3,5],"rel_levels":{"1":"A","3":"B","5":"C"},"reason":"简述"}'
        f"\n\n" + "\n".join(lines)
    )
    messages = [
        {"role": "system", "content": "你是专利技术方向判定助手，输出严格 JSON。"},
        {"role": "user", "content": user},
    ]
    try:
        # 用快速模型 deepseek-flash（关思考 + JSON 约束），一次评估 100 条仅 ~1.6s
        reply = _llm_chat(messages, model=_FAST_MODEL, max_tokens=8000,
                             temperature=0.1, thinking=_FAST_THINKING,
                             response_format=_FAST_JSON_FMT)
    except Exception:
        return {"grade": "B", "rel_ids": [], "rel_levels": {}, "reason": "评估失败，保守保留"}
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {"grade": "B", "rel_ids": [], "rel_levels": {}, "reason": "评估解析失败，保守保留"}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"grade": "B", "rel_ids": [], "rel_levels": {}, "reason": "评估 JSON 解析失败，保守保留"}

    grade = str(data.get("grade") or "B").strip().upper()
    if grade not in ("A", "B", "C"):
        grade = "B"

    # 相关序号（1-based，相对 records[:ASSESS_TOP]）
    rel_ids = []
    for x in (data.get("rel_ids") or []):
        try:
            n = int(x)
            if 1 <= n <= len(records):
                rel_ids.append(n)
        except Exception:
            continue
    rel_ids = sorted(set(rel_ids))

    # 逐篇相关度等级：只保留合法序号、等级归一化为 A/B/C
    rel_levels = {}
    for k, v in (data.get("rel_levels") or {}).items():
        try:
            idx = int(k)
        except Exception:
            continue
        lv = str(v or "").strip().upper()
        if lv not in ("A", "B", "C"):
            lv = "B"
        if 1 <= idx <= len(records):
            rel_levels[idx] = lv

    # 兜底：rel_ids 里但没给等级的，默认为 B
    for idx in rel_ids:
        if idx not in rel_levels:
            rel_levels[idx] = "B"

    reason = str(data.get("reason") or "").strip()
    return {"grade": grade, "rel_ids": rel_ids, "rel_levels": rel_levels, "reason": reason}


# ───────────────────────────────────────────────
# 主执行函数
# ───────────────────────────────────────────────
def execute(**kwargs) -> dict[str, Any]:
    """
    专利智能检索候选池（自动迭代检索，不下载全文）

    输入:
        path      (str) 待检专利/技术方案文件（txt/md/pdf/docx）
        country   (str) 国别代码 CN/US/EP/WO，输入 0 表示不限
        ipc       (str) 分类号 CPC（必填，输入 0 表示不限）
        date_from (str) 起始年份（必填，输入 0 表示不限）
        date_to   (str) 截止年份（必填，输入 0 表示不限）
        max_rounds (int) 最大迭代轮数，默认 5（硬上限，含首轮）
        n         (int) 检索数目（必填，1~50；返回最相关前 n 篇）
        download_dir (str) 下载目录（必填；输入 0 表示不下载、仅返回 n 个结果；
                           非 0 时返回结果的同时下载这 n 篇专利 PDF 原件到该目录）

    返回:
        dict: status / message / output_format(table) / data（候选池列表 + 下载结果）

    迭代策略（主词必含 + 反馈调词）：
        第 1 次仅主词；第 2~5 次逐个追加次级词，若顶部相关性变差则换词。
        每次只取第一页 100 条，用大模型按「同一技术方向」筛相关专利并保留。
    """
    try:
        path = (kwargs.get("path") or "").strip()
        country_raw = str(kwargs.get("country", "CN") or "").strip()

        if not path:
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 path（待检专利/技术方案文件）", "data": {}}

        # ── 必填参数校验：ipc / date_from / date_to（0 或空 = 不限）──
        ipc = str(kwargs.get("ipc") or "").strip()
        if ipc == "":
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 ipc（分类号 CPC，输入 0 表示不限）", "data": {}}
        date_from = str(kwargs.get("date_from") or "").strip()
        if date_from == "":
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 date_from（起始年份，输入 0 表示不限）", "data": {}}
        date_to = str(kwargs.get("date_to") or "").strip()
        if date_to == "":
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 date_to（截止年份，输入 0 表示不限）", "data": {}}

        # ── 检索数目 n（必填，1~50）──
        n = kwargs.get("n")
        if n is None or str(n).strip() == "":
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 n（检索数目，即返回的最相关结果数量，1~50）", "data": {}}
        try:
            n = int(n)
        except Exception:
            return {"status": "failed", "output_format": "text",
                    "message": f"参数 n 必须为 1~50 的整数，收到: {n!r}", "data": {}}
        if n < 1 or n > 50:
            return {"status": "failed", "output_format": "text",
                    "message": f"参数 n 必须在 1~50 之间，收到: {n}", "data": {}}

        # ── 下载目录 download_dir（必填；0 = 不下载，非 0 = 下载到该目录）──
        download_dir = kwargs.get("download_dir")
        if download_dir is None or str(download_dir).strip() == "":
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 download_dir（下载目录，输入 0 表示不下载）", "data": {}}
        download_dir = str(download_dir).strip()
        do_download = download_dir not in ("0", "0.0", "否", "不下载", "none", "None")

        try:
            max_rounds = int(kwargs.get("max_rounds", 5))
        except Exception:
            max_rounds = 5
        max_rounds = max(2, min(max_rounds, 5))

        # ── 硬约束处理（这些在迭代中不变）──
        country = _normalize_country(country_raw)
        after, before = _build_date_range(date_from, date_to)
        ipc = "" if ipc in ("0", "") else ipc

        # ── 读取待检文件 ──
        target_path = _resolve_path(path)
        target_text = _read_document(target_path)
        if not target_text or not target_text.strip():
            return {"status": "failed", "output_format": "text",
                    "message": "待检文件内容为空，无法提取关键词", "data": {}}

        # ── 大模型提取关键词 ──
        kw = _extract_keywords(target_text, rc)
        core = kw.get("core") or []
        if not core:
            return {"status": "failed", "output_format": "text",
                    "message": "大模型未能从文件中提取出有效关键词，请确认文件内容为完整技术方案",
                    "data": {}}
        secondary = kw["secondary"]
        # 分类号只在用户显式输入时生效（硬约束）；不再采用大模型建议的分类号——
        # 大模型「猜」的 CPC 常与 Google Patents 实际标注不符，会导致首轮 0 结果。

        # ── 迭代检索循环 ──
        # 策略（主词必含 + 反馈调词）：
        #   第 1 次仅主词；第 2~5 次逐个追加次级词，若顶部相关性变差则换词。
        #   每次只取第一页 100 条，用大模型按「同一技术方向」筛相关专利并保留。
        #   5 次后合并去重（按公开号），返回最相关前 20 篇。
        ROUNDS = max_rounds  # 固定迭代次数（默认 5）
        TOP_K = n            # 最终保留/返回的最相关条数（用户指定 n，1~99）

        rounds_log = []
        # 汇总：所有轮次筛出的相关专利，按 (公开号 -> record) 去重保序
        collected = []          # 有序列表，每项为 dict（含 record + level + round + pos 排序键）
        collected_keys = set()  # 公开号集合
        # 记录每个次级词在当前轮次中的使用情况，用于「换词」
        # secondary 列表按重要性从高到低；我们用指针 cur 依次追加，replaced 记录已换掉的
        used_secondary = []     # 已成功加入检索式的次级词（按加入顺序）
        sec_idx = 0             # 指向下一个待追加的次级词下标
        prev_grade = None       # 上一轮相关性等级

        def _append_records(recs, level_map, rnd):
            """把 recs 追加进 collected，按公开号去重。

            recs: 相关专利 record 列表（已按 rel_ids 顺序）
            level_map: {全局1-based序号: "A"|"B"|"C"} 逐篇相关度等级
            rnd: 当前轮次（用于最终排序）
            每项附加排序键：level(A>B>C)、round(越大越靠前)、pos(越小越靠前)。
            """
            for pos, r in enumerate(recs, 1):
                key = r.get("publication_number") or r.get("id") or ""
                if key and key in collected_keys:
                    continue
                if key:
                    collected_keys.add(key)
                lv = level_map.get(pos, "B")
                collected.append({
                    "record": r,
                    "level": lv,
                    "round": rnd,
                    "pos": pos,
                })

        for rnd in range(1, ROUNDS + 1):
            # 构造本轮检索式：主词组（组内 OR 同义词）+ 已用的次级词（AND）
            # Google Patents 查询语法：空格 = AND，`A OR B` = 或。
            # 每个主词及其同义词用 OR 连接（扩召回），不同主词组之间用空格（AND，都必含）。
            # 注意：含空格的英文短语必须用双引号包裹（_wrap_term），否则空格会被误当 AND。
            query_parts = []
            for c in core:
                term = _wrap_term(c.get("term", ""))
                syns = [_wrap_term(s) for s in c.get("synonyms", [])]
                group_terms = [x for x in ([term] + syns) if x]
                if group_terms:
                    query_parts.append(" OR ".join(group_terms))
            for s in used_secondary:
                sw = _wrap_term(s)
                if sw and sw not in query_parts:
                    query_parts.append(sw)
            kw_for_query = " ".join(query_parts)
            if ipc:
                q = f"{kw_for_query} AND CPC={ipc}"
            else:
                q = kw_for_query

            # 搜索顶部 100 篇，拆 2 次请求（num=50 page=1 + page=2）再合并。
            # 实测：一次 num=100 服务端序列化慢且不稳定（4~11s），拆 2×50 稳定更快（~2.5s），
            # 且 page=2 能拿到无重叠的第 51~100 条。
            records1, total1 = _gp_search(q, country, 50, after=after, before=before, page=1)
            records2, _ = _gp_search(q, country, 50, after=after, before=before, page=2)
            records = records1 + records2
            total = total1

            if not records:
                round_note = f"第{rnd}次：检索式「{q}」未返回结果（命中 {total} 篇）"
                rounds_log.append(round_note)
                # 无结果：若当前有次级词，说明收窄过度，退掉最后一个次级词（放宽）
                if used_secondary:
                    removed = used_secondary.pop()
                    round_note = f"  ↳ 无结果，退回次级词「{removed}」，下轮放宽"
                    rounds_log.append(round_note)
                prev_grade = "C"
                continue

            # 大模型评估本轮顶部相关性 + 筛出相关专利序号 + 逐篇打相关度等级
            assess = _ai_assess_relevance(rc, records, target_text, core, used_secondary)
            grade = assess["grade"]
            rel_ids = assess["rel_ids"]
            rel_levels = assess.get("rel_levels", {})
            reason = assess.get("reason", "")

            # 保留本轮相关专利（按 rel_ids 顺序；若大模型未给序号，保守保留全部）
            if rel_ids:
                rel_records = [records[i - 1] for i in rel_ids if 1 <= i <= len(records)]
                # level_map 的 key 用「rel_ids 内的位置序号」对齐（1-based 对应 rel_records 位置）
                level_map = {j: rel_levels.get(i, "B") for j, i in enumerate(rel_ids, 1)}
            else:
                rel_records = records
                # 未给序号时保守保留全部，默认等级 B
                level_map = {j: "B" for j in range(1, len(records) + 1)}
            _append_records(rel_records, level_map, rnd)

            round_note = (f"第{rnd}次：检索式「{q}」命中 {total} 篇，"
                          f"筛出相关 {len(rel_records)} 篇，相关性等级 {grade}"
                          f"{'（' + reason + '）' if reason else ''}")
            rounds_log.append(round_note)

            # ── 反馈调词 ──
            # 相关性变差（grade 低于上一轮）→ 换词（替换掉刚加入的次级词）
            grade_rank = {"A": 3, "B": 2, "C": 1}
            if prev_grade is not None and grade_rank[grade] < grade_rank[prev_grade]:
                if used_secondary and sec_idx < len(secondary):
                    old = used_secondary[-1]
                    new = secondary[sec_idx]
                    sec_idx += 1
                    used_secondary[-1] = new
                    rounds_log.append(f"  ↳ 相关性从 {prev_grade} 降为 {grade}，"
                                      f"将次级词「{old}」替换为「{new}」")
                elif used_secondary:
                    # 没有可换的新词，退掉当前次级词放宽
                    removed = used_secondary.pop()
                    rounds_log.append(f"  ↳ 相关性变差且无可换新词，退回次级词「{removed}」")
            else:
                # 相关性持平或更好 → 继续追加下一个次级词
                if sec_idx < len(secondary):
                    used_secondary.append(secondary[sec_idx])
                    sec_idx += 1
                else:
                    # 次级词已用完，若还有迭代次数，尝试用主词的同义扩展或保持
                    rounds_log.append("  ↳ 次级词已用完，后续轮次保持当前检索式")

            prev_grade = grade

        # ── 合并去重后，按「相关度等级 + 轮次 + 序号」排序，取最相关的前 TOP_K 篇 ──
        # 排序规则：
        #   1. 相关度等级优先：A > B > C（大模型逐篇判定，高相关的排前面）
        #   2. 同等级时，轮次越靠后越优先 —— 因为迭代逐渐追加次级词，
        #      后面的检索式更精准，其顶部结果相关性通常更高
        #   3. 同轮次同等级时，序号越小越优先（Google 相关性降序）
        _level_rank = {"A": 0, "B": 1, "C": 2}
        collected_sorted = sorted(
            collected,
            key=lambda it: (
                _level_rank.get(it["level"], 1),  # 等级：A 优先
                -it["round"],                      # 轮次：靠后优先
                it["pos"],                         # 序号：靠前优先
            ),
        )
        final_items = collected_sorted[:TOP_K]
        final_records = [it["record"] for it in final_items]
        rounds_log.append(f"→ 合并去重后共 {len(collected)} 篇相关专利，"
                          f"按相关度等级+轮次排序，返回最相关前 {len(final_records)} 篇")

        # ── 空结果提示 ──
        if not final_records:
            return {"status": "success", "output_format": "text",
                    "message": "经过迭代检索与筛选，未能保留足够相关的候选专利。"
                               "可尝试放宽年份/国别约束，或更换待检文件重试。",
                    "data": {"total_candidates": 0, "candidates": [],
                             "rounds": rounds_log, "core": core,
                             "secondary": secondary, "ipc": ipc,
                             "country": country}}

        # 候选池字段：标题/snippet/公开号/链接（snippet 即关键词命中片段，非完整摘要）
        core_disp = _core_to_display(core)
        candidates = []
        for r in final_records:
            candidates.append({
                "publication_number": r.get("publication_number", ""),
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
                "assignee": r.get("assignee", ""),
                "country": r.get("country", country),
                "url": r.get("url", ""),
            })

        columns = ["序号", "公开号", "标题", "关键词命中片段(snippet)", "链接"]
        rows = []
        for i, c in enumerate(candidates, 1):
            rows.append([
                str(i),
                c["publication_number"],
                c["title"],
                c["snippet"][:120] if c["snippet"] else "",
                c["url"],
            ])

        # ── 下载 PDF 原件（可选）──
        downloaded = []   # 每项 {publication_number, pdf_path, size_kb, ok, error}
        if do_download:
            download_dir_resolved = _resolve_path(download_dir)
            for i, r in enumerate(final_records, 1):
                pid = r.get("id") or f"patent/{r.get('publication_number','')}"
                pub = r.get("publication_number") or str(pid)
                res = _gp_download_pdf(pid, download_dir_resolved, filename=f"{pub}.pdf")
                downloaded.append({
                    "publication_number": pub,
                    "pdf_path": res.get("path", ""),
                    "size_kb": res.get("size_kb", 0),
                    "ok": res.get("ok", False),
                    "error": res.get("error", ""),
                })
            ok_count = sum(1 for d in downloaded if d["ok"])

        message = (
            f"已完成 {ROUNDS} 次自动迭代检索，合并去重后返回最相关 {len(candidates)} 篇候选专利"
            f"（主关键词：{core_disp}；国别：{country or '不限'}；分类号：{ipc or '不限'}）。\n"
            f"以下为候选池（标题 + 关键词命中片段 + 公开号 + 链接）。"
        )
        if do_download:
            message += (
                f"\n已下载 {ok_count}/{len(downloaded)} 篇专利 PDF 原件到: {download_dir_resolved}"
                + ("" if ok_count == len(downloaded)
                   else "（部分失败，见 data.downloaded）")
            )

        data = {
            "total_candidates": len(candidates),
            "candidates": candidates,
            "columns": columns,
            "rows": rows,
            "rounds": rounds_log,
            "core": core,
            "secondary": secondary,
            "ipc": ipc,
            "country": country,
            "n": n,
        }
        if do_download:
            data["download_dir"] = download_dir_resolved
            data["downloaded"] = downloaded
            data["downloaded_ok"] = ok_count

        return {"status": "success", "output_format": "table",
                "message": message,
                "data": data}

    except Exception as e:
        import traceback
        return {"status": "failed", "output_format": "text",
                "message": f"专利智能检索失败: {str(e)}\n\n{traceback.format_exc()}",
                "data": {}}

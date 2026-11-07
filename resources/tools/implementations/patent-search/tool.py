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
# 专利关键词检索工具
#
# 职责：用户输入一个关键词，通过 Google Patents 公开接口做一次检索，
# 返回最相关的前 n 条结果（标题 + 公开号 + 摘要片段 + 链接）。
# 不下载全文、不落盘。相当于一次简单的 Google Patents 搜索。
#
# 面向不熟悉检索语法的用户，可选的国别/分类号/年份约束，输入 0 表示不限。
# 本文件【完全自包含】：Google Patents 检索内联实现，不依赖其它工具。
# 移植时仅需复制本目录 + registry.json 条目。
# ───────────────────────────────────────────────

import urllib.parse

# ═══════════════════════════════════════════════════════════════
# 以下为内联的底层能力（Google Patents 浏览器检索通道）
# 本工具不需要大模型，仅做关键词检索 + 返回结果
# ═══════════════════════════════════════════════════════════════

_GP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


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


_GP_MIN_INTERVAL = 1.0   # 两次请求间最小间隔（秒）
_last_gp_req_ts = 0.0    # 上一次请求的单调时间戳


def _throttle_gp():
    """在发起下一次 Google Patents 请求前，保证与上次请求间隔 >= _GP_MIN_INTERVAL。"""
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


_browser = None
_page = None
_pw_instance = None
_playwright_ok = True
_playwright_error = ""


def _ensure_gp_browser(timeout: int = 30):
    """启动 Chromium 并建立 Google Patents 会话（懒加载）。"""
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


def _gp_response(url: str, retries: int = 2, timeout: int = 30):
    """用 Playwright 浏览器通道发 GET，返回类 requests.Response 的轻量对象。"""
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


class _GPResponse:
    """轻量响应对象，模拟 requests.Response 的常用接口。"""

    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _reset_gp_browser():
    """关闭并清空浏览器单例（供异常恢复或显式释放）。"""
    global _browser, _page, _pw_instance
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _page = None
    _pw_instance = None


def _gp_request(url: str, retries: int = 2, timeout: int = 30):
    """Google Patents GET 请求统一入口（已切换为 Playwright 浏览器通道）。"""
    return _gp_response(url, retries=retries, timeout=timeout)


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


def _build_query(mode: str, keyword: str, title_kw: str, abstract_kw: str,
                 claims_kw: str, ipc: str) -> str:
    """把「关键词 + 分类号」拼成 Google Patents 的 q 检索式（条件间用 AND 连接）。

    模糊模式：keyword 直接作为关键词（覆盖标题+摘要）；
    精确模式：title_kw / abstract_kw / claims_kw 分别用字段限定 TI=/AB=/CL=。
    分类号用 CPC=。

    注意：
    - Google Patents 字段限定用「等号」形式（TI=/AB=/CL=/CPC=），冒号形式会被忽略；
    - 日期范围不在此拼接——必须作为独立内层参数 after=/before= 传入（塞进 q 会返回 0 条）。
    """
    parts = []
    mode = (mode or "fuzzy").strip().lower()
    if mode == "exact":
        for kw, field in ((title_kw, "TI"), (abstract_kw, "AB"), (claims_kw, "CL")):
            k = (kw or "").strip()
            if k and k != "0":
                parts.append(f"{field}={k}")
    else:
        k = (keyword or "").strip()
        if k and k != "0":
            parts.append(k)

    if (ipc or "").strip() and (ipc or "").strip() != "0":
        parts.append(f"CPC={(ipc or '').strip()}")
    # 条件之间用显式 AND 连接（字段限定 TI=/AB=/CPC= 之间仅用空格会失效，返回 0 条）
    return " AND ".join(parts)


def _build_date_range(date_from: str, date_to: str):
    """把年份范围转成 Google Patents 的 after=/before= 内层参数值。

    返回 (after, before) 二元组，值为 'priority:YYYYMMDD' 或空串。
    """
    after, before = "", ""
    if (date_from or "").strip() and (date_from or "").strip() != "0":
        y = (date_from or "").strip()[:4]
        after = f"priority:{y}0101"
    if (date_to or "").strip() and (date_to or "").strip() != "0":
        y = (date_to or "").strip()[:4]
        before = f"priority:{y}1231"
    return after, before


# 国别中文/别名 → 两字母代码（Google Patents 只认两字母代码）
_COUNTRY_MAP = {
    "中国": "CN", "中华人民共和国": "CN", "中国大陆": "CN", "中国台湾": "TW", "中国香港": "HK",
    "美国": "US", "美国": "US", "usa": "US", "united states": "US",
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
    # 已是两字母代码（CN/US/EP...）则原样大写返回
    if re.fullmatch(r"[A-Za-z]{2}", c):
        return c.upper()
    return c  # 无法识别则原样透传


def execute(**kwargs) -> dict[str, Any]:
    """
    专利关键词检索（一次用户关键词搜索，返回前 n 条结果，不下载）

    输入:
        keyword   (str) 检索关键词，如 固态电池（覆盖标题+摘要），多个词用空格分隔
        ipc       (str) 分类号（IPC/CPC），输入 0 表示不限
        country   (str) 国别（如 CN/US/EP），输入 0 表示不限
        date_from (str) 起始年份，输入 0 表示不限
        date_to   (str) 截止年份，输入 0 表示不限
        n         (int) 返回的结果数量（按相关度取前 n 篇，1~50）

    返回:
        dict: status / message / data（total + 结果列表 results）
    """
    try:
        keyword = str(kwargs.get("keyword", "") or "").strip()
        ipc = str(kwargs.get("ipc", "") or "").strip()
        country = str(kwargs.get("country", "CN") or "").strip()
        date_from = str(kwargs.get("date_from", "") or "").strip()
        date_to = str(kwargs.get("date_to", "") or "").strip()

        try:
            n = int(kwargs.get("n", 10))
        except Exception:
            n = 10
        n = max(1, min(n, 50))

        if not keyword:
            return {"status": "failed", "output_format": "text",
                    "message": "缺少必填参数 keyword（检索关键词）", "data": {}}

        query = _build_query("fuzzy", keyword, "", "", "", ipc)
        after, before = _build_date_range(date_from, date_to)
        country = _normalize_country(country)

        # ── 一次检索，拿前 n 条结果 ──
        records, total = _gp_search(query, country, n, after=after, before=before)

        if not records:
            return {"status": "success", "output_format": "text",
                    "message": f"检索完成，但未找到相关专利（检索式：{query}）",
                    "data": {"total": 0, "results": []}}

        results = [{
            "publication_number": r.get("publication_number", ""),
            "title": r.get("title", ""),
            "snippet": r.get("snippet", ""),
            "assignee": r.get("assignee", ""),
            "url": r.get("url", ""),
        } for r in records]

        lines = [f"本次检索命中 {total} 篇专利，以下是相关度最高的前 {len(results)} 篇：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet'][:160]}")
            lines.append(f"   公开号: {r['publication_number']}  |  {r['url']}")
            lines.append("")

        return {"status": "success", "output_format": "text",
                "message": "\n".join(lines),
                "data": {"total": total, "results": results,
                         "query": query, "country": country}}

    except Exception as e:
        import traceback
        return {"status": "failed", "output_format": "text",
                "message": f"专利关键词检索失败: {str(e)}\n\n{traceback.format_exc()}",
                "data": {}}

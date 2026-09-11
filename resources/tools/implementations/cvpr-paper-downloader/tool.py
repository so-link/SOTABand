# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
from datetime import datetime

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

def _safe_filename(title: str, max_len: int = 100) -> str:
    """将标题转为安全的文件名"""
    name = re.sub(r'[\\/*?:"<>|]', '', title)
    name = re.sub(r'\s+', '_', name).strip('_')
    if len(name) > max_len:
        name = name[:max_len]
    if not name:
        name = "paper"
    return name + ".pdf"

def _extract_papers_from_list(html: str, conf: str, year: int, base_url: str):
    """从 CVF Open Access 列表页提取论文标题和详情页链接"""
    soup = BeautifulSoup(html, 'lxml')
    papers = []
    for dt in soup.find_all('dt', class_='ptitle'):
        a = dt.find('a')
        if not a or not a.get('href'):
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        detail_url = urljoin(base_url, a['href'])
        papers.append({
            "title": title,
            "detail_url": detail_url,
            "conf": conf,
            "year": year,
        })
    return papers

def _get_relevance_score(title: str, req: str) -> float:
    """基于标题与需求的词重叠计算相关分数"""
    title_lower = title.lower()
    req_lower = req.lower()

    # 完整匹配优先
    if req_lower in title_lower:
        return 100.0

    req_words = set(re.findall(r'[\w\-]+', req_lower))
    title_words = set(re.findall(r'[\w\-]+', title_lower))
    if not req_words:
        return 0.0

    overlap = len(req_words & title_words)
    occurrence = sum(title_lower.count(w) for w in req_words)

    return overlap * 10.0 + occurrence

def _extract_pdf_url(html: str, base_url: str):
    """从论文详情页提取 PDF 下载链接"""
    soup = BeautifulSoup(html, 'lxml')

    # 优先 meta 标签
    meta = soup.find('meta', attrs={'name': 'citation_pdf_url'})
    if meta and meta.get('content'):
        return urljoin(base_url, meta['content'])

    # 其次查找页面中的 PDF 链接
    a = soup.find('a', href=re.compile(r'\.pdf$', re.I))
    if a and a.get('href'):
        return urljoin(base_url, a['href'])

    return None

def execute(**kwargs) -> dict[str, Any]:
    try:
        req = kwargs.get("req", "")
        n = kwargs.get("n", 0)
        year = kwargs.get("year", 0)
        data_path = kwargs.get("data_path", "")

        # ── 参数校验 ──
        if not req or not isinstance(req, str) or not req.strip():
            return {"status": "failed", "message": "参数 req 不能为空", "output_format": "table", "data": {}}
        req = req.strip()

        try:
            n = int(n)
            year = int(year)
        except (TypeError, ValueError):
            return {"status": "failed", "message": "参数 n 和 year 必须为整数", "output_format": "table", "data": {}}

        if n <= 0:
            return {"status": "failed", "message": "参数 n 必须大于 0", "output_format": "table", "data": {}}
        if year < 1980 or year > 2100:
            return {"status": "failed", "message": "参数 year 不在有效范围内", "output_format": "table", "data": {}}
        if not data_path or not isinstance(data_path, str):
            return {"status": "failed", "message": "参数 data_path 不能为空", "output_format": "table", "data": {}}

        save_dir = Path(_resolve_path(data_path))
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            test_file = save_dir / ".test_write"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
        except Exception as e:
            return {"status": "failed", "message": f"保存目录不可写: {str(e)}", "output_format": "table", "data": {}}

        current_year = datetime.now().year

        # ── 构建会议列表 URL ──
        urls = []
        for y in range(year, current_year + 1):
            urls.append((f"https://openaccess.thecvf.com/CVPR{y}?day=all", "CVPR", y))
            if y % 2 == 1:
                urls.append((f"https://openaccess.thecvf.com/ICCV{y}?day=all", "ICCV", y))

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        }

        # ── 抓取所有论文列表 ──
        all_papers = []
        for url, conf, y in urls:
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                resp.raise_for_status()
                papers = _extract_papers_from_list(resp.text, conf, y, url)
                all_papers.extend(papers)
            except Exception:
                # 跳过不可访问的年份/会议
                continue

        if not all_papers:
            return {"status": "failed", "message": "无法访问 CVF Open Access 网站或未找到论文列表", "output_format": "table", "data": {}}

        # ── 相关度排序 ──
        for p in all_papers:
            p["score"] = _get_relevance_score(p["title"], req)
        all_papers.sort(key=lambda x: x["score"], reverse=True)

        # ── 过滤年份（列表已按 year 循环，但仍显式过滤） ──
        filtered = [p for p in all_papers if p["year"] >= year]
        if not filtered:
            return {"status": "failed", "message": "未找到符合条件的论文", "output_format": "table", "data": {}}

        selected = filtered[:n]

        # ── 下载 PDF 并收集信息 ──
        rows = []
        success_count = 0
        failed_count = 0

        for idx, paper in enumerate(selected, start=1):
            title = paper["title"]
            conf = paper["conf"]
            pyear = paper["year"]
            detail_url = paper["detail_url"]
            pdf_link = detail_url  # fallback
            local_pdf = ""

            try:
                resp = requests.get(detail_url, headers=headers, timeout=20)
                resp.raise_for_status()

                pdf_url = _extract_pdf_url(resp.text, detail_url)
                if pdf_url:
                    pdf_link = pdf_url
                    pdf_resp = requests.get(pdf_url, headers=headers, timeout=60, stream=True)
                    pdf_resp.raise_for_status()
                    filename = _safe_filename(f"{conf}_{pyear}_{title}")
                    local_path = save_dir / filename
                    with open(local_path, 'wb') as f:
                        for chunk in pdf_resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    local_pdf = str(local_path.resolve())
                else:
                    # 如果详情页本身就是 PDF 链接
                    if detail_url.lower().endswith('.pdf'):
                        pdf_link = detail_url
                        pdf_resp = requests.get(detail_url, headers=headers, timeout=60, stream=True)
                        pdf_resp.raise_for_status()
                        filename = _safe_filename(f"{conf}_{pyear}_{title}")
                        local_path = save_dir / filename
                        with open(local_path, 'wb') as f:
                            for chunk in pdf_resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                        local_pdf = str(local_path.resolve())
                    else:
                        raise Exception("未找到 PDF 下载链接")

                success_count += 1
            except Exception:
                failed_count += 1
                local_pdf = ""
                pdf_link = detail_url

            rows.append([idx, title, conf, pyear, pdf_link, local_pdf])

        if success_count == 0:
            return {"status": "failed", "message": "所有论文 PDF 下载失败", "output_format": "table", "data": {}}

        # ── 生成 Markdown 汇总 ──
        md_file = save_dir / "paper_list.md"
        md_lines = [
            "# 论文下载汇总",
            "",
            f"- 需求: {req}",
            f"- 年份阈值: {year}",
            f"- 成功下载: {success_count} 篇",
            f"- 失败: {failed_count} 篇",
            "",
            "| 序号 | 标题 | 会议 | 年份 | 论文链接 | 本地PDF路径 |",
            "|------|------|------|------|----------|-------------|",
        ]
        for row in rows:
            md_lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]} |")
        md_file.write_text("\n".join(md_lines), encoding="utf-8")

        message = f"成功下载 {success_count} 篇论文"
        if failed_count > 0:
            message += f"，失败 {failed_count} 篇"
        message += f"，Markdown 汇总文件已保存至 {md_file}"

        return {
            "status": "success" if success_count > 0 else "failed",
            "message": message,
            "output_format": "table",
            "data": {
                "columns": ["序号", "标题", "会议", "年份", "论文链接", "本地PDF路径"],
                "rows": rows,
                "md_file": str(md_file.resolve()),
            }
        }

    except Exception as e:
        return {"status": "failed", "message": str(e), "output_format": "table", "data": {}}
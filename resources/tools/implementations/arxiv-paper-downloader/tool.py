# === SOTABand 工具标准模板 ===
import os, sys, json, time, csv, datetime
from pathlib import Path
from typing import Any
import requests
import arxiv
import urllib3  # 用于禁用 SSL 警告以实现更宽松的网络访问

# 导入 arxiv 库中的具体异常，用于更精细的错误处理
from arxiv import HTTPError, UnexpectedEmptyPageError

# 禁用 InsecureRequestWarning，因为可能需要 verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def execute(**kwargs) -> dict[str, Any]:
    """根据检索需求下载 ArXiv 论文并注册数据集"""
    # 1. 获取参数
    req = kwargs.get("req", "")
    try:
        n = int(kwargs.get("n", 10))
    except (TypeError, ValueError):
        return {"status": "failed", "message": "参数 n 必须为整数"}
    try:
        year = int(kwargs.get("year", 2020))
    except (TypeError, ValueError):
        return {"status": "failed", "message": "参数 year 必须为整数起始年份"}
    dataset = kwargs.get("dataset", "arxiv_dataset")
    if not req:
        return {"status": "failed", "message": "检索词 req 不能为空"}

    # 2. 创建下载目录（带时间戳）
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    download_dir = _DOWNLOADS_DIR / timestamp
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"status": "failed", "message": f"无法创建下载目录: {str(e)}"}

    # 3. 准备网络会话（增加重试、跳过 SSL 验证，用于下载阶段）
    session = requests.Session()
    session.verify = False
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))

    # 4. 检索 arXiv 论文（配置客户端以优雅处理速率限制）
    try:
        # 设置更长的请求间隔和重试次数，避免 HTTP 429 错误
        client = arxiv.Client(
            page_size=100,          # 每次请求获取的最大结果数
            delay_seconds=5.0,      # 两次请求之间的等待秒数
            num_retries=5           # 若遇到 5xx 或 429 错误时的重试次数
        )
        # 一个微小的初始延迟，防止并发请求同一时刻触发限制
        time.sleep(1)
        search = arxiv.Search(
            query=req,
            max_results=max(100, n * 3),
            sort_by=arxiv.SortCriterion.Relevance,
            sort_order=arxiv.SortOrder.Descending
        )
        results = client.results(search)
    except HTTPError as e:
        return {
            "status": "failed",
            "message": f"arXiv API 请求失败 (HTTP {e.status}): {e.reason}. 请稍后重试或减少请求频率。"
        }
    except UnexpectedEmptyPageError as e:
        return {
            "status": "failed",
            "message": f"arXiv API 返回了空页面，可能参数有误: {str(e)}"
        }
    except Exception as e:
        return {"status": "failed", "message": f"arXiv API 检索失败: {str(e)}"}

    # 过滤年份并收集前 n 篇
    papers = []
    for paper in results:
        if paper.published.year >= year:
            papers.append(paper)
        if len(papers) >= n:
            break

    if not papers:
        return {
            "status": "failed",
            "message": f"未找到符合条件（年份>={year}）的论文，请调整检索词或年份参数。"
        }

    # 5. 下载 PDF 并收集论文信息（使用自定义会话，忽略 SSL）
    csv_rows = []
    download_count = 0
    for paper in papers:
        arxiv_id = paper.get_short_id()
        pdf_url = paper.pdf_url

        try:
            resp = session.get(pdf_url, stream=True, timeout=30)
            resp.raise_for_status()
            pdf_path = download_dir / f"{arxiv_id}.pdf"
            with open(pdf_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            download_count += 1
        except Exception as e:
            print(f"警告: 论文 {arxiv_id} 下载失败: {str(e)}")
            continue

        # 整理作者列表为字符串
        authors_str = ", ".join([author.name for author in paper.authors])
        csv_rows.append([
            paper.title,
            authors_str,
            arxiv_id,
            str(paper.published.year),
            paper.entry_id
        ])

    if download_count == 0:
        return {"status": "failed", "message": "所有论文下载均失败"}

    # 6. 生成 CSV 文件
    csv_file_path = download_dir / "papers_info.csv"
    try:
        with open(csv_file_path, mode="w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["标题", "作者", "arXiv ID", "发表年份", "链接"])
            writer.writerows(csv_rows)
    except Exception as e:
        return {"status": "failed", "message": f"无法写入 CSV 文件: {str(e)}"}

    # 7. 注册数据集
    all_files = list(download_dir.iterdir())
    file_count = len(all_files)
    total_size = sum(f.stat().st_size for f in all_files if f.is_file())
    formats = list(set(f.suffix[1:] for f in all_files if f.is_file()))

    raw_description = f"从 arXiv 检索 '{req}'（起始年份 {year}）的前 {n} 篇论文，实际下载 {download_count} 篇。"

    try:
        register_result = _call_api(
            "api-data-register",
            id=dataset,
            name=dataset,
            raw_md=raw_description,
            data_path=str(download_dir.absolute()),
            file_count=file_count,
            total_size=total_size,
            formats=formats
        )
        if isinstance(register_result, dict) and register_result.get("status") == "failed":
            err_msg = register_result.get("message", "数据集注册失败")
            return {"status": "failed", "message": f"数据集注册API错误: {err_msg}"}
    except Exception as e:
        return {"status": "failed", "message": f"数据集注册API调用异常: {str(e)}"}

    # 8. 返回成功结果
    return {
        "status": "success",
        "output_format": "file",
        "message": f"成功下载 {download_count} 篇论文，并已注册数据集 '{dataset}'",
        "data": {
            "csv_path": str(csv_file_path.absolute()),
            "pdf_dir": str(download_dir.absolute()),
            "count": download_count,
            "dataset_name": dataset
        }
    }

```
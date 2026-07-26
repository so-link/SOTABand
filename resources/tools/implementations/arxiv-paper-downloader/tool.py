
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

import arxiv
import pandas as pd
from datetime import datetime


def execute(**kwargs) -> dict[str, Any]:
    """ArXiv论文批量下载器主函数"""
    # 获取参数
    req = kwargs.get("req", "")
    n_raw = kwargs.get("n", 0)
    year_raw = kwargs.get("year", 0)
    dataset = kwargs.get("dataset", "")

    # 参数校验 - 类型转换
    try:
        n = int(n_raw)
    except (TypeError, ValueError):
        return {
            "status": "failed",
            "message": "参数 'n' 必须为整数"
        }
    try:
        year = int(year_raw)
    except (TypeError, ValueError):
        return {
            "status": "failed",
            "message": "参数 'year' 必须为整数"
        }

    # 参数校验 - 非空及合理性
    if not req or not dataset or n <= 0 or year <= 0:
        return {
            "status": "failed",
            "message": "缺少必要参数或其值非法: req, n (>0), year (>0), dataset 均不能为空"
        }

    # 创建带时间戳的下载目录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_path = _DATA_DIR / "download" / timestamp
    try:
        data_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {
            "status": "failed",
            "message": f"创建目录失败: {str(e)}"
        }

    # 构建查询，加入年份过滤条件
    query_str = f"{req} AND submittedDate:[{year}0101 TO 9999]"
    try:
        search = arxiv.Search(
            query=query_str,
            max_results=n,
            sort_by=arxiv.SortCriterion.Relevance
        )
        client = arxiv.Client()
        results = list(client.results(search))
    except Exception as e:
        return {
            "status": "failed",
            "message": f"ArXiv 搜索失败: {str(e)}"
        }

    if not results:
        return {
            "status": "failed",
            "message": "未找到符合条件的论文。"
        }

    papers_info = []
    downloaded_count = 0
    for paper in results[:n]:
        paper_id = paper.get_short_id()
        info = {
            "title": paper.title,
            "authors": ", ".join(a.name for a in paper.authors),
            "year": paper.published.year if paper.published else "",
            "summary": paper.summary,
            "link": paper.entry_id,
            "pdf_url": paper.pdf_url,
            "download_status": "pending"
        }
        try:
            # 尝试使用 arxiv 内建方法下载 PDF
            paper.download_pdf(dirpath=str(data_path), filename=f"{paper_id}.pdf")
            info["download_status"] = "success"
            downloaded_count += 1
        except Exception:
            try:
                # 备用方案：直接用 requests 下载
                pdf_resp = requests.get(paper.pdf_url, timeout=30)
                pdf_resp.raise_for_status()
                pdf_path = data_path / f"{paper_id}.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(pdf_resp.content)
                info["download_status"] = "success"
                downloaded_count += 1
            except Exception as e:
                info["download_status"] = f"failed: {str(e)[:200]}"
        papers_info.append(info)

    # 保存论文信息为 CSV
    csv_path = data_path / "papers_info.csv"
    try:
        df = pd.DataFrame(papers_info)
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    except Exception as e:
        return {
            "status": "failed",
            "message": f"CSV 保存失败: {str(e)}",
            "data": {
                "directory": str(data_path),
                "csv_path": str(csv_path),
                "dataset_name": ""
            }
        }

    # 统计文件信息，用于注册
    file_count = downloaded_count + 1  # PDF 文件数 + 1 个 CSV
    total_size = sum(
        f.stat().st_size for f in data_path.rglob("*") if f.is_file()
    )
    formats = list({
        f.suffix[1:] if f.suffix else "unknown"
        for f in data_path.rglob("*") if f.is_file()
    })
    raw_md = (
        f"ArXiv papers downloaded for query: '{req}', year >= {year}. "
        f"Total papers: {len(papers_info)} (downloaded: {downloaded_count})."
    )

    # 调用数据集注册 API
    try:
        register_result = _call_api(
            "api-data-register",
            id=dataset,
            name=dataset,
            raw_md=raw_md,
            data_path=str(data_path),
            file_count=file_count,
            total_size=total_size,
            formats=formats
        )
        dataset_name = register_result.get("dataset_id", dataset)
    except Exception as e:
        return {
            "status": "failed",
            "message": (
                f"论文已下载至 {data_path}，CSV 已保存，"
                f"但数据集注册失败: {str(e)}"
            ),
            "data": {
                "directory": str(data_path),
                "csv_path": str(csv_path),
                "dataset_name": ""
            }
        }

    # 构建成功返回信息
    message = (
        f"已成功下载 {downloaded_count}/{len(papers_info)} 篇论文"
        f"并注册数据集 {dataset_name}"
    )
    if downloaded_count < len(papers_info):
        message += f"，{len(papers_info) - downloaded_count} 篇下载失败"

    return {
        "status": "success",
        "output_format": "text",
        "message": message,
        "data": {
            "directory": str(data_path),
            "csv_path": str(csv_path),
            "dataset_name": dataset_name
        }
    }

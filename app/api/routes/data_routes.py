"""数据管理路由 — 扫描、规格生成、注册、预览、处理"""

import os
import json as _json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.schemas.data_schemas import (
    ScanDirectoryRequest,
    GenerateDataSpecRequest,
    RegisterDatasetRequest,
    MatchToolsRequest,
)
from core.llm.client import create_llm_client
from core.resource.registry.data_registry import DataRegistry
def _parse_files_from_spec(spec_md: str) -> list[dict]:
    """从 MD 规范的"数据格式"表格中解析文件列表"""
    files = []
    in_table = False
    for line in spec_md.split("\n"):
        if "数据格式" in line:
            in_table = True
            continue
        if in_table and line.startswith("##"):
            break
        if in_table and line.startswith("|") and "文件" not in line and "---" not in line:
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 3 and parts[0]:
                try:
                    size_str = parts[2].replace("KB", "").replace("MB", "").replace("B", "").strip()
                    size = float(size_str) * 1024 if "KB" in parts[2] else float(size_str) * 1048576 if "MB" in parts[2] else float(size_str) if size_str else 0
                except ValueError:
                    size = 0
                files.append({
                    "name": parts[0], "format": parts[1].lower() if len(parts) > 1 else "",
                    "size": int(size), "description": parts[3] if len(parts) > 3 else "",
                })
    return files


from core.resource.discoverer.data_discoverer import DataDiscoverer
from core.resource.registry.tool_registry import ToolRegistry
from core.resource.discoverer.tool_discoverer import ToolDiscoverer

router = APIRouter()
registry = DataRegistry()
discoverer = DataDiscoverer()
tool_registry = ToolRegistry()
tool_discoverer = ToolDiscoverer()
llm = create_llm_client()

SPEC_PROMPT = """你是一个数据集规格文档生成器。你必须严格根据用户提供的数据文件信息来生成文档，
**严禁编造不存在的信息**。如果某个信息没有提供，填写"待补充"。

模板：

---
id: {dataset-id}
name: {数据集名称}
version: 1.0.0
type: {从文件格式推断，image/tabular/text/timeseries/generic}
status: active
created: {today}
---

# {name}

## 1. 数据集概述
{仅根据用户描述填写，不要编造}

## 2. 目录结构
{列出用户实际提供的文件，树形结构}

## 3. 数据格式
| 文件 | 格式 | 大小 | 说明 |
{根据用户提供的文件信息填写，每个文件的 description 字段作为说明}

## 4. 数据 Schema
{如果无法从文件信息推断，写"待补充"}

## 5. 数据来源
{如果用户未说明，写"待补充"}

## 6. 使用场景
{根据用户描述推断，不要编造}

## 7. 质量评估
{如果无法评估，写"待补充"}

## 8. 访问权限
public

## 9. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | {today} | 初始版本 |

规则：
1. 只使用用户提供的文件信息，不要假设或编造任何数据属性
2. 文件描述(description)直接用作数据格式表的说明列
3. dataset-id 用小写英文+连字符，从用户描述中提取关键词
4. type 从实际文件格式推断: png/jpg→image, csv→tabular, edf→timeseries, txt/md→text
5. 只输出 Markdown，不要额外解释"""


@router.post("/scan-directory")
async def scan_directory(req: ScanDirectoryRequest):
    """扫描目录，返回文件列表和格式分析"""
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"目录不存在: {req.path}")

    files = []
    total_size = 0
    formats = set()

    for entry in path.rglob("*"):
        if entry.is_file():
            size = entry.stat().st_size
            fmt = entry.suffix.lstrip(".").lower()
            files.append({
                "name": entry.name,
                "path": str(entry.relative_to(path)),
                "format": fmt,
                "size": size,
            })
            total_size += size
            formats.add(fmt)

    return {
        "path": req.path,
        "file_count": len(files),
        "total_size": total_size,
        "formats": sorted(formats),
        "files": files,
    }


@router.post("/generate-spec")
async def generate_spec(req: GenerateDataSpecRequest):
    """NL + 文件信息（含描述） → MD 数据集描述文档"""
    context_lines = ["数据文件列表:"]
    for f in req.files:
        size_str = f"{f['size'] / 1024:.1f}KB" if f.get('size', 0) < 1048576 else f"{f.get('size', 0) / 1048576:.1f}MB"
        desc = f.get('description', '')
        desc_str = f" — {desc}" if desc else ""
        context_lines.append(f"- {f.get('name', 'unknown')} ({f.get('format', 'unknown')}, {size_str}){desc_str}")

    response = await llm.chat(
        messages=[
            {"role": "system", "content": SPEC_PROMPT},
            {"role": "user", "content": f"数据集描述: {req.description}\n\n" + "\n".join(context_lines)},
        ],
        temperature=0.3, max_tokens=100000,
    )
    return {"spec_md": response.strip()}


@router.post("/register")
async def register_dataset(req: RegisterDatasetRequest):
    """注册数据集 — 文件集中存储到 resources/data/datasets/{id}/"""
    if not req.spec_md.strip():
        raise HTTPException(400, "specMd 不能为空")

    ds_id = req.dataset_id or "custom-dataset"

    # 创建数据集专用目录
    datasets_root = registry._get_data_dir()
    ds_dir = datasets_root / ds_id
    ds_dir.mkdir(parents=True, exist_ok=True)

    # 从上传的零散文件中复制到数据集目录
    import shutil
    file_count = 0
    total_size = 0
    formats = set()
    for fp in (req.source_files or []):
        src = Path(fp)
        if src.exists() and src.is_file():
            dest = ds_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
            file_count += 1
            total_size += dest.stat().st_size
            formats.add(dest.suffix.lstrip(".").lower())

    # 如未传入文件信息，使用请求中的值
    if file_count == 0:
        file_count = req.file_count
        total_size = req.total_size
        formats = set(req.formats)

    resource = {
        "id": ds_id,
        "name": req.dataset_name or ds_id,
        "raw_md": req.spec_md,
        "data_path": str(ds_dir),
        "file_count": file_count,
        "total_size": total_size,
        "formats": sorted(formats),
        "tags": req.tags,
    }
    registered_id = await registry.register(resource)
    entry = await registry.get(registered_id)
    return {"dataset_id": registered_id, "entry": entry}


@router.get("/list")
async def list_datasets():
    """列出所有已注册数据集"""
    datasets = await registry.list_all()
    return {"datasets": datasets}


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: str):
    """数据集详情（含 MD）"""
    entry = await registry.get(dataset_id)
    if not entry:
        raise HTTPException(404, f"Dataset '{dataset_id}' not found")

    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    spec_md = spec_path.read_text() if spec_path.exists() else ""
    return {**entry, "spec_md": spec_md}


@router.get("/{dataset_id}/files")
async def list_files(dataset_id: str):
    """数据集文件列表"""
    entry = await registry.get(dataset_id)
    if not entry:
        raise HTTPException(404, f"Dataset '{dataset_id}' not found")

    data_path = Path(entry.get("data_path", ""))
    files = []
    if data_path.exists():
        for f in data_path.rglob("*"):
            if f.is_file():
                files.append({
                    "name": f.name,
                    "path": str(f.relative_to(data_path)),
                    "format": f.suffix.lstrip("."),
                    "size": f.stat().st_size,
                })
    return {"files": files, "count": len(files)}


@router.post("/match-tools")
async def match_tools(req: MatchToolsRequest):
    """根据数据集和用户需求，匹配可用工具"""
    entry = await registry.get(req.dataset_id) if req.dataset_id else None

    # 收集数据集信息
    ds_info = ""
    if entry:
        spec_path = registry._get_def_dir() / f"{req.dataset_id}.md"
        ds_info = spec_path.read_text()[:1000] if spec_path.exists() else entry.get("name", "")

    # 获取所有工具
    tools = await tool_registry.list_all()
    active_tools = [t for t in tools if t.get("status") == "active"]

    # LLM 匹配
    tools_str = "\n".join(
        f"- {t['id']}: {t['name']} (type: {t['type']}, tags: {t.get('tags', [])})"
        for t in active_tools
    )

    prompt = f"""用户需求: {req.request}

数据集信息:
{ds_info}

可用工具:
{tools_str}

请判断哪些工具可以用于处理该数据集。返回 JSON 格式:
{{"matches": ["tool-id-1", "tool-id-2"], "reason": "简短说明"}}
如果没有匹配工具，返回: {{"matches": [], "reason": "说明"}}
只返回 JSON。"""

    response = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=100000,
    )

    try:
        result = _json.loads(response.strip())
    except _json.JSONDecodeError:
        result = {"matches": [], "reason": response.strip()}

    return {
        "matches": result.get("matches", []),
        "reason": result.get("reason", ""),
        "total_tools": len(active_tools),
    }


@router.get("/{dataset_id}/preview")
async def preview_dataset(dataset_id: str):
    """预览数据集 — 从 MD 文档解析文件列表 + 匹配预览工具"""
    entry = await registry.get(dataset_id)
    if not entry:
        raise HTTPException(404, f"Dataset '{dataset_id}' not found")

    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    spec_md = spec_path.read_text() if spec_path.exists() else ""

    # 从 MD 文档的"数据格式"表格中解析文件列表
    files = _parse_files_from_spec(spec_md)

    # 如果 MD 中没有文件信息，扫描实际目录
    if not files:
        data_path = Path(entry.get("data_path", ""))
        if data_path.exists():
            for f in data_path.rglob("*"):
                if f.is_file():
                    files.append({
                        "name": f.name, "format": f.suffix.lstrip("."),
                        "size": f.stat().st_size, "description": "",
                    })

    # 匹配预览工具
    tools = await tool_registry.list_all()
    active_tools = [t for t in tools if t.get("status") == "active"]
    tools_str = "\n".join(f"- {t['id']}: {t['name']}" for t in active_tools)
    prompt = f"""数据集: {spec_md[:1500]}
可用工具: {tools_str}
请选择最适合预览该数据集的工具，回复 JSON:
{{"tool_id": "xxx" 或 null, "reason": "..."}}
只返回 JSON。"""

    response = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=100000,
    )

    preview_tool = None
    try: result = _json.loads(response.strip()); preview_tool = result.get("tool_id")
    except _json.JSONDecodeError: pass

    return {
        "dataset": entry, "spec_md": spec_md, "files": files,
        "preview_tool": preview_tool, "has_preview_tool": preview_tool is not None,
    }


@router.get("/search/find")
async def search_datasets(q: str = "", tags: str = ""):
    """搜索数据集"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    results = await discoverer.search(query=q, tags=tag_list)
    return {"datasets": results}


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str):
    """删除数据集（registry + data files）"""
    entry = await registry.get(dataset_id)
    if not entry:
        raise HTTPException(404, f"Dataset '{dataset_id}' not found")

    import shutil
    # 删除数据文件
    data_path = Path(entry.get("data_path", ""))
    if data_path.exists():
        shutil.rmtree(data_path, ignore_errors=True)
    # 删除 MD 文档
    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    if spec_path.exists():
        spec_path.unlink()
    # 从 registry 移除
    await registry.unregister(dataset_id)
    return {"deleted": dataset_id}

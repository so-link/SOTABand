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
from core.resource.discoverer.data_discoverer import DataDiscoverer
from core.resource.registry.tool_registry import ToolRegistry
from core.resource.discoverer.tool_discoverer import ToolDiscoverer

router = APIRouter()
registry = DataRegistry()
discoverer = DataDiscoverer()
tool_registry = ToolRegistry()
tool_discoverer = ToolDiscoverer()
llm = create_llm_client()

SPEC_PROMPT = """你是一个数据集规格文档生成器。根据用户描述和目录扫描结果，
生成标准化的 Dataset MD 描述文档。

严格按照以下 Markdown 模板输出：

---
id: {dataset-id}
name: {数据集名称}
version: 1.0.0
type: {类型}
status: active
created: {日期}
---

# {数据集名称}

## 1. 数据集概述

## 2. 目录结构

## 3. 数据格式

| 文件 | 格式 | 大小 | 说明 |
|------|------|------|------|

## 4. 数据 Schema

| 字段 | 类型 | 说明 |
|------|------|------|

## 5. 数据来源

## 6. 使用场景

## 7. 质量评估

## 8. 访问权限

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | {日期} | 初始版本 |

规则：
1. 根据目录结构和文件格式自动推断 type（time-series/image/tabular/text/generic）
2. 自动填充目录结构和数据格式表
3. dataset-id 使用小写字母+连字符
4. 只输出 Markdown"""


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
    """NL + 目录信息 → MD 数据集描述文档"""
    context = ""
    if req.files:
        context = "目录内容:\n"
        for f in req.files:
            size_str = f"{f['size'] / 1024:.1f}KB" if f['size'] < 1048576 else f"{f['size'] / 1048576:.1f}MB"
            context += f"- {f['name']} ({f.get('format', 'unknown')}, {size_str})\n"

    response = await llm.chat(
        messages=[
            {"role": "system", "content": SPEC_PROMPT},
            {"role": "user", "content": f"用户描述: {req.description}\n{context}"},
        ],
        temperature=0.5, max_tokens=100000,
    )
    return {"spec_md": response.strip()}


@router.post("/register")
async def register_dataset(req: RegisterDatasetRequest):
    """注册数据集"""
    if not req.spec_md.strip():
        raise HTTPException(400, "specMd 不能为空")

    ds_id = req.dataset_id or "custom-dataset"
    resource = {
        "id": ds_id,
        "name": req.dataset_name or ds_id,
        "raw_md": req.spec_md,
        "data_path": req.data_path,
        "file_count": req.file_count,
        "total_size": req.total_size,
        "formats": req.formats,
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
    """预览数据集 — 自动匹配预览工具"""
    entry = await registry.get(dataset_id)
    if not entry:
        raise HTTPException(404, f"Dataset '{dataset_id}' not found")

    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    spec_md = spec_path.read_text() if spec_path.exists() else ""

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
    try:
        result = _json.loads(response.strip())
        preview_tool = result.get("tool_id")
    except _json.JSONDecodeError:
        pass

    # 获取文件列表
    data_path = Path(entry.get("data_path", ""))
    files = []
    if data_path.exists():
        for f in data_path.rglob("*"):
            if f.is_file():
                files.append({"name": f.name, "format": f.suffix.lstrip("."), "size": f.stat().st_size})

    return {
        "dataset": entry,
        "spec_md": spec_md,
        "files": files,
        "preview_tool": preview_tool,
        "has_preview_tool": preview_tool is not None,
    }


@router.get("/search/find")
async def search_datasets(q: str = "", tags: str = ""):
    """搜索数据集"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    results = await discoverer.search(query=q, tags=tag_list)
    return {"datasets": results}

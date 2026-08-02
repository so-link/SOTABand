"""数据管理路由 — 扫描、规格生成、注册、预览、处理"""

import os
import re
import time
import json as _json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.schemas.data_schemas import (
    ScanDirectoryRequest,
    GenerateDataSpecRequest,
    RegisterDatasetRequest,
    MatchToolsRequest,
)
from core.llm.client import create_llm_client
from core.resource.registry.data_registry import DataRegistry

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff', '.tif'}


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
    """注册数据集 — 文件保持在原位置，不复制"""
    if not req.spec_md.strip():
        raise HTTPException(400, "specMd 不能为空")

    ds_id = req.dataset_id or f"{req.dataset_name}_{int(time.time())}" if req.dataset_name else f"dataset_{int(time.time())}"

    # 数据集文件保持在原位置，从上传的文件路径推断 data_path
    data_path = ""
    file_count = req.file_count
    total_size = req.total_size
    formats = set(req.formats)

    if req.source_files:
        # 用第一个文件的父目录作为数据集根路径
        first = Path(req.source_files[0])
        if first.exists():
            data_path = str(first.parent)
        # 统计实际文件信息
        file_count = 0
        total_size = 0
        formats = set()
        for fp in req.source_files:
            src = Path(fp)
            if src.exists() and src.is_file():
                file_count += 1
                total_size += src.stat().st_size
                formats.add(src.suffix.lstrip(".").lower())

    resource = {
        "id": ds_id,
        "name": req.dataset_name or ds_id,
        "raw_md": req.spec_md,
        "data_path": data_path,
        "file_count": file_count,
        "total_size": total_size,
        "formats": sorted(formats),
        "tags": req.tags,
    }

    # 在 spec_md 末尾追加数据集目录信息
    if data_path:
        path_info = f"\n\n---\n## 数据集目录\n- **路径**: `{data_path}`\n- **文件数**: {file_count}\n- **总大小**: {total_size} bytes\n- **格式**: {', '.join(sorted(formats))}\n"
        resource["raw_md"] = req.spec_md + path_info

    registered_id = await registry.register(resource)
    entry = await registry.get(registered_id)

    # 注册后异步匹配预览工具（存到 registry，预览时直接用缓存）
    import asyncio
    asyncio.create_task(_match_preview_tool(registered_id, req.spec_md))

    # 如果没有前端传入的标签，异步调用 LLM 自动生成
    if not req.tags:
        asyncio.create_task(_auto_generate_dataset_tags(registered_id, req.dataset_name or registered_id, req.spec_md))

    return {"dataset_id": registered_id, "entry": entry}


async def _match_preview_tool(dataset_id: str, spec_md: str):
    """后台匹配预览工具，结果更新到 registry"""
    try:
        tools = await tool_registry.list_all()
        active_tools = [t for t in tools if t.get("status") == "active"]
        if not active_tools:
            return
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
        result = _json.loads(response.strip())
        preview_tool = result.get("tool_id")
        if preview_tool:
            await registry.update(dataset_id, {"preview_tool": preview_tool})
    except Exception:
        pass  # 后台任务失败不影响注册


async def _auto_generate_dataset_tags(dataset_id: str, name: str, spec_md: str):
    """LLM 自动生成数据集标签，更新到 registry"""
    try:
        from core.llm.client import get_llm_client
        llm = get_llm_client()
        prompt = f"""根据以下数据集信息，生成3-5个简短的中文标签（每个2-4字），用于数据集分类和检索。直接返回 JSON 数组。

数据集名称: {name}
数据集描述: {spec_md[:500]}

返回格式: ["标签1", "标签2", "标签3"]"""
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200, timeout=30,
        )
        print(f"[_auto_generate_dataset_tags] LLM返回 dataset_id={dataset_id}: {repr(response[:200])}")

        tags = _extract_tags_json(response)
        if not tags:
            import re
            matches = re.findall(r'[\u4e00-\u9fff]{2,4}', response)
            tags = list(dict.fromkeys(matches))[:5]
            if tags:
                print(f"[_auto_generate_dataset_tags] 回退正则提取 dataset_id={dataset_id}: {tags}")

        if tags:
            entry = await registry.get(dataset_id)
            if entry:
                entry["tags"] = tags
                await registry._save()
                print(f"[_auto_generate_dataset_tags] 标签已更新 dataset_id={dataset_id}: {tags}")
        else:
            print(f"[_auto_generate_dataset_tags] 无法提取标签 dataset_id={dataset_id}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[_auto_generate_dataset_tags] 异常 dataset_id={dataset_id}: {e}")


def _extract_tags_json(text: str) -> list[str] | None:
    """多策略从 LLM 返回中提取 JSON 标签数组"""
    import re
    clean = text.strip()
    for prefix in ['```json', '```', '`']:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
    for suffix in ['```', '`']:
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)].strip()
    if clean.startswith('[') and ']' in clean:
        try:
            end = clean.rindex(']') + 1
            arr = _json.loads(clean[:end])
            if isinstance(arr, list):
                return [str(t).strip() for t in arr if str(t).strip()]
        except _json.JSONDecodeError:
            pass
    return None


@router.get("/repository")
async def get_dataset_repository():
    """获取数据集仓库列表（含标签统计和缩略图）"""
    datasets = await registry.list_all()
    # 计算标签统计
    tag_stats: dict[str, int] = {}
    for ds in datasets:
        for tag in ds.get("tags", []):
            tag_stats[tag] = tag_stats.get(tag, 0) + 1

    # 为每个数据集查找第一张图片的缩略图路径
    for ds in datasets:
        ds_id = ds.get("id", "")
        data_path = ds.get("data_path", "")
        img = _find_first_image(ds_id, data_path)
        if img:
            ds["thumbnail"] = f"/api/data/{ds_id}/thumbnail"
        else:
            # 无图片：尝试提取第一句话作为预览
            preview = _get_first_sentence(ds_id, data_path, ds.get("description", ""))
            if preview:
                ds["description_preview"] = preview

    return {"datasets": datasets, "tag_stats": tag_stats}


@router.get("/list")
async def list_datasets():
    """列出所有已注册数据集"""
    datasets = await registry.list_all()
    return {"datasets": datasets}


@router.post("/batch-generate-tags")
async def batch_generate_tags():
    """为所有无标签的数据集批量生成标签（LLM 异步生成）"""
    datasets = await registry.list_all()
    no_tag_ids = [ds["id"] for ds in datasets if not ds.get("tags")]
    if not no_tag_ids:
        return {"message": "所有数据集已有标签", "count": 0}

    import asyncio
    for ds_id in no_tag_ids:
        entry = await registry.get(ds_id)
        if not entry:
            continue
        spec_path = registry._get_def_dir() / f"{ds_id}.md"
        spec_md = spec_path.read_text() if spec_path.exists() else ""
        asyncio.create_task(_auto_generate_dataset_tags(
            ds_id,
            entry.get("name", ds_id),
            spec_md
        ))

    return {"message": f"已触发 {len(no_tag_ids)} 个数据集的标签生成（后台异步进行）", "count": len(no_tag_ids), "dataset_ids": no_tag_ids}


def _find_first_image(dataset_id: str, data_path: str | None = None) -> Path | None:
    """查找数据集中第一张图片文件"""
    # 优先使用 registry 中记录的 data_path
    if data_path:
        data_dir = Path(data_path)
    else:
        data_dir = registry._get_def_dir().parent / "datasets" / dataset_id
    if not data_dir.exists():
        return None
    for f in sorted(data_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
            return f
    return None


def _get_first_sentence(dataset_id: str, data_path: str = "", description: str = "") -> str:
    """获取数据集的第一句话作为预览：
    1. 优先找数据目录中第一个 .md 文件的内容的第一句话
    2. 如果没有 .md 文件，取 description 的第一句话
    """
    # 尝试读取数据目录中的第一个 md 文件
    if data_path:
        data_dir = Path(data_path)
    else:
        data_dir = registry._get_def_dir().parent / "datasets" / dataset_id
    if data_dir.exists() and data_dir.is_dir():
        md_files = sorted([f for f in data_dir.iterdir() if f.is_file() and f.suffix.lower() == '.md'])
        for md_file in md_files:
            try:
                text = md_file.read_text()
                # 去掉 markdown 标题符号，取第一个有意义的句子
                lines = [l.strip() for l in text.split('\n') if l.strip() and not l.strip().startswith('#')]
                if lines:
                    return _extract_first_sentence(lines[0])
            except Exception:
                continue

    # 回退到 description
    if description:
        return _extract_first_sentence(description)

    return ""


def _extract_first_sentence(text: str) -> str:
    """从文本中提取第一句话（截取到第一个句号、感叹号或问号，限制 80 字符）"""
    text = text.strip()
    if not text:
        return ""
    # 按中文/英文句子结束符截断
    m = re.search(r'[。！？!?.]', text)
    if m:
        sentence = text[:m.end()]
    else:
        sentence = text
    if len(sentence) > 80:
        sentence = sentence[:80] + '…'
    return sentence


@router.get("/{dataset_id}/thumbnail")
async def get_dataset_thumbnail(dataset_id: str):
    """返回数据集的第一张图片缩略图"""
    entry = await registry.get(dataset_id)
    data_path = entry.get("data_path", "") if entry else ""
    img_path = _find_first_image(dataset_id, data_path)
    if img_path is None:
        raise HTTPException(404, f"No image found in dataset '{dataset_id}'")
    return FileResponse(img_path, media_type=f"image/{img_path.suffix.lstrip('.')}")


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
                        "name": str(f.relative_to(data_path)), "format": f.suffix.lstrip("."),
                        "size": f.stat().st_size, "description": "",
                    })

    # 预览工具：优先用缓存，没有缓存则跳过（避免每次点击都调 LLM）
    preview_tool = entry.get("preview_tool")

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

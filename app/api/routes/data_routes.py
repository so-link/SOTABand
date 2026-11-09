"""数据管理路由 — 扫描、规格生成、注册、预览、处理"""

import os
import re
import time
import json as _json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from app.api.schemas.data_schemas import (
    ScanDirectoryRequest,
    GenerateDataSpecRequest,
    RegisterDatasetRequest,
    MatchToolsRequest,
)
from core.llm.client import create_llm_client
from core.resource.registry.data_registry import DataRegistry
from core.user_context import get_current_user_id

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json"}
_PREVIEW_MAX_CHARS = 120


def _find_first_image(dataset_id: str, data_path: str) -> str:
    """找数据集里的第一张图片（绝对路径），用于列表缩略图；找不到返回空串。

    历史遗留：``/repository`` 一直在调用这个函数，但仓库里从来没有它的定义，
    该接口实际是 500（因为对应的"数据集仓库"视图没有入口，一直没暴露）。
    """
    try:
        dir_path = registry.resolve_data_path(data_path)
        if not dir_path.exists() or not dir_path.is_dir():
            return ""
        for p in sorted(dir_path.rglob("*")):
            if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES:
                return str(p)
    except Exception:
        # 缩略图只是列表的装饰，任何异常都不能让整个仓库列表失败
        return ""
    return ""


def _get_first_sentence(dataset_id: str, data_path: str, description: str) -> str:
    """取一段文字预览：优先数据集描述首句，其次第一个文本文件的首行。"""
    text = (description or "").strip()
    if text:
        first = text.splitlines()[0].strip()
        if first:
            return first[:_PREVIEW_MAX_CHARS]
    try:
        dir_path = registry.resolve_data_path(data_path)
        if dir_path.exists() and dir_path.is_dir():
            for p in sorted(dir_path.rglob("*")):
                if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES:
                    with p.open("r", encoding="utf-8", errors="replace") as f:
                        line = f.readline().strip()
                    if line:
                        return line[:_PREVIEW_MAX_CHARS]
    except Exception:
        pass
    return ""


def _spec_column_roles(headers: list[str]) -> list[str]:
    """按表头文字判定每一列的语义，返回 role 列表（name/format/size/desc/'')。

    规范文档里的文件表格是人手/LLM 写的，列序和列数都不固定：既有
    `| 文件 | 格式 | 大小 | 说明 |`，也有把两组文件并排的
    `| 文件 | 标的 | 文件 | 标的 |`。按固定下标取列会把"标的"当成格式，
    前端据此判定文件类型就会全部失配，所以必须按表头语义映射。
    """
    roles = []
    for h in headers:
        t = h.strip()
        if re.search(r"文件|文件名|名称|^file$|路径|path", t, re.I):
            roles.append("name")
        elif re.search(r"大小|体积|size", t, re.I):
            roles.append("size")
        elif re.search(r"格式|format|扩展名|ext$", t, re.I):
            roles.append("format")
        elif re.search(r"标的|说明|描述|备注|description|desc|用途|内容", t, re.I):
            roles.append("desc")
        else:
            roles.append("")
    return roles


def _spec_col_value(role: str, value: str, rec: dict) -> None:
    """把一列的文本写进当前文件记录。

    表头写"格式"但填的是中文标的名的（LLM 常这么写），按说明处理，
    避免把「上证50ETF华夏」当成文件扩展名。
    """
    v = value.strip()
    if not v:
        return
    if role == "size":
        m = re.match(r"^([\d.]+)\s*(GB|MB|KB|B)?$", v, re.I)
        if m:
            unit = (m.group(2) or "B").upper()
            factor = {"B": 1, "KB": 1024, "MB": 1048576, "GB": 1073741824}[unit]
            rec["size"] = int(float(m.group(1)) * factor)
    elif role == "format":
        if re.fullmatch(r"[A-Za-z0-9]{1,8}", v):
            rec["format"] = v.lower()
        else:
            rec["description"] = rec["description"] or v
    elif role == "desc":
        rec["description"] = rec["description"] or v


def _parse_files_from_spec(spec_md: str) -> list[dict]:
    """从 MD 规范的「数据格式」表格中解析文件列表（列序按表头语义识别）"""
    files: list[dict] = []
    in_section = False
    roles: list[str] = []
    for line in spec_md.split("\n"):
        if not in_section:
            if "数据格式" in line:
                in_section = True
            continue
        if line.startswith("##"):
            break
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if not parts or all(not p for p in parts):
            continue
        # 分隔行 |---|---|
        if all(re.fullmatch(r":?-{2,}:?", p) for p in parts):
            continue
        # 首个表格行是表头：据此确定列语义，后续行按语义填字段
        if not roles:
            roles = _spec_column_roles(parts)
            if "name" not in roles:
                return []
            continue
        rec: dict | None = None
        for i, cell in enumerate(parts):
            role = roles[i] if i < len(roles) else ""
            if role == "name":
                # 并排多组文件：遇到新的文件列就另起一条记录
                if not cell:
                    continue
                rec = {"name": cell, "format": "", "size": 0, "description": ""}
                files.append(rec)
            elif rec is not None:
                _spec_col_value(role, cell, rec)
    # 丢掉不像文件名的行（例如把表说明误判成文件列）
    return [f for f in files if "." in f["name"] or "/" in f["name"]]


from core.resource.discoverer.data_discoverer import DataDiscoverer
from core.resource.registry.tool_registry import ToolRegistry
from core.resource.discoverer.tool_discoverer import ToolDiscoverer

router = APIRouter()
registry = DataRegistry()
discoverer = DataDiscoverer()
tool_registry = ToolRegistry()
tool_discoverer = ToolDiscoverer()
llm = create_llm_client()


async def _owned_dataset(dataset_id: str) -> dict:
    """按 id 取数据集，并校验归属/可见性。

    ``registry.get()`` 读的是随项目目录走的共享 registry（内置 + 私有合并），
    **不做归属过滤**；而数据集是私有资源，跨用户读写必须在 API 层拦住
    （见 core/user_context.py 的设计约束：共享资源访问私有数据时必须过滤）。
    跨用户访问一律 404（与"不存在"同响应），避免泄露条目是否存在。
    """
    entry = await registry.get(dataset_id)
    if not entry or not registry.is_visible(entry, get_current_user_id()):
        raise HTTPException(404, f"Dataset '{dataset_id}' not found")
    return entry


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
    """扫描目录，返回文件列表和格式分析。

    只能扫**当前用户目录**内的路径：任意路径枚举等于把本机文件系统
    开放成接口（此前可扫 /etc 等任意位置）。
    """
    from core.user_paths import user_home

    try:
        base = user_home().resolve()
        target = (base / req.path).resolve() if not Path(req.path).is_absolute() else Path(req.path).resolve()
        target.relative_to(base)  # 越界即 ValueError
    except ValueError:
        raise HTTPException(403, "不允许访问用户目录之外的路径")

    if not target.exists():
        raise HTTPException(404, f"目录不存在: {req.path}")

    # 空串/根路径即用户目录本身：rglob 会扫到 platform.db、资源注册表等
    # 平台文件，收敛为只扫受控子目录
    scan_dirs = [base / s for s in ("uploads", "plots")] if target == base else [target]

    files = []
    total_size = 0
    formats = set()

    for d in scan_dirs:
        if not d.is_dir():
            continue
        for entry in d.rglob("*"):
            if entry.is_file():
                size = entry.stat().st_size
                fmt = entry.suffix.lstrip(".").lower()
                files.append({
                    "name": entry.name,
                    "path": str(entry.relative_to(target)),
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
    """NL + 文件信息（含描述） → MD 数据集描述文档（SSE 流式）"""
    context_lines = ["数据文件列表:"]
    for f in req.files:
        size_str = f"{f['size'] / 1024:.1f}KB" if f.get('size', 0) < 1048576 else f"{f.get('size', 0) / 1048576:.1f}MB"
        desc = f.get('description', '')
        desc_str = f" — {desc}" if desc else ""
        context_lines.append(f"- {f.get('name', 'unknown')} ({f.get('format', 'unknown')}, {size_str}){desc_str}")

    async def event_stream():
        try:
            full = ""
            async for kind, token in llm.chat_stream_events(
                messages=[
                    {"role": "system", "content": SPEC_PROMPT},
                    {"role": "user", "content": f"数据集描述: {req.description}\n\n" + "\n".join(context_lines)},
                ],
                temperature=0.3, max_tokens=100000,
            ):
                if kind == "reasoning":
                    yield {"event": "reasoning", "data": _json.dumps({"token": token}, ensure_ascii=False)}
                else:
                    full += token
                    yield {"event": "delta", "data": _json.dumps({"token": token}, ensure_ascii=False)}

            spec_md = full.strip()

            # 同时生成标签（小调用，非流式，在 done 里返回）
            tags = []
            try:
                tag_response = await llm.chat(
                    messages=[{
                        "role": "user",
                        "content": f"""根据以下数据集信息，生成3-5个简短的中文标签（每个2-4字），用于数据集分类和检索。只输出 JSON 数组，不要输出任何其他内容。

数据集描述: {req.description}
{spec_md[:300]}

返回格式: ["标签1", "标签2", "标签3"]"""
                    }],
                    temperature=0.3, max_tokens=1000, timeout=30,
                )
                tags = _extract_tags_json(tag_response)
                if not tags:
                    matches = re.findall(r'[\u4e00-\u9fff]{2,4}', tag_response)
                    tags = list(dict.fromkeys(matches))[:5]
            except Exception:
                pass

            yield {"event": "done", "data": _json.dumps({"spec_md": spec_md, "tags": tags}, ensure_ascii=False)}

        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else f"{type(exc).__name__}: {exc}"
            yield {"event": "error", "data": _json.dumps({"detail": detail}, ensure_ascii=False)}

    return EventSourceResponse(event_stream())


@router.post("/register")
async def register_dataset(req: RegisterDatasetRequest):
    """注册数据集 — 文件保持在原位置，不复制"""
    if not req.spec_md.strip():
        raise HTTPException(400, "specMd 不能为空")

    # id 会被直接拼进 spec 文件路径（definitions/<id>.md），必须消毒；
    # 从 dataset_name 派生的 id 同样可能含 "/"、".."，不合法则退回时间戳 id
    from core.resource.paths import is_valid_resource_id
    if req.dataset_id and not is_valid_resource_id(req.dataset_id):
        raise HTTPException(400, "datasetId 含非法字符（仅允许小写字母/数字/-/_）")
    ds_id = req.dataset_id or f"{req.dataset_name}_{int(time.time())}" if req.dataset_name else f"dataset_{int(time.time())}"
    if not is_valid_resource_id(ds_id):
        ds_id = f"dataset_{int(time.time())}"

    # 数据集文件保持在原位置，从上传的文件路径推断 data_path
    data_path = ""
    file_count = req.file_count
    total_size = req.total_size
    formats = set(req.formats)

    # 前端只持有 file_id：先解析成当前用户目录内的真实路径，再走原逻辑
    source_files = list(req.source_files)
    if req.source_file_ids:
        from core import user_files
        for fid in req.source_file_ids:
            try:
                source_files.append(str(user_files.resolve(fid)))
            except FileNotFoundError:
                continue
        if not source_files:
            raise HTTPException(400, "sourceFileIds 均无法解析为有效文件")

    # sourceFiles 是客户端原样传入的路径：resolve 后必须仍在当前用户目录内，
    # 否则可借注册别人的 uploads 路径读取他人文件
    from core.user_paths import user_home
    _home = user_home().resolve()
    for fp in req.source_files:
        try:
            Path(fp).expanduser().resolve().relative_to(_home)
        except ValueError:
            raise HTTPException(400, f"sourceFiles 只能引用当前用户目录内的文件: {fp}")

    if source_files:
        # 用第一个文件的父目录作为数据集根路径
        first = Path(source_files[0])
        if first.exists():
            data_path = str(first.parent)
        # 统计实际文件信息
        file_count = 0
        total_size = 0
        formats = set()
        for fp in source_files:
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
        from core.llm.client import create_llm_client
        llm = create_llm_client()
        prompt = f"""根据以下数据集信息，生成3-5个简短的中文标签（每个2-4字），用于数据集分类和检索。只输出 JSON 数组，不要输出任何其他内容。

数据集名称: {name}
数据集描述: {spec_md[:500]}

返回格式: ["标签1", "标签2", "标签3"]"""
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=1000, timeout=30,
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
            await registry.update(dataset_id, {"tags": tags})
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
    """获取数据集仓库列表（含标签统计、缩略图与**可用性标记**）。

    为什么要带 is_local：注册表 ``data/registry.json`` 随项目目录走，而数据本身
    不随（``data_path`` 常是原机器的绝对路径，如 ``/Users/leo/...``）。从别人环境
    复制项目后，条目能看到但文件打不开 —— 前端需要据此区分"可选"与"不可选"，
    并在选择框里给出具体原因，而不是只显示一句"暂无数据集"。
    """
    datasets = await registry.list_all()

    current_user = get_current_user_id()
    for ds in datasets:
        ds["is_local"] = registry.is_present_locally(ds)
        owner = ds.get("owner")
        # 无 owner 的历史条目：数据在本机就视为本人的（与 is_visible 的启发式一致）
        ds["owner_match"] = (owner == current_user) if owner else ds["is_local"]
        ds.setdefault("data_path", "")

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
async def list_datasets(
    available_only: bool = Query(False, description="仅返回本机有数据的数据集"),
    include_others: bool = Query(False, description="包含其他用户的数据集（默认只返回本人的）"),
):
    """列出已注册数据集（**按归属隔离**）。

    数据集是「谁的资源」而不是共享资源。注册表文件随项目目录走，
    从他人环境复制项目时会连同对方的注册记录一起带过来，这些记录
    的 data_path 指向对方机器，本机并没有数据。

    默认只返回属于当前用户的数据集：
    - 带 owner 且 == 当前用户 → 返回
    - 早期无 owner 的历史条目 → 本机确实有数据的视为本人的（平滑迁移）

    参数：
    - include_others=true ：返回全部（含他人条目），仅供排查/迁移使用
    - available_only=true ：只返回本机确实有数据的
    """
    # include_others 会连他人的 data_path 一起下发（check_availability 返回条目
    # 全文），属于跨用户信息泄露，只开放给超管排查/迁移用。
    if include_others:
        from app.api.deps import current_role
        if current_role() != "admin":
            raise HTTPException(403, "仅超级用户可以查看他人的数据集")

    current_user = get_current_user_id()
    entries = await registry.list_all(owner=None if include_others else current_user)

    checked = []
    for d in entries:
        info = registry.check_availability(d)
        # 标注归属关系，便于前端区分「我的」与「他人的」
        owner = d.get("owner")
        info["owner"] = owner
        info["is_owner"] = (owner == current_user) if owner else \
            registry.is_present_locally(d)
        checked.append(info)

    if available_only:
        checked = [d for d in checked if d.get("available")]

    return {
        "datasets": checked,
        "total": len(checked),
        "current_user": current_user,
        "available_count": sum(1 for d in checked if d.get("available")),
        "unavailable_count": sum(1 for d in checked if not d.get("available")),
    }


@router.post("/claim-local")
async def claim_local_datasets():
    """把本机有数据、但缺少归属标记的历史数据集认领到当前用户。

    用于一次性迁移：这些条目确认是自己的数据后打上 owner，
    之后列表就走精确的归属匹配，不再依赖"本机是否有数据"的启发式判断。
    """
    claimed = registry.claim_local_orphans()
    return {"claimed": claimed, "owner": get_current_user_id()}


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: str):
    """数据集详情（含 MD）"""
    entry = await _owned_dataset(dataset_id)

    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    spec_md = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    return {**entry, "spec_md": spec_md}


@router.get("/{dataset_id}/archive")
async def download_archive(dataset_id: str):
    """把数据集目录打包为 zip 下载（与工具「导出」对应的能力）。"""
    import io
    import zipfile
    from fastapi.responses import Response

    entry = await _owned_dataset(dataset_id)

    data_path = registry.resolve_data_path(entry.get("data_path", ""))
    if not data_path.is_dir():
        raise HTTPException(404, f"数据集 '{dataset_id}' 的数据目录不存在: {data_path}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(data_path.rglob("*")):
            if not f.is_file():
                continue
            zf.write(f, arcname=str(f.relative_to(data_path)))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{dataset_id}.zip"',
        },
    )


@router.get("/{dataset_id}/files")
async def list_files(dataset_id: str):
    """数据集文件列表"""
    entry = await _owned_dataset(dataset_id)

    data_path = registry.resolve_data_path(entry.get("data_path", ""))
    if not data_path.exists():
        # 明确告知"数据不在本机"，而不是静默返回空列表
        raise HTTPException(
            404,
            f"数据集 '{dataset_id}' 的数据目录不存在: {data_path}。"
            f"（该数据集可能注册于其他机器）"
        )

    files = []
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
    # 传了 dataset_id 就必须是自己可见的数据集，否则 404：此前只取返回值，
    # 不存在/他人的 id 会一路走到 LLM 并返回 200，等于用平台额度做无效请求。
    entry = await _owned_dataset(req.dataset_id) if req.dataset_id else None

    # 收集数据集信息
    ds_info = ""
    if entry:
        spec_path = registry._get_def_dir() / f"{req.dataset_id}.md"
        ds_info = spec_path.read_text(encoding="utf-8")[:1000] if spec_path.exists() else entry.get("name", "")

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


@router.get("/{dataset_id}/table")
async def preview_table(dataset_id: str, path: str = Query(..., description="数据集内相对路径"), limit: int = Query(200)):
    """读取数据集内的表格文件（csv/xlsx/xls/parquet/tsv），返回统一表格结构供中栏预览。"""
    entry = await _owned_dataset(dataset_id)

    data_path = registry.resolve_data_path(entry.get("data_path", ""))
    # 防目录穿越：resolve 后必须仍在数据集目录内（前缀字符串比对会把
    # 兄弟目录 ds_backup 误判为 ds 之内，见 raw 接口的 relative_to 写法）
    target = (data_path / path).resolve()
    try:
        target.relative_to(data_path.resolve())
    except ValueError:
        raise HTTPException(404, f"文件不存在: {path}")
    if not target.is_file():
        raise HTTPException(404, f"文件不存在: {path}")

    suffix = target.suffix.lower()
    try:
        import pandas as pd

        if suffix == ".csv":
            df = pd.read_csv(target, nrows=limit)
        elif suffix == ".tsv":
            df = pd.read_csv(target, sep="\t", nrows=limit)
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(target, nrows=limit)
        elif suffix in (".parquet", ".pq"):
            df = pd.read_parquet(target)
            df = df.head(limit)
        else:
            raise HTTPException(400, f"不支持的表格格式: {suffix}")
    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(500, "服务端缺少 pandas/openpyxl/pyarrow 依赖，无法预览该格式")
    except Exception as exc:
        raise HTTPException(500, f"解析失败: {type(exc).__name__}: {exc}")

    # NaN/NaT 等无法直接 JSON 序列化的值统一转字符串
    df = df.astype(object).where(pd.notna(df), None)
    return {
        "name": target.name,
        "headers": [str(c) for c in df.columns],
        "rows": [[None if v is None else str(v) for v in row] for row in df.itertuples(index=False, name=None)],
    }


@router.get("/{dataset_id}/raw")
async def get_raw_file(dataset_id: str, path: str = Query(..., description="数据集内相对路径")):
    """按数据集内**相对路径**提供原文件（图片/Markdown/文本预览用）。

    数据集预览此前回落到 ``/api/file/download?path=``（绝对路径），随该
    接口删除。这里以「数据集 id + 相对路径」定位：id 经注册表白名单解析，
    相对路径拼接后强制仍在数据集目录内——浏览器永远不需要知道数据落在
    磁盘哪里。
    """
    from fastapi.responses import FileResponse
    import mimetypes

    entry = await _owned_dataset(dataset_id)

    data_path = registry.resolve_data_path(entry.get("data_path", ""))
    # 兼容历史数据：旧版 MD 规格里存过绝对路径，等价处理（仍限数据集内）
    candidate = Path(path)
    target = candidate.resolve() if candidate.is_absolute() else (data_path / path).resolve()
    try:
        target.relative_to(data_path.resolve())
    except ValueError:
        raise HTTPException(403, "不允许访问数据集目录之外的路径")
    if not target.is_file():
        raise HTTPException(404, f"文件不存在: {path}")

    mime_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        target,
        media_type=mime_type or "application/octet-stream",
        filename=target.name,
        content_disposition_type="inline",
    )


@router.get("/{dataset_id}/preview")
async def preview_dataset(dataset_id: str):
    """预览数据集 — 从 MD 文档解析文件列表 + 匹配预览工具"""
    entry = await _owned_dataset(dataset_id)

    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    spec_md = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""

    # 文件清单以**实际目录扫描**为准：只有扫出来的条目才带 path，前端取文件
    # 内容要靠它拼 URL；规范表格里的清单是人写的，可能缺文件、多文件、
    # 列序错位，只用来补充「说明」这一栏。
    files = _parse_files_from_spec(spec_md)
    meta = {f["name"]: f for f in files}
    data_path = registry.resolve_data_path(entry.get("data_path", ""))
    scanned = []
    if data_path.exists():
        for f in sorted(data_path.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(data_path))
            info = meta.get(rel) or meta.get(f.name) or {}
            scanned.append({
                "name": f.name, "path": rel,
                "format": f.suffix.lstrip(".").lower(),
                "size": f.stat().st_size,
                "description": info.get("description", ""),
            })
    if scanned:
        files = scanned

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
    results = await discoverer.search(query=q, tags=tag_list,
                                      owner=get_current_user_id())
    return {"datasets": results}


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str):
    """删除数据集（registry + data files）"""
    entry = await _owned_dataset(dataset_id)

    import shutil
    # 删除数据文件：仅限「托管目录内」的数据集副本。
    # 上传注册的数据集 data_path 常指向上传器原位置（托管目录之外），
    # 那属于用户的源文件，删除数据集只注销、不删源文件；
    # 托管目录（private/data/datasets）之下才允许整目录删除。
    data_path = registry.resolve_data_path(entry.get("data_path", ""))
    managed_root = registry._get_data_dir().resolve()
    files_removed = False
    if data_path.exists() and data_path.is_dir():
        resolved = data_path.resolve()
        if resolved in (managed_root, managed_root.parent, managed_root.parent.parent):
            raise HTTPException(400, "拒绝删除数据根目录")
        if resolved.is_relative_to(managed_root):
            shutil.rmtree(resolved, ignore_errors=True)
            files_removed = True
    # 删除 MD 文档
    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    if spec_path.exists():
        spec_path.unlink()
    # 从 registry 移除
    await registry.unregister(dataset_id)
    return {"deleted": dataset_id, "files_removed": files_removed}

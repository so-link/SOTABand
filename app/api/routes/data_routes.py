"""数据管理路由 — 扫描、规格生成、注册、预览、处理"""

import os
import time
import json as _json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.data_schemas import (
    ScanDirectoryRequest,
    GenerateDataSpecRequest,
    RegisterDatasetRequest,
    MatchToolsRequest,
)
from core.llm.client import create_llm_client
from core.resource.registry.data_registry import DataRegistry
from app.api.deps import get_auth_context, get_optional_auth
from core.auth.context import AuthContext
from core.db.session import get_db_session
from core.auth.platform_admin import is_platform_admin
from core.file_guard import (
    PathAccessError,
    is_sensitive_path,
    resolve_checked_path,
    validate_dataset_deletion,
    validate_dataset_path,
)
from core.user_context import resolve_owner_id

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


# ── 归属与路径校验（管理员评审意见 1 + D3）──────────────────────────


def _is_machine_owner(value: str | None) -> bool:
    """机器身份（8.1 之前 local-xxxx）判定；兼容测试替身 registry。"""
    fn = getattr(registry, "is_machine_owner", None)
    if callable(fn):
        try:
            return bool(fn(value))
        except Exception:
            pass
    return str(value or "").startswith("local-")


async def _require_owner_dataset_async(ctx: AuthContext, dataset_id: str) -> dict:
    """数据集归属校验。

    - 有 owner：必须等于当前用户，否则 **404**（不泄露"存在但不属于你"）；
    - 历史无 owner：本机有数据视为本人（与 ``DataRegistry.belongs_to`` 一致）。
    """
    me = resolve_owner_id(ctx)
    entry = await registry.get(dataset_id)
    if entry is None:
        raise HTTPException(404, "数据集不存在")
    owner = str(entry.get("owner") or "")
    if owner and not _is_machine_owner(owner):
        if owner != me:
            raise HTTPException(404, "数据集不存在")
    elif not registry.is_present_locally(entry):
        # 无 owner 或机器身份历史条目：本机有数据视为本人（升级兼容）
        raise HTTPException(404, "数据集不存在")
    return entry


def _guard_dir(raw: str, owner_id: str | None = None) -> Path:
    """目录路径边界 + 归属校验（扫描/浏览类接口，D3 / v3 评审）。

    必须传 ``owner_id``：否则上传目录的归属规则不生效，任何登录用户都能
    扫描他人上传目录（实测越权）。
    """
    try:
        return resolve_checked_path(raw, must_exist=True, owner_id=owner_id)
    except PathAccessError as exc:
        raise HTTPException(403, str(exc)) from exc

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
async def scan_directory(
    req: ScanDirectoryRequest, ctx: AuthContext = Depends(get_auth_context)
):
    """扫描目录，返回文件列表和格式分析（仅允许根内 + 本人的目录，D3）"""
    path = _guard_dir(req.path, resolve_owner_id(ctx))

    files = []
    total_size = 0
    formats = set()

    for entry in path.rglob("*"):
        # 目录扫描不得旁路列出敏感条目（storage/.env/.git 等）
        if is_sensitive_path(entry):
            continue
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
    spec_md = response.strip()

    # 同时生成标签
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

    return {"spec_md": spec_md, "tags": tags}


@router.post("/register")
async def register_dataset(
    req: RegisterDatasetRequest,
    ctx: AuthContext = Depends(get_auth_context),
):
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

    # 路径边界（D3/P1-10）：注册任意目录会绕过文件接口的允许根，
    # 因此 data_path 必须在数据允许根内、不能是资源根本身、不能与他人数据集重叠。
    if data_path:
        try:
            data_path = str(
                validate_dataset_path(data_path, owner_id=resolve_owner_id(ctx))
            )
            if not Path(data_path).exists():
                raise HTTPException(404, f"数据集目录不存在: {data_path}")
        except PathAccessError as exc:
            raise HTTPException(400, str(exc)) from exc

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

    # 已存在同 id：只有资源所有者/管理员可以更新（否则等于接管他人条目）
    existing = await registry.get(ds_id)
    if existing is not None:
        await _require_owner_dataset_async(ctx, ds_id)

    registered_id = await registry.register(resource, owner=resolve_owner_id(ctx))
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
async def get_dataset_repository(
    include_others: bool = False,
    ctx: AuthContext = Depends(get_auth_context),
):
    """获取数据集仓库列表（**默认只本人**；含标签统计/缩略图/可用性标记）。

    为什么要带 is_local：注册表 ``data/registry.json`` 随项目目录走，而数据本身
    不随（``data_path`` 常是原机器的绝对路径，如 ``/Users/leo/...``）。从别人环境
    复制项目后，条目能看到但文件打不开 —— 前端需要据此区分"可选"与"不可选"，
    并在选择框里给出具体原因，而不是只显示一句"暂无数据集"。

    ⚠️ 多用户：默认只返回当前用户的数据集；``include_others=true`` 仅**平台
    管理员**可用（否则会泄露他人数据集的名称/路径）。租户 Owner/Admin 不是
    平台管理员 —— 跨租户可见性必须由部署方显式授权。
    """
    current_user = resolve_owner_id(ctx)
    if include_others and not is_platform_admin(ctx):
        raise HTTPException(403, "只有平台管理员可以查看他人的数据集")
    datasets = await registry.list_all(owner=None if include_others else current_user)

    for ds in datasets:
        ds["is_local"] = registry.is_present_locally(ds)
        owner = ds.get("owner")
        # 无 owner 的历史条目：数据在本机就视为本人的（与 is_visible 的启发式一致）
        ds["owner_match"] = (owner == current_user) if owner else ds["is_local"]
        ds["is_owner"] = ds["owner_match"]
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
    ctx: AuthContext = Depends(get_auth_context),
):
    """列出已注册数据集（**按归属隔离**）。

    数据集是「谁的资源」而不是共享资源。注册表文件随项目目录走，
    从他人环境复制项目时会连同对方的注册记录一起带过来，这些记录
    的 data_path 指向对方机器，本机并没有数据。

    默认只返回属于当前用户的数据集：
    - 带 owner 且 == 当前用户 → 返回
    - 早期无 owner 的历史条目 → 本机确实有数据的视为本人的（平滑迁移）

    参数：
    - include_others=true ：返回全部（含他人条目），仅平台管理员可用
    - available_only=true ：只返回本机确实有数据的
    """
    current_user = resolve_owner_id(ctx)
    if include_others and not is_platform_admin(ctx):
        raise HTTPException(403, "只有平台管理员可以查看他人的数据集")
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
async def claim_local_datasets(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
):
    """把本机有数据、但缺少归属标记的历史数据集认领到当前用户。

    用于一次性迁移：这些条目确认是自己的数据后打上 owner，
    之后列表就走精确的归属匹配，不再依赖"本机是否有数据"的启发式判断。

    ⚠️ 仅平台管理员可调用：服务器从单机升级时无主条目是共享资产，
    "先到先得"会让第一个调用者把所有人的历史数据认领走。
    """
    if not is_platform_admin(ctx):
        raise HTTPException(403, "只有平台管理员可以认领历史数据集")
    owner = resolve_owner_id(ctx)
    claimed = registry.claim_local_orphans(user_id=owner)

    import uuid as _uuid
    from core.auth.audit import add_audit_event
    from core.db.models import RESULT_SUCCESS

    add_audit_event(
        session,
        action="data.claim_local",
        result=RESULT_SUCCESS,
        request_id=ctx.request_id,
        actor_id=ctx.user_id,
        tenant_id=_uuid.UUID(ctx.tenant_id),
        target_type="user_dataset_claim",
        target_id=owner,
    )
    await session.commit()
    return {"claimed": claimed, "owner": owner}


@router.get("/{dataset_id}/thumbnail")
async def dataset_thumbnail(
    dataset_id: str, ctx: AuthContext = Depends(get_auth_context)
):
    """数据集列表缩略图（第一张图片）。仅本人可见，且路径须在允许根内。"""
    from fastapi.responses import FileResponse

    entry = await _require_owner_dataset_async(ctx, dataset_id)
    data_path = entry.get("data_path", "")
    img = _find_first_image(dataset_id, data_path)
    if not img:
        raise HTTPException(404, "缩略图不存在")
    try:
        # 必须带 owner：否则可借缩略图接口读他人上传目录里的图片
        safe = resolve_checked_path(
            img, must_exist=True, owner_id=resolve_owner_id(ctx)
        )
    except PathAccessError:
        raise HTTPException(404, "缩略图不存在")
    return FileResponse(str(safe), media_type="image/png")


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str, ctx: AuthContext = Depends(get_auth_context)
):
    """数据集详情（含 MD；**仅本人**，他人条目按 404 处理）"""
    entry = await _require_owner_dataset_async(ctx, dataset_id)

    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    spec_md = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    return {**entry, "spec_md": spec_md}


@router.get("/{dataset_id}/files")
async def list_files(
    dataset_id: str, ctx: AuthContext = Depends(get_auth_context)
):
    """数据集文件列表（仅本人）"""
    entry = await _require_owner_dataset_async(ctx, dataset_id)

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
async def match_tools(
    req: MatchToolsRequest, ctx: AuthContext = Depends(get_auth_context)
):
    """根据数据集和用户需求，匹配可用工具（数据集须属于本人）"""
    entry = (
        await _require_owner_dataset_async(ctx, req.dataset_id)
        if req.dataset_id
        else None
    )

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


@router.get("/{dataset_id}/preview")
async def preview_dataset(
    dataset_id: str, ctx: AuthContext = Depends(get_auth_context)
):
    """预览数据集（仅本人）— 从 MD 文档解析文件列表 + 匹配预览工具"""
    entry = await _require_owner_dataset_async(ctx, dataset_id)

    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    spec_md = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""

    # 从 MD 文档的"数据格式"表格中解析文件列表
    files = _parse_files_from_spec(spec_md)

    # 如果 MD 中没有文件信息，扫描实际目录
    if not files:
        data_path = registry.resolve_data_path(entry.get("data_path", ""))
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
async def search_datasets(
    q: str = "", tags: str = "", ctx: AuthContext = Depends(get_auth_context)
):
    """搜索数据集（**按归属过滤**，不返回他人条目）"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    results = await discoverer.search(query=q, tags=tag_list)
    me = resolve_owner_id(ctx)
    filtered = [
        d
        for d in results
        if (str(d.get("owner") or "") == me)
        or (not d.get("owner") and registry.is_present_locally(d))
    ]
    return {"datasets": filtered}


@router.delete("/{dataset_id}")
async def delete_dataset(
    dataset_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
):
    """删除数据集（registry + data files；**仅本人**）。

    删除文件前经 :func:`core.file_guard.validate_dataset_deletion` 二次校验：
    资源根本身 / 他人上传目录 / 他人数据集重叠一律 **409**，绝不 rmtree。
    """
    entry = await _require_owner_dataset_async(ctx, dataset_id)

    import shutil
    # 删除数据文件
    # 必须解析成绝对路径：相对路径若直接 rmtree，会误删 resources/data 下的目录
    data_path = registry.resolve_data_path(entry.get("data_path", ""))
    if data_path.exists() and data_path.is_dir():
        # v3 评审：删除前必须再做一次"删除边界"校验 —— registry 里的 data_path
        # 不可信（历史条目 / 被篡改的注册表），只允许删除安全根内的、本人拥有的
        # 独立数据集目录；资源根本身、他人上传目录、他人数据集一律 409。
        try:
            safe_path = validate_dataset_deletion(
                data_path, owner_id=resolve_owner_id(ctx)
            )
        except PathAccessError as exc:
            raise HTTPException(409, f"拒绝删除：{exc}") from exc
        shutil.rmtree(safe_path, ignore_errors=True)
    # 删除 MD 文档
    spec_path = registry._get_def_dir() / f"{dataset_id}.md"
    if spec_path.exists():
        spec_path.unlink()
    # 从 registry 移除
    await registry.unregister(dataset_id)

    # 删除后撤回其发布，避免 catalog/install 悬空指向已不存在的资源
    try:
        from core.sharing.service import revoke_publications_for_release

        await revoke_publications_for_release(
            session,
            "data",
            dataset_id,
            actor_id=str(ctx.user_id),
            tenant_id=getattr(ctx, "tenant_id", None),
            request_id=getattr(ctx, "request_id", "") or "",
        )
    except Exception:  # noqa: BLE001 - 撤发布失败不应回滚已完成的删除
        pass

    from core.auth.audit_actions import ACTION_DATA_DELETED, record_resource_change

    await record_resource_change(
        session, ctx,
        action=ACTION_DATA_DELETED, target_type="data", target_id=dataset_id,
    )
    return {"deleted": dataset_id}

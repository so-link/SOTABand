"""平台工具处理器 — 全部转调现有 registry / 业务函数，不复制业务逻辑。

P0 实现 L0 只读工具（直通执行）。P1/P2/P3 在同一注册表里按批放开写工具，
网关逻辑不因能力增加而改动。

约定：
- 处理器是 `async def handler(args: 已校验模型, ctx: CallContext) -> dict`。
- 返回值必须是可 JSON 序列化的 dict，会同时进入：① 审计表 ② 回填给 LLM 的工具结果。
  因此**只放模型需要的信息**，大文本一律 clip。
- 列表类结果统一用 `{count, items}`，便于模型直接汇总回答。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from core.chat_control.guards import (
    LIST_MAX_ITEMS,
    SPEC_MAX_CHARS,
    clip,
    resolve_allowed_path,
)
from core.chat_control.registry import (
    RISK_READ,
    CallContext,
    StrictArgs,
    platform_tool,
)

# 各注册表单例（与 REST 路由用的是同一份文件存储，读到的内容一致）
from core.resource.registry.agent_registry import AgentRegistry
from core.resource.registry.data_registry import DataRegistry
from core.resource.registry.model_registry import ModelRegistry
from core.resource.registry.tool_registry import ToolRegistry

_data = DataRegistry()
_tools = ToolRegistry()
_models = ModelRegistry()
_agents = AgentRegistry()


# ────────────────────────── 资源清单（L0） ──────────────────────────

class EmptyArgs(StrictArgs):
    """无参数工具。保留显式模型以便 schema 里有描述可写。"""


async def _brief_datasets(ctx: CallContext) -> dict:
    items = await _data.list_all(owner=ctx.owner)
    return {
        "count": len(items),
        "items": [
            {"id": e.get("id"), "name": e.get("name"), "type": e.get("type"),
             "status": e.get("status"), "tags": e.get("tags") or []}
            for e in items[:LIST_MAX_ITEMS]
        ],
        "truncated": len(items) > LIST_MAX_ITEMS,
    }


@platform_tool(
    "list_datasets",
    "列出当前用户可见的数据集（id、名称、类型、状态、标签）。"
    "用户询问自己有哪些数据集时使用。",
    EmptyArgs, RISK_READ,
)
async def list_datasets(args: EmptyArgs, ctx: CallContext) -> dict:
    return await _brief_datasets(ctx)


@platform_tool(
    "list_tools",
    "列出平台已注册的工具（id、名称、状态、描述摘要）。",
    EmptyArgs, RISK_READ,
)
async def list_tools(args: EmptyArgs, ctx: CallContext) -> dict:
    items = await _tools.list_all(user_id=ctx.owner)
    out = []
    for e in items[:LIST_MAX_ITEMS]:
        out.append({
            "id": e.get("id"), "name": e.get("name"), "status": e.get("status"),
            "description": clip(e.get("description") or "", 120),
        })
    return {"count": len(items), "items": out, "truncated": len(items) > LIST_MAX_ITEMS}


@platform_tool(
    "list_models",
    "列出已注册的模型（id、名称、框架、版本）。",
    EmptyArgs, RISK_READ,
)
async def list_models(args: EmptyArgs, ctx: CallContext) -> dict:
    items = await _models.list_all()
    out = [{
        "id": e.get("id"), "name": e.get("name"),
        "framework": e.get("framework"), "version": e.get("version"),
    } for e in items[:LIST_MAX_ITEMS]]
    return {"count": len(items), "items": out, "truncated": len(items) > LIST_MAX_ITEMS}


@platform_tool(
    "list_agents",
    "列出平台上的智能体（id、名称、运行状态）。",
    EmptyArgs, RISK_READ,
)
async def list_agents(args: EmptyArgs, ctx: CallContext) -> dict:
    from core.agent.factory import agent_factory
    items = await _agents.list_all(user_id=ctx.owner)
    out = []
    for e in items[:LIST_MAX_ITEMS]:
        out.append({
            "id": e.get("id"), "name": e.get("name"),
            "description": clip(e.get("description") or "", 120),
            "running": agent_factory.is_running(e.get("id") or ""),
        })
    return {"count": len(items), "items": out, "truncated": len(items) > LIST_MAX_ITEMS}


# ────────────────────────── 资源详情（L0） ──────────────────────────

class IdArgs(StrictArgs):
    resource_id: str = Field(description="资源 id，如数据集/工具/智能体的 id")


@platform_tool(
    "get_dataset_info",
    "查看数据集详情：注册信息 + 规格文档（spec_md，已截断）+ 文件清单。",
    IdArgs, RISK_READ,
)
async def get_dataset_info(args: IdArgs, ctx: CallContext) -> dict:
    entry = await _data.get(args.resource_id)
    if not entry:
        return {"error": f"数据集不存在: {args.resource_id}"}
    if not _data.is_visible(entry, ctx.owner):
        return {"error": f"无权访问数据集: {args.resource_id}"}
    spec_path = _data._get_def_dir() / f"{args.resource_id}.md"
    spec_md = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    return {"dataset": entry, "spec_md": clip(spec_md, SPEC_MAX_CHARS)}


@platform_tool(
    "get_tool_spec",
    "查看工具详情：注册信息 + 规格文档（spec_md，已截断）。",
    IdArgs, RISK_READ,
)
async def get_tool_spec(args: IdArgs, ctx: CallContext) -> dict:
    entry = await _tools.get(args.resource_id)
    if not entry:
        return {"error": f"工具不存在: {args.resource_id}"}
    out = {k: v for k, v in entry.items() if k not in ("code", "code_path")}
    spec_md = entry.get("spec_md") or ""
    if not spec_md:
        spec_path = Path(entry.get("spec_path") or "")
        if spec_path.exists():
            spec_md = spec_path.read_text(encoding="utf-8")
    out["spec_md"] = clip(spec_md, SPEC_MAX_CHARS)
    return out


@platform_tool(
    "get_agent_spec",
    "查看智能体详情：注册信息 + 规格文档（spec_md，已截断）+ 是否运行中。",
    IdArgs, RISK_READ,
)
async def get_agent_spec(args: IdArgs, ctx: CallContext) -> dict:
    entry = await _agents.get(args.resource_id)
    if not entry:
        return {"error": f"智能体不存在: {args.resource_id}"}
    from core.agent.factory import agent_factory
    out = {k: v for k, v in entry.items() if k != "code"}
    spec_md = entry.get("spec_md") or ""
    if not spec_md:
        spec_path = Path(entry.get("spec_path") or "")
        if spec_path.exists():
            spec_md = spec_path.read_text(encoding="utf-8")
    out["spec_md"] = clip(spec_md, SPEC_MAX_CHARS)
    out["running"] = agent_factory.is_running(args.resource_id)
    return out


# ────────────────────────── 数据集预览（L0） ──────────────────────────

@platform_tool(
    "preview_dataset",
    "预览数据集：返回文件清单与规格摘要（不返回原始数据行，避免上下文膨胀）。",
    IdArgs, RISK_READ,
)
async def preview_dataset(args: IdArgs, ctx: CallContext) -> dict:
    from app.api.routes import data_routes
    try:
        result = await data_routes.preview_dataset(args.resource_id)
    except Exception as e:  # HTTPException 的 detail 对模型更有用
        return {"error": f"预览失败: {getattr(e, 'detail', e)}"}
    entry = result.get("dataset") or {}
    if entry and not _data.is_visible(entry, ctx.owner):
        return {"error": f"无权访问数据集: {args.resource_id}"}
    files = result.get("files") or []
    return {
        "dataset_id": args.resource_id,
        "data_path": entry.get("data_path", ""),
        "file_count": len(files),
        "files": [
            {"name": f.get("name"), "format": f.get("format"), "size": f.get("size")}
            for f in files[:LIST_MAX_ITEMS]
        ],
        "truncated": len(files) > LIST_MAX_ITEMS,
        "spec_md": clip(result.get("spec_md") or "", SPEC_MAX_CHARS),
    }


# ────────────────────────── 目录浏览（L0） ──────────────────────────

class ListDirectoryArgs(StrictArgs):
    path: str = Field(
        description="要浏览的目录。必须位于允许范围内（私有资源区或项目工作区）。"
    )
    limit: int = Field(default=60, ge=1, le=200, description="最多返回多少个条目")


def _dir_view(args: ListDirectoryArgs) -> dict:
    root = resolve_allowed_path(args.path)
    if not root.is_dir():
        return {"error": f"不是目录: {args.path}"}
    entries = []
    for child in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if len(entries) >= args.limit:
            break
        entries.append({
            "name": child.name,
            "is_dir": child.is_dir(),
            "size": child.stat().st_size if child.is_file() else None,
        })
    return {
        "path": str(root),
        "entry_count": len(entries),
        "entries": entries,
        "has_more": len(list(root.iterdir())) > args.limit,
    }


@platform_tool(
    "list_directory",
    "浏览目录条目（仅限允许根目录内），用于确认某个路径下有哪些文件再注册数据集。",
    ListDirectoryArgs, RISK_READ,
    summary_fn=lambda a: f"浏览目录 {a.get('path', '')}",
)
async def list_directory(args: ListDirectoryArgs, ctx: CallContext) -> dict:
    try:
        return _dir_view(args)
    except Exception as e:
        return {"error": str(e)}


# ────────────────────────── 定时任务状态（L0） ──────────────────────────

class CronJobArgs(StrictArgs):
    job_id: str = Field(description="定时任务 id")


class LimitArgs(StrictArgs):
    limit: int = Field(default=20, ge=1, le=100, description="返回条数")


@platform_tool(
    "list_cron_jobs",
    "列出定时任务（含下次执行时间、上次状态、启用状态）。",
    EmptyArgs, RISK_READ,
)
async def list_cron_jobs(args: EmptyArgs, ctx: CallContext) -> dict:
    from core.cron import store as cron_store
    jobs = cron_store.list_jobs(owner=ctx.owner)
    return {
        "count": len(jobs),
        "items": [{
            "id": j.get("id"), "name": j.get("name"), "kind": j.get("kind"),
            "target_id": j.get("target_id"), "cron": j.get("cron"),
            "enabled": j.get("enabled"), "last_status": j.get("last_status"),
            "next_run_at": j.get("next_run_at"),
        } for j in jobs[:LIST_MAX_ITEMS]],
    }


@platform_tool(
    "list_cron_runs",
    "查看某个定时任务的执行历史（状态、耗时、错误）。",
    CronJobArgs, RISK_READ,
)
async def list_cron_runs(args: CronJobArgs, ctx: CallContext) -> dict:
    from core.cron import store as cron_store
    job = cron_store.get(args.job_id)
    if not job:
        return {"error": f"定时任务不存在: {args.job_id}"}
    if job.get("owner") and job["owner"] != ctx.owner:
        return {"error": f"无权查看该定时任务: {args.job_id}"}
    runs = cron_store.runs(args.job_id, limit=20)
    return {
        "job": {"id": job["id"], "name": job["name"], "cron": job["cron"]},
        "runs": [{
            "id": r.get("id"), "status": r.get("status"),
            "started_at": r.get("started_at"), "duration_ms": r.get("duration_ms"),
            "message": clip(r.get("message") or "", 200),
            "error": clip(r.get("error") or "", 200),
        } for r in runs],
    }

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

import os
import re
import time
from pathlib import Path

from pydantic import Field

from core.chat_control.guards import (
    LIST_MAX_ITEMS,
    SPEC_MAX_CHARS,
    PathNotAllowed,
    clip,
    resolve_allowed_path,
)
from core.chat_control.registry import (
    RISK_READ,
    RISK_WRITE,
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


# ────────────────────────── 数据集 / 模型注册（P1，写） ──────────────────────────
#
# 写工具在网关侧只生成待确认操作；处理器在用户确认后才被调用，
# 因此路径白名单等语义校验在执行时才生效——越界错误会回给 LLM 换路径重试。

# 目录扫描上限：防止把超大目录的统计拖垮确认流程
SCAN_MAX_FILES = 5000


class RegisterDatasetArgs(StrictArgs):
    path: str = Field(
        description="要注册的数据集目录。必须位于允许范围内（私有资源区或项目工作区）。"
    )
    name: str = Field(description="数据集名称")
    description: str = Field(default="", description="数据集用途描述（写入规格文档）")
    tags: list[str] = Field(default_factory=list, description="分类标签（可选）")


def _slug(text: str) -> str:
    """数据集 id 前缀：非 ASCII 字母数字一律折叠，空则兜底。"""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-").lower()
    return s or "dataset"


def _scan_dataset_dir(root: Path) -> dict:
    """递归统计目录文件（跳过隐藏文件），超过 SCAN_MAX_FILES 提前截止。"""
    file_count = 0
    total_size = 0
    formats: set[str] = set()
    samples: list[tuple[str, int]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            fp = Path(dirpath) / fn
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            file_count += 1
            total_size += size
            suffix = fp.suffix.lstrip(".").lower()
            if suffix:
                formats.add(suffix)
            if len(samples) < 10:
                samples.append((str(fp.relative_to(root)), size))
            if file_count >= SCAN_MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    return {"file_count": file_count, "total_size": total_size,
            "formats": sorted(formats), "samples": samples, "truncated": truncated}


def _dataset_spec_md(name: str, description: str, root: Path, scan: dict) -> str:
    """对话注册的规格文档：确定性生成，不额外消耗 LLM（可稍后在编辑器里完善）。"""
    sample_lines = "\n".join(f"- `{rel}`（{size} bytes）" for rel, size in scan["samples"])
    return (
        f"# {name}\n\n{description or '（通过对话注册的数据集）'}\n\n"
        f"---\n## 数据集目录\n"
        f"- **路径**: `{root}`\n"
        f"- **文件数**: {scan['file_count']}\n"
        f"- **总大小**: {scan['total_size']} bytes\n"
        f"- **格式**: {', '.join(scan['formats']) or '未知'}\n\n"
        f"## 文件样例（前 {len(scan['samples'])} 个）\n{sample_lines}\n"
    )


@platform_tool(
    "register_dataset",
    "把一个目录注册为平台数据集（文件保持原位，不复制）。"
    "注册成功后数据集会出现在数据空间。注册前可先用 list_directory 确认路径内容。",
    RegisterDatasetArgs, RISK_WRITE,
    summary_fn=lambda a: f"注册数据集「{a.get('name', '')}」（{a.get('path', '')}）",
)
async def register_dataset(args: RegisterDatasetArgs, ctx: CallContext) -> dict:
    from core.api.implementations.api_data import ApiDataRegister
    try:
        root = resolve_allowed_path(args.path)
    except PathNotAllowed as e:
        return {"error": str(e)}
    if not root.is_dir():
        return {"error": f"不是目录: {root}。请提供数据集所在目录。"}
    name = args.name.strip()
    if not name:
        return {"error": "数据集名称不能为空"}

    scan = _scan_dataset_dir(root)
    if scan["file_count"] == 0:
        return {"error": f"目录为空（无可用文件）: {root}"}

    spec_md = _dataset_spec_md(name, args.description.strip(), root, scan)
    result = await ApiDataRegister.call(
        id=f"{_slug(name)}_{int(time.time())}",
        name=name, raw_md=spec_md, data_path=str(root),
        file_count=scan["file_count"], total_size=scan["total_size"],
        formats=scan["formats"], tags=args.tags,
    )
    # 与 REST 注册路径一致：后台匹配预览工具（失败不影响注册结果）
    try:
        import asyncio
        from app.api.routes.data_routes import _match_preview_tool
        asyncio.create_task(_match_preview_tool(result["dataset_id"], spec_md))
    except Exception:
        pass
    return {
        "dataset_id": result["dataset_id"], "name": name,
        "data_path": str(root), "file_count": scan["file_count"],
        "total_size": scan["total_size"], "formats": scan["formats"],
        "scan_truncated": scan["truncated"],
    }


class RegisterModelArgs(StrictArgs):
    name: str = Field(description="模型名称（注册时会自动追加时间戳后缀防冲突）")
    description: str = Field(default="", description="模型用途/结构描述（写入规格文档）")
    model_path: str = Field(
        default="", description="模型权重文件或目录路径（可选；必须在允许范围内）"
    )
    framework: str = Field(default="", description="框架，如 PyTorch / ONNX / sklearn（可选）")
    version: str = Field(default="0.1.0", description="版本号")
    tags: list[str] = Field(default_factory=list, description="分类标签（留空则由系统自动生成）")


@platform_tool(
    "register_model",
    "注册一个模型到模型空间。模型权重文件保持在原位置，不复制。",
    RegisterModelArgs, RISK_WRITE,
    summary_fn=lambda a: f"注册模型「{a.get('name', '')}」",
)
async def register_model(args: RegisterModelArgs, ctx: CallContext) -> dict:
    from core.api.implementations.api_model import ApiModelRegister
    model_path = ""
    if args.model_path.strip():
        try:
            model_path = str(resolve_allowed_path(args.model_path))
        except PathNotAllowed as e:
            return {"error": str(e)}
    name = args.name.strip()
    if not name:
        return {"error": "模型名称不能为空"}

    lines = [f"# {name}", "", args.description.strip() or "（通过对话注册的模型）", ""]
    if args.framework.strip():
        lines.append(f"- **框架**: {args.framework.strip()}")
    if model_path:
        lines.append(f"- **模型路径**: `{model_path}`")
    lines.append(f"- **版本**: {args.version.strip() or '0.1.0'}")
    result = await ApiModelRegister.call(
        name=name, raw_md="\n".join(lines) + "\n",
        framework=args.framework.strip(), model_path=model_path,
        version=args.version.strip() or "0.1.0", tags=args.tags,
    )
    return {
        "model_id": result.get("model_id"), "name": result.get("name"),
        "framework": args.framework.strip(), "model_path": model_path,
        "tags": result.get("tags") or [],
    }


# ────────────────────────── 工具生成链（P2，写） ──────────────────────────
#
# create_tool 完整走现有生成链路：generate-spec → generate-code → scan_code
# 静态安全审查 → registry.register，与 /api/tool/register 同标准，不因
# "对话触发"降低任何审查。SPEC_PROMPT / builder / registry 全部复用
# tool_routes 的模块级单例，零复制。

class CreateToolArgs(StrictArgs):
    description: str = Field(description="工具需求描述（要做什么、输入输出是什么）")
    tool_name: str = Field(default="", description="工具名称（留空则由系统根据描述生成）")
    reference_code: str = Field(default="", description="参考实现代码（可选）")


def _clip_view(s: str, n: int = 600) -> str:
    return s if len(s) <= n else s[:n] + f"…（共 {len(s)} 字符，已截断展示）"


async def _collect_stream(agen) -> str:
    """把 (kind, token) 流式事件累积为完整文本。

    生成链必须走流式：非流式 chat() 配 max_tokens=100000 时，长响应会
    把单次 read timeout 全部耗光导致请求永久挂起（P2 冒烟实测）。
    流式持续有数据返回，timeout 只对"相邻数据间隔"生效，稳定可靠。
    """
    parts = []
    async for kind, token in agen:
        if kind == "content":
            parts.append(token)
    return "".join(parts)


@platform_tool(
    "create_tool",
    "按需求描述创建一个新数据处理工具：自动生成规格与代码、经静态安全审查后注册到工具空间。"
    "耗时较长（约 1 分钟），一次对话建议只创建一个。",
    CreateToolArgs, RISK_WRITE,
    summary_fn=lambda a: f"创建工具：{_clip_view(a.get('description', ''), 80)}",
    args_view_fn=lambda a: {
        "description": _clip_view(a.get("description", "")),
        "tool_name": a.get("tool_name", ""),
        "reference_code": _clip_view(a.get("reference_code", "")) if a.get("reference_code") else "",
    },
)
async def create_tool(args: CreateToolArgs, ctx: CallContext) -> dict:
    from datetime import datetime

    from app.api.routes import tool_routes
    from core.resource import paths as res_paths
    from core.security.code_scan import scan_code

    builder = tool_routes.builder
    tool_reg = tool_routes.registry
    desc = args.description.strip()
    if not desc:
        return {"error": "工具需求描述不能为空"}

    await ctx.progress({"type": "tool-gen", "step": "spec", "message": "正在生成工具规格…"})
    user_content = desc
    if args.reference_code.strip():
        user_content += (
            "\n\n---\n以下为用户提供的参考代码，请完整复制到生成的 MD 文档中"
            "「5. 运行机制」部分（用 ```python 代码块包裹）：\n\n"
            f"```python\n{args.reference_code.strip()}\n```\n"
        )
    spec_md = await _collect_stream(tool_routes.llm.chat_stream_events(
        messages=[
            {"role": "system", "content": tool_routes.SPEC_PROMPT.replace(
                "{日期}", datetime.now().strftime("%Y-%m-%d"))},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3, max_tokens=100000,
    ))
    spec_md = spec_md.strip()
    if not spec_md:
        return {"error": "规格生成失败（LLM 返回为空），请稍后重试"}

    name = args.tool_name.strip() or desc[:24].strip() or "对话创建的工具"
    tool_id = f"{_slug(name)}_{int(time.time())}"
    if not res_paths.is_valid_resource_id(tool_id):
        tool_id = f"tool-{int(time.time())}"

    await ctx.progress({"type": "tool-gen", "step": "code", "message": "正在生成工具代码…"})
    try:
        raw_code = await _collect_stream(builder.build_stream(
            {"raw_md": spec_md, "id": tool_id, "name": name}))
        code = builder._clean_code(raw_code)
    except Exception as e:
        return {"error": f"代码生成失败: {type(e).__name__}: {e}"}
    if not code.strip():
        return {"error": "代码生成失败（LLM 返回为空），请稍后重试"}

    # 与 /api/tool/register 完全相同的审查闸门
    scan = scan_code(code)
    if not scan.ok:
        return {"error": "静态安全审查未通过，工具未注册：\n" + "\n".join(scan.errors)}

    await ctx.progress({"type": "tool-gen", "step": "register", "message": "正在注册工具…"})
    param_meta = []
    try:
        param_meta = await builder.extract_param_metadata(spec_md)
    except Exception:
        pass
    if not param_meta:
        param_meta = builder._parse_spec_inputs(spec_md)
    resource = {
        "id": tool_id, "name": name, "version": "0.1.0",
        "raw_md": spec_md, "code": code, "tags": [],
        "param_meta": param_meta, "modes": builder.parse_modes(spec_md),
    }
    registered_id = await tool_reg.register(resource)
    return {"tool_id": registered_id, "name": name, "scan_passed": True}


class ModifyToolArgs(StrictArgs):
    tool_id: str = Field(description="要修改的工具 id（先用 list_tools 确认）")
    request: str = Field(description="修改需求（对现有代码要做什么改动，描述清楚）")


@platform_tool(
    "modify_tool",
    "按修改需求改写已注册工具的代码：LLM 修改 → 静态安全审查 → 写回实现文件。"
    "修改前会向用户展示确认卡片。",
    ModifyToolArgs, RISK_WRITE,
    summary_fn=lambda a: f"修改工具 {a.get('tool_id', '')}：{_clip_view(a.get('request', ''), 80)}",
)
async def modify_tool(args: ModifyToolArgs, ctx: CallContext) -> dict:
    from app.api.routes import tool_routes
    from core.security.code_scan import scan_code

    builder = tool_routes.builder
    tool_reg = tool_routes.registry

    entry = await tool_reg.get(args.tool_id)
    if not entry:
        return {"error": f"工具不存在: {args.tool_id}"}
    request = args.request.strip()
    if not request:
        return {"error": "修改描述不能为空"}
    current_code = tool_reg.read_code_text(args.tool_id)
    if not current_code.strip():
        return {"error": f"读不到工具现有代码: {args.tool_id}"}

    prompt = f"""Modify the following Python tool code according to the user's request.
IMPORTANT: Keep execute(**kwargs)->dict signature and return format. Return ONLY the complete modified code.

Current code:
```python
{current_code}
```

User's request:
{request}

Modified code:"""
    full = await _collect_stream(tool_routes.llm.chat_stream_events(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=100000,
    ))
    modified = builder._clean_code(full)
    if not modified.strip():
        return {"error": "代码修改失败（LLM 返回为空），请稍后重试"}

    # 与 /api/tool/{id}/save-code 相同的审查闸门
    scan = scan_code(modified)
    if not scan.ok:
        return {"error": "静态安全审查未通过，修改未写入：\n" + "\n".join(scan.errors)}

    impl_dir = tool_reg._get_impl_dir() / args.tool_id
    impl_dir.mkdir(parents=True, exist_ok=True)
    (impl_dir / "tool.py").write_text(modified, encoding="utf-8")
    return {
        "tool_id": args.tool_id, "saved": True,
        "code_summary": f"新代码共 {len(modified.splitlines())} 行",
    }


class RunToolArgs(StrictArgs):
    tool_id: str = Field(description="要执行的工具 id（只能跑已注册且 active 的工具）")
    params: dict = Field(default_factory=dict, description="工具参数（键按工具规格文档）")


@platform_tool(
    "run_tool",
    "执行一个已注册的工具（子进程沙箱内运行）。参数会在确认卡片中全量展示。",
    RunToolArgs, RISK_WRITE,
    summary_fn=lambda a: f"执行工具 {a.get('tool_id', '')}",
)
async def run_tool(args: RunToolArgs, ctx: CallContext) -> dict:
    from core.executor.tool_executor import ToolExecutor
    from core.resource.registry.tool_registry import ToolRegistry

    entry = await ToolRegistry().get(args.tool_id)
    if not entry:
        return {"error": f"工具不存在: {args.tool_id}"}
    if entry.get("status") != "active":
        return {"error": f"工具 {args.tool_id} 状态为 {entry.get('status')}，不可执行"}
    result = await ToolExecutor.execute(tool_id=args.tool_id, params=args.params)
    # ToolExecutor 统一返回 {status, message, data}；失败转成 error 让 LLM 能自我修正
    if result.get("status") != "success":
        return {"error": f"执行失败: {clip(str(result.get('message', '')), 500)}",
                "detail": result.get("data")}
    return result

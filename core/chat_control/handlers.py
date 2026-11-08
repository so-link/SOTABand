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
    RISK_DANGER,
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
    """数据集 id 前缀：非 ASCII 字母数字一律折叠，空则兜底。

    中文名（如「ETT-h1 电力负荷小时数据集」）经折叠后会只剩 etth1 这样的
    尾部片段，再加时间戳后缀就成了 etth1_1794053389 —— 用户和自己给的
    dataset_id 对不上。中文名改为取目录名（更短、更稳定、更像用户心里的
    那个 id）。
    """
    s = (text or "").strip()
    if re.search(r"[\u4e00-\u9fff]", s):
        return "dataset"
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-").lower()
    return s or "dataset"


def _spec_name(spec_md: str) -> str:
    """取 MD 规范 frontmatter 的 name（LLM 生成的中文工具名），没有则空串。"""
    for line in (spec_md or "").split("\n"):
        s = line.strip()
        if s.startswith("name:") and ":" in s:
            return s.split(":", 1)[1].strip().strip("'\"")
    return ""


def _spec_id_slug(spec_md: str) -> str:
    """取 MD 规范 frontmatter 的 id（LLM 生成的英文短 id），没有则空串。

    中文展示名没法直接做 id，规格文档里的 id 字段就是可读的英文标识
    （hourly-ot-stats）；拿不到时调用方退回通用前缀。
    """
    for line in (spec_md or "").split("\n"):
        s = line.strip()
        if s.startswith("id:") and ":" in s:
            v = s.split(":", 1)[1].strip().strip("'\"")
            v = re.sub(r"[^A-Za-z0-9_-]+", "-", v).strip("-").lower()
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,40}", v or ""):
                return v
    return ""


def _is_ascii_slug(s: str) -> bool:
    """判断名称是否只是英文 id（含空格/连字符/下划线），不是给人看的名字。"""
    s = (s or "").strip()
    return bool(s) and all(ord(ch) < 128 for ch in s.replace(" ", "").replace("-", "").replace("_", ""))


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
    # id 前缀：中文名取目录名（etth1 / etf_90d 这类，和用户嘴上说的 id 一致）；
    # 纯英文名沿用名称折叠。
    id_prefix = _slug(name)
    if id_prefix == "dataset":
        id_prefix = _slug(root.name)
    result = await ApiDataRegister.call(
        id=f"{id_prefix}_{int(time.time())}",
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
    tool_name: str = Field(
        default="",
        description="工具的中文展示名（如「ETF 近90日涨跌幅排行」）。不要填英文 id，"
                    "留空则由系统按规格文档自动取名")
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
    "用户要求「做一个 / 新建 / 生成某个工具」时使用本工具，不要在回复里让用户自己去找编辑器。"
    "description 要写清楚工具做什么、输入参数（名称/类型/是否必填/默认值）与输出形态（table 需要 columns+rows）。"
    "耗时较长（约 1 分钟），一次对话建议只创建一个；创建后可用 run_tool 执行验证。",
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

    # 工具展示名：优先用规格文档 frontmatter 的 name（LLM 生成的中文名），
    # 其次调用方给的 tool_name。调用方给的往往直接抄了需求里的英文 id
    # （tool_name=etf-90d-change-ranking），注册后资源空间/确认卡里都显示
    # 成一串 id，用户认不出。纯 ASCII 的 id 不作为展示名。
    spec_name = _spec_name(spec_md)
    given = args.tool_name.strip()
    if _is_ascii_slug(spec_name):
        spec_name = ""
    name = spec_name or (given if not _is_ascii_slug(given) else "") \
        or desc[:24].strip() or "对话创建的工具"
    if _is_ascii_slug(name):
        # 两头都是英文 id：从需求里取第一段中文当展示名（「ETF 近90日涨跌幅排行」）
        m = re.search(r"[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9\s「」《》（）\-]{3,23}",
                      desc)
        if m:
            name = m.group(0).strip()
    # 工具 id 前缀：优先规格文档里的英文 id（hourly-ot-stats），再用展示名
    # 折叠。中文展示名折叠不出来时 _slug 会退回 "dataset"，id 就成了
    # dataset_<ts>，看不出是什么工具。
    tool_id = f"{_spec_id_slug(spec_md) or _slug(name)}_{int(time.time())}"
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
    params: dict = Field(
        default_factory=dict,
        description="工具参数。**调用前必须先 get_tool_spec 查该工具的 param_meta**，"
                    "按其 name/required/default/hints 传值（枚举值要用 hints 里的写法，"
                    "如语言码是 zh_cn 而不是 zh）。不要凭常识猜参数名和取值。")
    # 从「工作空间工具」发起的执行（交互 Agent 的 tool_call 与工具匹配两条
    # 路径都归一到本工具）：确认门由 Agent 侧统一卡片承担，这里直接执行。
    # 仅供 Agent 子进程内部使用，不进入 LLM 可见的 schema。
    confirmed: bool = Field(
        default=False,
        description="内部字段：用户已在本轮确认卡上确认执行，网关跳过二次确认。")


def _tool_label(tool_id: str) -> str:
    """确认卡里展示工具的中文名而不是内部 id（同 _agent_label）。"""
    if not tool_id:
        return ""
    try:
        from core.resource.registry.tool_registry import ToolRegistry
        for e in ToolRegistry()._read():
            if e.get("id") == tool_id:
                return e.get("name") or tool_id
    except Exception:
        pass
    return tool_id


@platform_tool(
    "run_tool",
    "执行一个已注册的工具（子进程沙箱内运行）。参数会在确认卡片中全量展示。",
    RunToolArgs, RISK_WRITE,
    summary_fn=lambda a: f"执行工具「{_tool_label(a.get('tool_id', ''))}」",
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
        # 失败回执附上参数定义，模型无需再调 get_tool_spec 即可一次纠正
        return {"error": f"执行失败: {clip(str(result.get('message', '')), 500)}",
                "param_meta": entry.get("param_meta") or [],
                "detail": result.get("data")}
    return result


# ────────────────────────── Agent 生成与运行（P3） ──────────────────────────

class CreateAgentArgs(StrictArgs):
    description: str = Field(
        description="Agent 需求描述（要做什么；可用【【工具名】】引用已有工具）"
    )
    role: str = Field(default="task", description="Agent 类型：task / interactive / web-service")
    agent_name: str = Field(default="", description="Agent 名称（留空则从规范文档提取）")


@platform_tool(
    "create_agent",
    "按需求描述创建一个新智能体：生成规格 → 生成代码 → 静态安全审查 → 注册。"
    "用户要求「做一个 / 新建 / 生成某个 Agent」时使用本工具，不要在回复里让用户自己去找编辑器。"
    "描述里用【【工具名】】引用已有工具（名称必须与 list_tools 返回的一致），Agent 只能用被引用的工具。"
    "创建后可用 start_agent 启动。耗时较长，一次对话建议只创建一个。",
    CreateAgentArgs, RISK_WRITE,
    summary_fn=lambda a: f"创建 Agent：{_clip_view(a.get('description', ''), 80)}（{a.get('role') or 'task'}）",
)
async def create_agent(args: CreateAgentArgs, ctx: CallContext) -> dict:
    from datetime import date

    from app.api.routes import agent_routes
    from core.resource import paths as res_paths
    from core.security.code_scan import scan_code

    desc = args.description.strip()
    if not desc:
        return {"error": "Agent 需求描述不能为空"}
    role = (args.role or "task").strip().lower()
    if role in ("web_service", "webservice"):
        role = "web-service"
    if role not in ("task", "interactive", "web-service"):
        return {"error": f"未知 role: {role}（可选 task / interactive / web-service）"}

    await ctx.progress({"type": "agent-gen", "step": "spec", "message": "正在生成 Agent 规格…"})
    user_prompt = agent_routes.build_spec_user_prompt(desc)
    guidance = agent_routes._role_spec_guidance(role)
    if guidance:
        user_prompt += f"\n\n---\n## Agent 类型约束（必须严格遵守）\n\n{guidance}"
    spec_md = (await _collect_stream(agent_routes.llm.chat_stream_events(
        messages=[
            {"role": "system", "content": agent_routes.SPEC_GENERATION_PROMPT.replace(
                "{今天日期}", date.today().isoformat())},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.5, max_tokens=100000,
    ))).strip()
    if not spec_md:
        return {"error": "规格生成失败（LLM 返回为空），请稍后重试"}

    agent_id = ""
    for line in spec_md.split("\n"):
        if line.startswith("id:") and "---" not in line:
            agent_id = line.split(":", 1)[1].strip()
            break
    if not agent_id or not res_paths.is_valid_resource_id(agent_id):
        agent_id = f"agent-{int(time.time())}"
    name = args.agent_name.strip() or agent_id

    builder = agent_routes.builder
    if not await builder.validate_spec({"raw_md": spec_md}):
        return {"error": "生成的 MD 规范缺少必需段落，请调整描述后重试"}

    await ctx.progress({"type": "agent-gen", "step": "code", "message": "正在生成 Agent 代码…"})
    try:
        code = await builder.build({"raw_md": spec_md, "id": agent_id, "name": name, "role": role})
    except Exception as e:
        return {"error": f"代码生成失败: {type(e).__name__}: {e}"}

    scan = scan_code(code)
    if not scan.ok:
        return {"error": "静态安全审查未通过，Agent 未注册：\n" + "\n".join(scan.errors)}

    # 工作流程步骤事件闸门：task/interactive 对话运行依赖 _step 展示每步过程与
    # 耗时，缺失即视为生成质量不达标，阻断注册（web-service 走 HTTP，不强制）。
    if role != "web-service" and "self._step(" not in code:
        return {"error": "生成的代码缺少工作流程步骤事件（self._step），Agent 未注册；"
                         "请重试，或在编辑器中补全每步的 _step/_log_step 调用后再保存"}

    await ctx.progress({"type": "agent-gen", "step": "register", "message": "正在注册 Agent…"})
    tool_ids: list[str] = []
    try:
        for tr in builder._extract_tool_refs(spec_md):
            if tr.get("id") and tr["id"] not in tool_ids:
                tool_ids.append(tr["id"])
    except Exception:
        pass
    reg = agent_routes.registry
    # 可见性按创建者角色：超管建的供全员使用，普通用户建的仅自己可用
    # （与 REST 注册接口同一规则，避免对话入口绕过）
    from core.chat_control.gateway import _role_of
    from core.user_context import VISIBILITY_PRIVATE, VISIBILITY_PUBLIC

    visibility = (VISIBILITY_PUBLIC if _role_of(ctx.owner) == "admin"
                  else VISIBILITY_PRIVATE)
    registered_id = await reg.register({
        "id": agent_id, "name": name, "version": "0.1.0", "role": role,
        "raw_md": spec_md, "tags": [], "required_tools": tool_ids,
        "visibility": visibility,
    })
    impl_dir = reg._get_impl_dir(visibility) / registered_id
    impl_dir.mkdir(parents=True, exist_ok=True)
    (impl_dir / "agent.py").write_text(code, encoding="utf-8")
    (impl_dir / "spec.md").write_text(spec_md, encoding="utf-8")
    (impl_dir / "config.yaml").write_text(
        f"# {name} 运行时配置\nrole: {role}\nversion: 0.1.0\n", encoding="utf-8",
    )
    agent_routes._maybe_generate_api_doc(registered_id)
    # 启动即自检：只对任务式 Agent 做一轮真实试跑（子进程 + 首个 flow 步骤）。
    # 目的是尽早暴露「必填参数没人传 / 工具名为不可移植默认值」这类结构性缺陷，
    # 失败不回滚注册，但把可读原因回给模型，让它如实告知用户。
    warning = ""
    if role == "task":
        try:
            warning = await _probe_task_agent(registered_id)
        except Exception:
            warning = ""
    out = {"agent_id": registered_id, "name": name, "role": role, "scan_passed": True,
           "self_check": "passed"}
    if warning:
        out["self_check"] = "failed"
        out["warning"] = warning
        out["instruction"] = ("该 Agent 已注册但自检未通过，向用户汇报时必须说明"
                              "「已创建，但首次运行自检未通过」并给出原因，"
                              "禁止说已可用/自检通过。")
    return out


async def _probe_task_agent(agent_id: str) -> str:
    """启动新建任务式 Agent 并试跑一轮，返回可读的告警（通过则空串）。

    试跑带一段占位 content（入口文本本身几乎不会被业务用掉，参数都在 flow
    里固定），目的不是校验输入，而是尽快跑到第一个工具调用，暴露「必填的
    数据集/列名没人传」「数据集 id 用了不可移植默认值」这类结构性缺陷。
    只收集到首个输出事件，超时即判通过（不自检长任务耗时）。
    """
    import asyncio as _asyncio

    from core.agent.factory import agent_factory

    await agent_factory.start(agent_id)
    err = ""
    try:
        async with _asyncio.timeout(180):
            async for ev in agent_factory.execute_stream(
                    agent_id, params={"content": "自检运行"},
                    context={"user_id": "0"}):
                etype = ev.get("event")
                if etype == "error":
                    err = str((ev.get("data") or {}).get("message") or "")
                    break
                if etype in ("content", "delta", "card"):
                    break
                if etype == "done":
                    break
    except Exception:
        err = ""
    if err:
        return f"自检未通过：{err[:200]}"
    return ""


class AgentIdArgs(StrictArgs):
    agent_id: str = Field(description="Agent id（先用 list_agents 确认）")


def _agent_label(agent_id: str) -> str:
    """确认卡里展示 Agent 的中文名而不是内部 id。

    `启动 Agent city-info-query-agent` 这种卡片对用户没有信息量（同名中文
    名在注册表里本来就有）。取不到名字时退回 id，不吞错。
    """
    if not agent_id:
        return ""
    try:
        for e in _agents._read():
            if e.get("id") == agent_id:
                return e.get("name") or agent_id
    except Exception:
        pass
    return agent_id


@platform_tool(
    "start_agent", "启动一个已注册的 Agent（拉起其子进程）。",
    AgentIdArgs, RISK_WRITE,
    summary_fn=lambda a: f"启动 Agent「{_agent_label(a.get('agent_id', ''))}」",
)
async def start_agent(args: AgentIdArgs, ctx: CallContext) -> dict:
    from core.agent.factory import agent_factory
    entry = await _agents.get(args.agent_id)
    if not entry or not _agents.is_visible(entry, ctx.owner):
        return {"error": f"Agent 不存在: {args.agent_id}"}
    r = await agent_factory.start(args.agent_id)
    if r.get("status") not in (None, "success", "started", "running", "already_running"):
        return {"error": f"启动失败: {r.get('error') or r}"}
    return r


@platform_tool(
    "stop_agent", "停止一个正在运行的 Agent 子进程。",
    AgentIdArgs, RISK_WRITE,
    summary_fn=lambda a: f"停止 Agent「{_agent_label(a.get('agent_id', ''))}」",
)
async def stop_agent(args: AgentIdArgs, ctx: CallContext) -> dict:
    from core.agent.factory import agent_factory
    entry = await _agents.get(args.agent_id)
    if not entry or not _agents.is_visible(entry, ctx.owner):
        return {"error": f"Agent 不存在: {args.agent_id}"}
    return await agent_factory.stop(args.agent_id)


# ────────────────────────── 定时任务（P3） ──────────────────────────

class CronJobIdArgs(StrictArgs):
    job_id: str = Field(description="定时任务 id（先用 list_cron_jobs 确认）")


def _cron_ownership(job_id: str, ctx: CallContext) -> dict | str:
    """取任务并校验归属；失败返回错误字符串。"""
    from core.cron import store as cron_store
    job = cron_store.get(job_id)
    if not job:
        return f"定时任务不存在: {job_id}"
    if job.get("owner") and job["owner"] != ctx.owner:
        return f"无权操作该定时任务: {job_id}"
    return job


class CreateCronJobArgs(StrictArgs):
    name: str = Field(description="任务名称")
    kind: str = Field(description="目标类型：tool 或 agent")
    target_id: str = Field(description="要周期执行的工具/Agent id")
    cron: str = Field(description="5 字段 cron 表达式，如 '0 17 * * 1-5'（工作日 17 点）")
    params: dict = Field(default_factory=dict, description="传给目标的参数（可选）")
    timezone: str = Field(default="Asia/Shanghai", description="时区")
    enabled: bool = Field(default=True, description="创建后是否立即启用")
    timeout: int = Field(default=600, ge=5, le=7200, description="单次执行超时秒数")
    misfire: str = Field(default="skip", description="错过触发的策略：skip / fire_once")


@platform_tool(
    "create_cron_job",
    "创建一个定时任务（按 cron 计划周期执行某个工具或 Agent）。cron 表达式与目标都会先校验。",
    CreateCronJobArgs, RISK_WRITE,
    summary_fn=lambda a: f"新建定时任务「{a.get('name', '')}」: {a.get('kind', '')}={a.get('target_id', '')} @ {a.get('cron', '')}",
)
async def create_cron_job(args: CreateCronJobArgs, ctx: CallContext) -> dict:
    from core.cron import runner, store as cron_store
    from core.cron.cron_parse import CronError
    from core.cron.scheduler import cron_scheduler

    if args.kind not in ("tool", "agent"):
        return {"error": f"kind 必须是 tool 或 agent，收到 {args.kind}"}
    payload = args.model_dump()
    try:
        cron, tz, ts = runner.validate_job_payload(payload)
    except CronError as e:
        return {"error": f"cron 校验失败: {e}"}
    ok, msg = await runner.check_target(args.kind, args.target_id, owner=ctx.owner)
    if not ok:
        return {"error": msg}
    job = cron_store.create({
        "name": args.name, "kind": args.kind, "target_id": args.target_id,
        "params": args.params, "cron": cron, "timezone": tz,
        "enabled": args.enabled, "owner": ctx.owner,
        "timeout": args.timeout, "misfire": args.misfire, "next_run_at": ts,
    })
    cron_scheduler.upsert(job["id"])
    return {"job_id": job["id"], "name": job["name"], "cron": job["cron"],
            "enabled": job["enabled"], "next_run_at": job.get("next_run_at")}


class UpdateCronJobArgs(StrictArgs):
    job_id: str = Field(description="要修改的任务 id")
    name: str = Field(default="", description="新名称（不改则留空）")
    kind: str = Field(default="", description="新目标类型（不改则留空）")
    target_id: str = Field(default="", description="新目标 id（不改则留空）")
    cron: str = Field(default="", description="新 cron 表达式（不改则留空）")
    params: dict | None = Field(default=None, description="新参数（不改则留空）")
    enabled: bool | None = Field(default=None, description="启用/禁用（不改则留空）")


@platform_tool(
    "update_cron_job",
    "修改一个定时任务的字段（名称/cron/目标/参数/启用状态；未提供的字段保持原样）。",
    UpdateCronJobArgs, RISK_WRITE,
    summary_fn=lambda a: f"修改定时任务 {a.get('job_id', '')}",
)
async def update_cron_job(args: UpdateCronJobArgs, ctx: CallContext) -> dict:
    from core.cron import runner, store as cron_store
    from core.cron.cron_parse import CronError
    from core.cron.scheduler import cron_scheduler

    job = _cron_ownership(args.job_id, ctx)
    if isinstance(job, str):
        return {"error": job}

    updates = {}
    for key in ("name", "kind", "target_id", "cron"):
        v = getattr(args, key).strip()
        if v:
            updates[key] = v
    if args.params is not None:
        updates["params"] = args.params
    if args.enabled is not None:
        updates["enabled"] = args.enabled
    if not updates:
        return {"error": "没有需要修改的字段"}

    merged = {**job, **updates}
    try:
        cron, tz, ts = runner.validate_job_payload(merged)
    except CronError as e:
        return {"error": f"cron 校验失败: {e}"}
    ok, msg = await runner.check_target(merged["kind"], merged["target_id"],
                                        owner=ctx.owner)
    if not ok:
        return {"error": msg}
    updates.update({"cron": cron, "timezone": tz, "next_run_at": ts})
    updated = cron_store.update(args.job_id, updates)
    cron_scheduler.upsert(args.job_id)
    return {"job_id": args.job_id, "cron": updated["cron"],
            "enabled": updated["enabled"], "next_run_at": updated.get("next_run_at")}


@platform_tool(
    "toggle_cron_job", "启用/禁用一个定时任务（切换开关，不改动其它字段）。",
    CronJobIdArgs, RISK_WRITE,
    summary_fn=lambda a: f"切换定时任务 {a.get('job_id', '')} 的启用状态",
)
async def toggle_cron_job(args: CronJobIdArgs, ctx: CallContext) -> dict:
    from core.cron import runner, store as cron_store
    from core.cron.cron_parse import CronError
    from core.cron.scheduler import cron_scheduler

    job = _cron_ownership(args.job_id, ctx)
    if isinstance(job, str):
        return {"error": job}
    enabled = not job["enabled"]
    updates: dict = {"enabled": enabled}
    if enabled:
        try:
            updates["next_run_at"] = runner.validate_job_payload(
                {"cron": job["cron"], "timezone": job["timezone"]})[2]
        except CronError as e:
            return {"error": f"cron 已失效，无法启用：{e}"}
    updated = cron_store.update(args.job_id, updates)
    cron_scheduler.upsert(args.job_id)
    return {"job_id": args.job_id, "enabled": updated["enabled"],
            "next_run_at": updated.get("next_run_at")}


@platform_tool(
    "trigger_cron_job",
    "立即手动执行一次定时任务（不等 cron 计划，马上产生真实执行副作用）。",
    CronJobIdArgs, RISK_DANGER,
    summary_fn=lambda a: f"立即执行定时任务 {a.get('job_id', '')}",
)
async def trigger_cron_job(args: CronJobIdArgs, ctx: CallContext) -> dict:
    from core.cron.scheduler import cron_scheduler

    job = _cron_ownership(args.job_id, ctx)
    if isinstance(job, str):
        return {"error": job}
    r = cron_scheduler.trigger_now(args.job_id)
    if r.get("status") == "failed":
        return {"error": f"触发失败: {r.get('error') or r}"}
    return r


# ────────────────────────── 资源删除（P3，L2 高危） ──────────────────────────

class DeleteResourceArgs(StrictArgs):
    resource_type: str = Field(description="资源类型：dataset / tool / model / agent / cron_job")
    resource_id: str = Field(description="要删除的资源 id")


@platform_tool(
    "delete_resource",
    "删除一个平台资源（数据集/工具/模型/Agent/定时任务），不可恢复。",
    DeleteResourceArgs, RISK_DANGER,
    summary_fn=lambda a: f"删除 {a.get('resource_type', '')}「{a.get('resource_id', '')}」（不可恢复）",
)
async def delete_resource(args: DeleteResourceArgs, ctx: CallContext) -> dict:
    rtype = args.resource_type.strip().lower()
    rid = args.resource_id.strip()
    if not rid:
        return {"error": "resource_id 不能为空"}

    if rtype == "dataset":
        entry = await _data.get(rid)
        if not entry:
            return {"error": f"数据集不存在: {rid}"}
        if not _data.is_visible(entry, ctx.owner):
            return {"error": f"无权删除数据集 {rid}（不属于当前用户）"}
        import shutil
        data_path = _data.resolve_data_path(entry.get("data_path", ""))
        managed_root = _data._get_data_dir().resolve()
        files_removed = False
        if data_path.exists() and data_path.is_dir():
            resolved = data_path.resolve()
            if resolved in (managed_root, managed_root.parent, managed_root.parent.parent):
                return {"error": "拒绝删除数据根目录"}
            if resolved.is_relative_to(managed_root):
                shutil.rmtree(resolved, ignore_errors=True)
                files_removed = True
        spec_path = _data._get_def_dir() / f"{rid}.md"
        if spec_path.exists():
            spec_path.unlink()
        await _data.unregister(rid)
        return {"deleted": rid, "type": "dataset", "files_removed": files_removed}

    if rtype == "tool":
        entry = await _tools.get(rid)
        if not entry:
            return {"error": f"工具不存在: {rid}"}
        # 与 DELETE /api/tool/{id} 一致：取消收藏语义，按命中的层删除——
        # 只注销条目、实现保留在仓库；内置与个人定制版互不牵连。
        # 内置层仅超级管理员可删（网关内 current_role 按会话归属人查表）。
        from app.api.deps import current_role
        source = entry.get("source")
        if source != "private" and current_role() != "admin":
            return {"error": f"'{rid}' 是内置工具，仅超级用户可删除；"
                             "你可以先「复制为我的副本」再管理自己的版本"}
        scope = "private" if source == "private" else "builtin"
        await _tools.unregister(rid, scope=scope)
        return {"deleted": rid, "type": "tool", "scope": scope}

    if rtype == "model":
        entry = await _models.get(rid)
        if not entry:
            return {"error": f"模型不存在: {rid}"}
        # 内置模型是随 git 分发的系统资产，删除是全局生效（共享层掩码），
        # 因此仅超级用户可删——与普通用户可自删的私有模型区分开。
        private_ids = {e.get("id") for e in _models._read_private()}
        if rid not in private_ids:
            from core.chat_control.gateway import _role_of
            if _role_of(ctx.owner) != "admin":
                return {"error": f"'{rid}' 是内置模型，仅超级用户可删除"}
        await _models.unregister(rid)
        return {"deleted": rid, "type": "model"}

    if rtype == "agent":
        from core.chat_control.gateway import _role_of
        from core.resource import fork as _fork

        entry = await _agents.get(rid)
        if not entry or not _agents.is_visible(entry, ctx.owner):
            return {"error": f"Agent 不存在: {rid}"}
        from core.agent.factory import agent_factory

        source = entry.get("source")
        await agent_factory.stop(rid)

        if source == "system":
            # 公共层（系统用户目录）：闸门在 ③b 换轨为「必须系统身份」，
            # 过渡期仍按 admin 角色把关。删除对所有用户生效。
            if _role_of(ctx.owner) != "admin":
                return {"error": f"'{rid}' 是系统级 Agent，仅超级用户可删除；"
                                 "你可以先「复制为我的副本」再管理自己的版本"}
            _fork.drop_copy("agents", rid, root="system")
            await _agents.unregister(rid, scope="system")
            return {"deleted": rid, "type": "agent", "scope": "system"}

        # 私有层：删文件（definitions/<id>* + implementations/<id>/，含
        # -demand.md；drop_copy 对缺失路径幂等）+ 删私有条目
        _fork.drop_copy("agents", rid)
        await _agents.unregister(rid, scope="private")
        return {"deleted": rid, "type": "agent", "scope": "private"}

    if rtype == "cron_job":
        from core.cron import store as cron_store
        from core.cron.scheduler import cron_scheduler

        job = _cron_ownership(rid, ctx)
        if isinstance(job, str):
            return {"error": job}
        if not cron_store.delete(rid):
            return {"error": f"定时任务不存在: {rid}"}
        cron_scheduler.remove(rid)
        return {"deleted": rid, "type": "cron_job"}

    return {"error": f"未知资源类型: {rtype}（可选 dataset/tool/model/agent/cron_job）"}

"""资源迁移路由 — 工具导出 / 导入。

对应 UI：工具空间的「导出」「导入」入口，以及内置工具 `tool-transfer`
（Agent 在对话里也能直接调它完成搬运）。

导出产物是 `.sotatool.zip`，默认落在 `<项目根>/data/exports/`，
可通过 `GET /api/migrate/bundles` 列出、`GET /api/migrate/bundles/{name}` 下载。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import get_auth_context
from core.auth.context import AuthContext
from core.auth.ownership import can_write_resource
from core.migration import agent_bundle, tool_bundle
from core.user_context import resolve_owner_id

_PRIVILEGED_TENANT_ROLES = ("owner", "admin")


def _is_admin(ctx: AuthContext) -> bool:
    """租户 Owner/Admin（可看全部迁移包、可兜底覆盖）。"""
    return getattr(ctx, "tenant_role", None) in _PRIVILEGED_TENANT_ROLES


def _load_tool_entry(tool_id: str):
    from core.resource.registry.tool_registry import ToolRegistry

    return ToolRegistry()


def _load_agent_entry(agent_id: str):
    from core.resource.registry.agent_registry import AgentRegistry

    return AgentRegistry()


def _bundle_owner(bundle_path, *, kind: str) -> str | None:
    """读迁移包 manifest 里的 exported_by（读不到返回 None）。"""
    import json as _json
    import zipfile as _zip

    try:
        with _zip.ZipFile(bundle_path) as zf:
            manifest = _json.loads(zf.read("manifest.json").decode("utf-8"))
        return manifest.get("exported_by")
    except Exception:
        return None


def _assert_bundle_visible(ctx: AuthContext, bundle_path, *, kind: str) -> None:
    """迁移包可见性：本人或租户管理员；旧包无字段则仅管理员可见。"""
    if _is_admin(ctx):
        return
    owner = _bundle_owner(bundle_path, kind=kind)
    if not owner or owner != resolve_owner_id(ctx):
        raise HTTPException(404, "迁移包不存在")


def _assert_can_export(ctx: AuthContext, entry: dict | None, *, kind: str) -> None:
    """导出要求对资源有写权限（D2）：否则可把别人私有工具源码打包带走。"""
    if entry is None or not can_write_resource(ctx, entry):
        raise HTTPException(404, f"{kind}不存在")

router = APIRouter()


def _deps_report(tool_id: str) -> dict:
    """导入后立刻体检依赖：缺失的包名 + 可直接 pip 安装的包名。

    体检本身要起子进程逐个 import，放到线程池里跑，避免阻塞事件循环。
    """
    try:
        from core.deps import check_tool_dependencies

        info = check_tool_dependencies(tool_id)
        missing = info.get("missing", [])
        deps = info.get("deps", [])
        return {
            "missing": missing,
            "install": info.get("install", []),
            "pairs": info.get("pairs", []),
            "satisfied": [m for m in deps if m not in missing],
            "python": info.get("python", ""),
            "env": info.get("env", ""),
        }
    except Exception as e:
        # 体检失败不阻塞导入，前端按"未知"处理
        return {"missing": [], "install": [], "pairs": [], "satisfied": [],
                "error": str(e)}


def _ensure_in_workspace(
    tool_id: str, name: str, tags: list[str] | None, owner_id: str | None = None
) -> None:
    """把导入的工具直接放进「工具空间」。

    工具空间（左侧列表）展示的是 storage/workspace_tools.json 里的**已加载清单**，
    不是 registry 全量。只写 registry 的话，工具会出现在「工具仓库」但工具空间看不到，
    使用者会以为导入失败。导入即视为"加载"，避免这一步心智负担。
    """
    try:
        from app.api.routes.tool_routes import _read_workspace, _write_workspace
        items = _read_workspace(owner_id)
        if any(t.get("id") == tool_id for t in items):
            return
        items.append({
            "id": tool_id,
            "name": name,
            "tags": tags or [],
            "loadedAt": datetime.now().isoformat(),
        })
        _write_workspace(items, owner_id)
    except Exception:
        # 工具空间清单写失败不影响导入本身（可从工具仓库手动加载）
        pass


class ExportToolRequest(BaseModel):
    tool_id: str
    include_tests: bool = True
    include_assets: bool = True


class ImportToolRequest(BaseModel):
    """三种来源三选一：本机路径 / 导出目录里的文件名 / base64 内容。"""

    bundle_path: str | None = None
    bundle_name: str | None = None
    bundle_base64: str | None = None
    overwrite: bool = False


@router.get("/info")
async def migrate_info():
    """迁移能力自述（供前端/Agent 探测）。"""
    return {
        "bundle_format": tool_bundle.BUNDLE_FORMAT,
        "format_version": tool_bundle.BUNDLE_VERSION,
        "suffix": tool_bundle.BUNDLE_SUFFIX,
        "export_dir": str(tool_bundle.export_dir()),
        "supports": ["tool", "agent"],
        "suffixes": {"tool": tool_bundle.BUNDLE_SUFFIX,
                     "agent": agent_bundle.BUNDLE_SUFFIX},
    }


@router.post("/tool/export")
async def export_tool(
    req: ExportToolRequest, ctx: AuthContext = Depends(get_auth_context)
):
    """把工具打成一个迁移包（需对该工具写权限）。"""
    entry = await _load_tool_entry(req.tool_id).get(req.tool_id)
    _assert_can_export(ctx, entry, kind="工具")
    try:
        result = await asyncio.to_thread(
            tool_bundle.export_tool,
            req.tool_id,
            None,
            req.include_tests,
            req.include_assets,
            exported_by=resolve_owner_id(ctx),
            allow_other=_is_admin(ctx),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # 磁盘异常等
        raise HTTPException(500, f"导出失败: {e}") from e
    return {"status": "success", **result}


@router.get("/bundles")
async def list_bundles(ctx: AuthContext = Depends(get_auth_context)):
    """列出导出目录下的迁移包（默认只看自己导出的；管理员看全部）。"""
    items = await asyncio.to_thread(
        tool_bundle.list_bundles,
        None,
        user_id=resolve_owner_id(ctx),
        include_all=_is_admin(ctx),
    )
    return {"export_dir": str(tool_bundle.export_dir()), "bundles": items, "total": len(items)}


@router.get("/bundles/{name}")
async def download_bundle(name: str, ctx: AuthContext = Depends(get_auth_context)):
    """下载某个迁移包（仅本人导出或租户管理员）。"""
    try:
        p = tool_bundle.resolve_bundle(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    _assert_bundle_visible(ctx, p, kind="tool")
    return FileResponse(str(p), filename=p.name, media_type="application/zip")


@router.post("/tool/import")
async def import_tool(
    req: ImportToolRequest, ctx: AuthContext = Depends(get_auth_context)
):
    """从本机路径 / 导出目录文件名 / base64 内容导入工具。

    导入后的 registry owner = **导入者 UUID**；``overwrite`` 默认只允许覆盖自己的
    工具（租户管理员可兜底）。
    """
    owner = resolve_owner_id(ctx)
    allow_overwrite = _is_admin(ctx)
    try:
        if req.bundle_base64:
            try:
                raw = base64.b64decode(req.bundle_base64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise HTTPException(400, f"bundle_base64 解码失败: {e}") from e
            result = await asyncio.to_thread(
                tool_bundle.import_tool, None, raw, req.overwrite,
                owner_id=owner, allow_overwrite=allow_overwrite,
            )
        elif req.bundle_name:
            p = tool_bundle.resolve_bundle(req.bundle_name)
            _assert_bundle_visible(ctx, p, kind="tool")
            result = await asyncio.to_thread(
                tool_bundle.import_tool, p, None, req.overwrite,
                owner_id=owner, allow_overwrite=allow_overwrite,
            )
        elif req.bundle_path:
            from core.file_guard import PathAccessError, resolve_checked_path

            try:
                # 归属校验：不得读取他人上传目录里的包（与 bundle_name 分支同口径）
                bundle_path = resolve_checked_path(
                    req.bundle_path, must_exist=True, owner_id=owner
                )
            except PathAccessError as e:
                raise HTTPException(400, f"bundle_path 不合法: {e}") from e
            # 可见性校验：bundle_name 分支有，本机路径分支此前漏了（v3 评审）
            _assert_bundle_visible(ctx, bundle_path, kind="tool")
            result = await asyncio.to_thread(
                tool_bundle.import_tool, bundle_path, None, req.overwrite,
                owner_id=owner, allow_overwrite=allow_overwrite,
            )
        else:
            raise HTTPException(400, "必须提供 bundle_path / bundle_name / bundle_base64 之一")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}") from e
    _ensure_in_workspace(result["tool_id"], result["name"], result.get("tags"), owner)
    deps = await asyncio.to_thread(_deps_report, result["tool_id"])
    return {"status": "success", "added_to_workspace": True, "deps": deps, **result}


@router.post("/tool/import-upload")
async def import_tool_upload(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    ctx: AuthContext = Depends(get_auth_context),
):
    """上传 `.sotatool.zip` 直接导入（跨机器搬运最常见的方式）。"""
    owner = resolve_owner_id(ctx)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "上传内容为空")
    try:
        result = await asyncio.to_thread(
            tool_bundle.import_tool, None, raw, overwrite,
            owner_id=owner, allow_overwrite=_is_admin(ctx),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}") from e
    _ensure_in_workspace(result["tool_id"], result["name"], result.get("tags"), owner)
    deps = await asyncio.to_thread(_deps_report, result["tool_id"])
    return {"status": "success", "added_to_workspace": True, "deps": deps, **result}


# ══════════════════════════════════════════════════════════════
#  Agent 迁移（.sotaagent.zip）
# ══════════════════════════════════════════════════════════════

class ExportAgentRequest(BaseModel):
    agent_id: str
    include_assets: bool = True


class ImportAgentRequest(BaseModel):
    bundle_path: str | None = None
    bundle_name: str | None = None
    bundle_base64: str | None = None
    overwrite: bool = False


def _agent_deps_report(agent_id: str) -> dict:
    """Agent 的第三方依赖体检（agent.py 的 import + 规范里的依赖表）。"""
    try:
        info = agent_bundle.inspect_agent_dependencies(agent_id)
        missing = info.get("missing", [])
        deps = info.get("deps", [])
        return {
            "missing": missing,
            "install": info.get("install", []),
            "pairs": info.get("pairs", []),
            "satisfied": [d for d in deps if d not in missing],
            "python": info.get("python", ""),
            "env": info.get("env", ""),
        }
    except Exception as e:
        return {"missing": [], "install": [], "pairs": [], "satisfied": [], "error": str(e)}


@router.post("/agent/export")
async def export_agent(
    req: ExportAgentRequest, ctx: AuthContext = Depends(get_auth_context)
):
    """把 Agent 打成一个迁移包（需对该 Agent 写权限）。"""
    entry = await _load_agent_entry(req.agent_id).get(req.agent_id)
    _assert_can_export(ctx, entry, kind="Agent")
    try:
        result = await asyncio.to_thread(
            agent_bundle.export_agent,
            req.agent_id,
            None,
            req.include_assets,
            exported_by=resolve_owner_id(ctx),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"导出失败: {e}") from e
    return {"status": "success", **result}


@router.get("/agent/bundles")
async def list_agent_bundles(ctx: AuthContext = Depends(get_auth_context)):
    """列出 Agent 迁移包（默认只看自己导出的；管理员看全部）。"""
    items = await asyncio.to_thread(
        agent_bundle.list_agent_bundles,
        None,
        user_id=resolve_owner_id(ctx),
        include_all=_is_admin(ctx),
    )
    return {"export_dir": str(agent_bundle.export_dir()), "bundles": items, "total": len(items)}


@router.get("/agent/bundles/{name}")
async def download_agent_bundle(name: str, ctx: AuthContext = Depends(get_auth_context)):
    """下载 Agent 迁移包（仅本人导出或租户管理员）。"""
    try:
        p = agent_bundle.resolve_agent_bundle(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    _assert_bundle_visible(ctx, p, kind="agent")
    return FileResponse(str(p), filename=p.name, media_type="application/zip")


@router.post("/agent/import")
async def import_agent(
    req: ImportAgentRequest, ctx: AuthContext = Depends(get_auth_context)
):
    """从本机路径 / 导出目录文件名 / base64 内容导入 Agent。

    导入后的 registry owner = **导入者 UUID**；``overwrite`` 默认只允许覆盖自己的
    Agent（租户管理员可兜底）。
    """
    owner = resolve_owner_id(ctx)
    allow_overwrite = _is_admin(ctx)
    try:
        if req.bundle_base64:
            try:
                raw = base64.b64decode(req.bundle_base64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise HTTPException(400, f"bundle_base64 解码失败: {e}") from e
            result = await asyncio.to_thread(
                agent_bundle.import_agent, None, raw, req.overwrite,
                owner_id=owner, allow_overwrite=allow_overwrite,
            )
        elif req.bundle_name:
            p = agent_bundle.resolve_agent_bundle(req.bundle_name)
            _assert_bundle_visible(ctx, p, kind="agent")
            result = await asyncio.to_thread(
                agent_bundle.import_agent, p, None, req.overwrite,
                owner_id=owner, allow_overwrite=allow_overwrite,
            )
        elif req.bundle_path:
            from core.file_guard import PathAccessError, resolve_checked_path

            try:
                # 归属校验：不得读取他人上传目录里的包（与 bundle_name 分支同口径）
                bundle_path = resolve_checked_path(
                    req.bundle_path, must_exist=True, owner_id=owner
                )
            except PathAccessError as e:
                raise HTTPException(400, f"bundle_path 不合法: {e}") from e
            # 可见性校验：bundle_name 分支有，本机路径分支此前漏了（v3 评审）
            _assert_bundle_visible(ctx, bundle_path, kind="agent")
            result = await asyncio.to_thread(
                agent_bundle.import_agent, bundle_path, None, req.overwrite,
                owner_id=owner, allow_overwrite=allow_overwrite,
            )
        else:
            raise HTTPException(400, "必须提供 bundle_path / bundle_name / bundle_base64 之一")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}") from e
    deps = await asyncio.to_thread(_agent_deps_report, result["agent_id"])
    return {"status": "success", "deps": deps, **result}


@router.post("/agent/{agent_id}/install-deps")
async def install_agent_deps(
    agent_id: str, req: dict, ctx: AuthContext = Depends(get_auth_context)
):
    """把 Agent 的 Python 依赖装进它的执行环境（项目 venv；需写权限）。

    Agent 没有独立 venv 概念，复用工具那套安装器即可：
    `_get_exec_python(agent_id)` 找不到 agent 专属 venv 时会回退项目 venv。
    """
    entry = await _load_agent_entry(agent_id).get(agent_id)
    if entry is None or not can_write_resource(ctx, entry):
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    dependencies = req.get("dependencies", [])
    if not dependencies:
        raise HTTPException(400, "dependencies 不能为空")
    try:
        from app.api.routes.tool_routes import builder
        result = await builder.install_deps(agent_id, dependencies)
    except Exception as e:
        raise HTTPException(500, f"安装失败: {e}") from e
    return result


@router.post("/agent/import-upload")
async def import_agent_upload(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    ctx: AuthContext = Depends(get_auth_context),
):
    """上传 `.sotaagent.zip` 直接导入。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "上传内容为空")
    try:
        result = await asyncio.to_thread(
            agent_bundle.import_agent, None, raw, overwrite,
            owner_id=resolve_owner_id(ctx), allow_overwrite=_is_admin(ctx),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}") from e
    deps = await asyncio.to_thread(_agent_deps_report, result["agent_id"])
    return {"status": "success", "deps": deps, **result}

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

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.migration import agent_bundle, tool_bundle

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


def _ensure_in_workspace(tool_id: str, name: str, tags: list[str] | None) -> None:
    """把导入的工具直接放进「工具空间」。

    工具空间（左侧列表）展示的是 storage/workspace_tools.json 里的**已加载清单**，
    不是 registry 全量。只写 registry 的话，工具会出现在「工具仓库」但工具空间看不到，
    使用者会以为导入失败。导入即视为"加载"，避免这一步心智负担。
    """
    try:
        # 本地 tool_routes 用「读-改-写锁 + 原子写」保护 workspace 清单：
        # 必须持锁操作，否则导入写清单会与用户的加载/移除操作互相覆盖。
        from app.api.routes.tool_routes import (
            _WORKSPACE_LOCK, _read_workspace_locked, _write_workspace_locked)
        with _WORKSPACE_LOCK:
            items = _read_workspace_locked()
            if any(t.get("id") == tool_id for t in items):
                return
            items.append({
                "id": tool_id,
                "name": name,
                "tags": tags or [],
                "loadedAt": datetime.now().isoformat(),
            })
            _write_workspace_locked(items)
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
async def export_tool(req: ExportToolRequest):
    """把工具打成一个迁移包。"""
    try:
        result = await asyncio.to_thread(
            tool_bundle.export_tool,
            req.tool_id,
            None,
            req.include_tests,
            req.include_assets,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # 磁盘异常等
        raise HTTPException(500, f"导出失败: {e}") from e
    return {"status": "success", **result}


@router.get("/bundles")
async def list_bundles():
    """列出导出目录下的迁移包。"""
    items = await asyncio.to_thread(tool_bundle.list_bundles)
    return {"export_dir": str(tool_bundle.export_dir()), "bundles": items, "total": len(items)}


@router.get("/bundles/{name}")
async def download_bundle(name: str):
    """下载某个迁移包。"""
    try:
        p = tool_bundle.resolve_bundle(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(str(p), filename=p.name, media_type="application/zip")


@router.post("/tool/import")
async def import_tool(req: ImportToolRequest):
    """从本机路径 / 导出目录文件名 / base64 内容导入工具。"""
    try:
        if req.bundle_base64:
            try:
                raw = base64.b64decode(req.bundle_base64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise HTTPException(400, f"bundle_base64 解码失败: {e}") from e
            result = await asyncio.to_thread(
                tool_bundle.import_tool, None, raw, req.overwrite
            )
        elif req.bundle_name:
            p = tool_bundle.resolve_bundle(req.bundle_name)
            result = await asyncio.to_thread(
                tool_bundle.import_tool, p, None, req.overwrite
            )
        elif req.bundle_path:
            result = await asyncio.to_thread(
                tool_bundle.import_tool, Path(req.bundle_path), None, req.overwrite
            )
        else:
            raise HTTPException(400, "必须提供 bundle_path / bundle_name / bundle_base64 之一")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}") from e
    _ensure_in_workspace(result["tool_id"], result["name"], result.get("tags"))
    deps = await asyncio.to_thread(_deps_report, result["tool_id"])
    return {"status": "success", "added_to_workspace": True, "deps": deps, **result}


@router.post("/tool/import-upload")
async def import_tool_upload(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
):
    """上传 `.sotatool.zip` 直接导入（跨机器搬运最常见的方式）。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "上传内容为空")
    try:
        result = await asyncio.to_thread(
            tool_bundle.import_tool, None, raw, overwrite
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}") from e
    _ensure_in_workspace(result["tool_id"], result["name"], result.get("tags"))
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
async def export_agent(req: ExportAgentRequest):
    """把 Agent 打成一个迁移包。"""
    try:
        result = await asyncio.to_thread(
            agent_bundle.export_agent, req.agent_id, None, req.include_assets
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"导出失败: {e}") from e
    return {"status": "success", **result}


@router.get("/agent/bundles")
async def list_agent_bundles():
    items = await asyncio.to_thread(agent_bundle.list_agent_bundles)
    return {"export_dir": str(agent_bundle.export_dir()), "bundles": items, "total": len(items)}


@router.get("/agent/bundles/{name}")
async def download_agent_bundle(name: str):
    try:
        p = agent_bundle.resolve_agent_bundle(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(str(p), filename=p.name, media_type="application/zip")


@router.post("/agent/import")
async def import_agent(req: ImportAgentRequest):
    """从本机路径 / 导出目录文件名 / base64 内容导入 Agent。"""
    try:
        if req.bundle_base64:
            try:
                raw = base64.b64decode(req.bundle_base64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise HTTPException(400, f"bundle_base64 解码失败: {e}") from e
            result = await asyncio.to_thread(
                agent_bundle.import_agent, None, raw, req.overwrite
            )
        elif req.bundle_name:
            p = agent_bundle.resolve_agent_bundle(req.bundle_name)
            result = await asyncio.to_thread(
                agent_bundle.import_agent, p, None, req.overwrite
            )
        elif req.bundle_path:
            result = await asyncio.to_thread(
                agent_bundle.import_agent, Path(req.bundle_path), None, req.overwrite
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
async def install_agent_deps(agent_id: str, req: dict):
    """把 Agent 的 Python 依赖装进它的执行环境（项目 venv）。

    不能复用工具安装器：ToolCodeBuilder.install_deps 会把 agent_id 当成
    工具 id 去 tools/implementations/ 下找实现目录，找不到就在私有根凭空
    创建一个工具 .venv——装进去的包 Agent 运行时根本看不到。Agent 没有
    独立 venv，执行解释器是项目 venv（agent_bundle._agent_python），
    所以用 uv 直接 `uv pip install --python <项目 venv>` 安装。
    """
    dependencies = req.get("dependencies", [])
    if not dependencies:
        raise HTTPException(400, "dependencies 不能为空")

    from core.migration.agent_bundle import _agent_python
    from core.resource.deps import uv_bin, uv_pip_install_argv

    python_exe = _agent_python()

    def _install_one(dep: str) -> dict:
        import subprocess

        from core.deps import import_name_for
        mod = import_name_for(dep)
        probe = (
            "import importlib.util, sys; "
            "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
        )
        try:
            r = subprocess.run([python_exe, "-c", probe, mod],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return {"dep": dep, "success": True, "already": True}
        except Exception:
            pass
        try:
            uv = uv_bin()
        except RuntimeError as e:
            return {"dep": dep, "success": False, "reason": str(e)}
        proc = subprocess.run(
            uv_pip_install_argv(uv, python_exe, dep),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=1800,
        )
        if proc.returncode == 0:
            return {"dep": dep, "success": True}
        tail = (proc.stdout or "") + (proc.stderr or "")
        return {"dep": dep, "success": False, "reason": tail[-2000:]}

    results = [await asyncio.to_thread(_install_one, d)
               for d in dependencies if str(d).strip()]
    return {"python": python_exe, "env": "project_venv", "results": results}


@router.post("/agent/import-upload")
async def import_agent_upload(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
):
    """上传 `.sotaagent.zip` 直接导入。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "上传内容为空")
    try:
        result = await asyncio.to_thread(
            agent_bundle.import_agent, None, raw, overwrite
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}") from e
    deps = await asyncio.to_thread(_agent_deps_report, result["agent_id"])
    return {"status": "success", "deps": deps, **result}

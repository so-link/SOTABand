"""资源迁移路由 — 导入与依赖安装。

对应 UI：资源空间的「导入 .sotatool.zip / .sotaagent.zip」入口，以及内置
搬运工具 `tool-transfer` / `agent-transfer`（Agent 在对话里直接进程内调用
core.migration 完成搬运，不经这里的 HTTP 端点）。

导出已统一到资源详情页标题条的直链下载（GET /api/tool/{id}/export、
GET /api/agent/{id}/export），内存打包、不落服务器磁盘。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import require_superuser
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


# 依赖规格白名单：包名 + 可选 extras + 版本约束，仅此而已。
# 明确拒绝：以 - 开头（会被 uv 解析成选项，如 --index-url 换源、
# --config-settings 注入构建参数）、file:/path:/URL 规格、环境标记
# （`;` 段）、以及一切空格/引号/分号等分隔字符——安装目标是**共享
# 项目 venv**，一条恶意规格会影响全体用户的运行环境。
_DEP_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"                  # 包名
    r"(\[[A-Za-z0-9._,\-]+\])?"                     # 可选 extras
    r"((===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9*.+!_\-]+)*$"  # 版本约束
)


@router.post("/agent/{agent_id}/install-deps")
async def install_agent_deps(agent_id: str, req: dict,
                             _su=Depends(require_superuser)):
    """把 Agent 的 Python 依赖装进它的执行环境（项目 venv）。限超管。

    不能复用工具安装器：ToolCodeBuilder.install_deps 会把 agent_id 当成
    工具 id 去 tools/implementations/ 下找实现目录，找不到就在私有根凭空
    创建一个工具 .venv——装进去的包 Agent 运行时根本看不到。Agent 没有
    独立 venv，执行解释器是项目 venv（agent_bundle._agent_python），
    所以用 uv 直接 `uv pip install --python <项目 venv>` 安装。

    安装目标是全体用户共享的项目 venv，因此限超管（与 config/llm 等平台
    资产同级），并逐条通过 _DEP_SPEC_RE 白名单后才进 argv。
    """
    from core.resource.registry.agent_registry import AgentRegistry

    if not await AgentRegistry().get(agent_id):
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    dependencies = req.get("dependencies", [])
    if not dependencies:
        raise HTTPException(400, "dependencies 不能为空")
    cleaned = [str(d).strip() for d in dependencies if str(d).strip()]
    for d in cleaned:
        if not _DEP_SPEC_RE.match(d):
            raise HTTPException(400, f"依赖规格不被允许: {d!r}（仅支持「包名[extras] + 版本约束」）")

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

    results = [await asyncio.to_thread(_install_one, d) for d in cleaned]
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

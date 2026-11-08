"""HTTP endpoints for long-running platform tasks.

## 归属（2026-11 起）

任务注册表里保存了提交者（``owner``）。此前任何登录用户都能 list 到别人的
训练任务（连同 ``command`` / ``cwd`` / ``log_path``），也能 cancel 别人的任务。
现在所有端点都要求 ``AuthContext`` 并按 ``ctx.user_id`` 过滤：

- 不是自己的任务一律 404（而不是 403）—— 不确认「这个 id 存在但不属于你」；
- 内部调用（worker / workflow / 脚本）仍走 ``TaskManager(owner=None)``，
  不受 HTTP 归属过滤影响。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_auth_context
from core.auth.context import AuthContext
from core.task.task_manager import TaskManager
from core.user_context import resolve_owner_id

router = APIRouter()
manager = TaskManager()


@router.get("/list")
async def list_tasks(
    limit: int = Query(100, ge=1, le=500),
    ctx: AuthContext = Depends(get_auth_context),
):
    """只返回当前用户提交的任务。"""
    return {"tasks": manager.list_all(limit, owner=resolve_owner_id(ctx))}


@router.get("/{task_id}")
async def get_task(task_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """读取任务详情（他人任务 → 404，避免泄露 command / cwd / log_path）。"""
    task = manager.get(task_id, owner=resolve_owner_id(ctx))
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    return {"task": task}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """取消任务（只能取消自己的；他人任务 → 404）。"""
    task = manager.cancel(task_id, owner=resolve_owner_id(ctx))
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    return {"task": task}

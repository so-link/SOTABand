"""长任务队列路由（从 main 移植）。

与 wxbit 已有的 ``/api/task/*`` 的区别：

- ``/api/task/*``：Agent 执行**历史**记录（SQLite ``platform.db`` 的
  ``task_history``），前端用于「最近使用的 Agent」与历史回看；
- ``/api/task-jobs/*``（本模块）：**长任务队列**——工具提交的耗时作业，
  由 ``core.task`` 的独立 worker 子进程执行、日志落盘、状态供前端轮询。

两者语义不同但 main 与 wxbit 都占用 ``/api/task``，因此这里改用
``/api/task-jobs`` 前缀，互不覆盖。

归属：所有端点按当前用户过滤；他人任务一律 404（而不是 403），
不确认「这个 id 存在但不属于你」，避免泄露 ``command`` / ``cwd`` / ``log_path``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_login
from core.task.task_manager import TaskManager
from core.user_context import get_current_user_id

router = APIRouter()


@router.get("/list")
async def list_jobs(
    limit: int = Query(100, ge=1, le=500),
    _user=Depends(require_login),
):
    """只返回当前用户提交的长任务。"""
    return {"tasks": TaskManager().list_all(limit, owner=get_current_user_id())}


@router.get("/{task_id}")
async def get_job(task_id: str, _user=Depends(require_login)):
    """任务详情（含实时进度）。"""
    task = TaskManager().get(task_id, owner=get_current_user_id())
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    return {"task": task}


@router.post("/{task_id}/cancel")
async def cancel_job(task_id: str, _user=Depends(require_login)):
    """取消任务（只能取消自己的）。"""
    task = TaskManager().cancel(task_id, owner=get_current_user_id())
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    return {"task": task}

"""HTTP endpoints for long-running platform tasks."""

from fastapi import APIRouter, HTTPException, Query

from core.task.task_manager import TaskManager


router = APIRouter()
manager = TaskManager()


@router.get("/list")
async def list_tasks(limit: int = Query(100, ge=1, le=500)):
    return {"tasks": manager.list_all(limit)}


@router.get("/{task_id}")
async def get_task(task_id: str):
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    return {"task": task}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    task = manager.cancel(task_id)
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    return {"task": task}

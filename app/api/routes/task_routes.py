"""任务历史路由 — Agent 执行记录（SQLite storage/platform.db）"""

from fastapi import APIRouter, HTTPException, Query

from core.observability import task_history

router = APIRouter()


@router.get("/list")
async def list_tasks(
    limit: int = Query(30, ge=1, le=200),
    agent_id: str | None = Query(None),
):
    """最近任务历史（时间倒序）。"""
    return {"tasks": task_history.recent(limit=limit, agent_id=agent_id)}


@router.delete("/{task_id}")
async def delete_task(task_id: int):
    """删除一条任务历史记录。"""
    if not task_history.delete(task_id):
        raise HTTPException(status_code=404, detail="任务记录不存在")
    return {"ok": True}


@router.get("/recent-agents")
async def recent_agents(limit: int = Query(10, ge=1, le=50)):
    """按最近执行时间排序的 Agent（供「最近使用的 Agent」面板）。"""
    return {"agents": task_history.last_used_per_agent(limit=limit)}

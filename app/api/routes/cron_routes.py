"""定时任务 API（core/cron 模块的 HTTP 层）。

设计文档：docs/cron-scheduler-design.md 第 5 节。
写接口由 auth 中间件统一要求登录；owner 取当前用户（get_current_user_id），
后台触发时按 owner 注入身份。

多用户：任务存在当前用户自己的 platform.db，因此所有 store/scheduler 调用都
带上 uid=当前用户——天然只操作自己的库，不存在越权看到/改到别人的任务。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.cron import runner, store
from core.cron.cron_parse import CronError
from core.cron.scheduler import cron_scheduler
from core.user_context import get_current_user_id

router = APIRouter()


class JobPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: str = Field(..., pattern="^(tool|agent)$")
    target_id: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    cron: str = Field(..., min_length=5)
    timezone: str = ""
    enabled: bool = True
    timeout: int = Field(600, ge=5, le=7200)
    misfire: str = Field("skip", pattern="^(skip|fire_once)$")


def _with_display(job: dict[str, Any]) -> dict[str, Any]:
    """补充前端展示字段：下次/上次执行的本地时间串。"""
    j = dict(job)
    j["next_run_local"] = runner.as_local(j.get("next_run_at"), j.get("timezone") or "")
    j["last_run_local"] = runner.as_local(j.get("last_run_at"), j.get("timezone") or "")
    return j


async def _validate(payload: JobPayload) -> tuple[str, str, float]:
    try:
        cron, tz, ts = runner.validate_job_payload(payload.model_dump())
    except CronError as exc:
        raise HTTPException(422, str(exc)) from exc
    ok, msg = await runner.check_target(payload.kind, payload.target_id,
                                        owner=get_current_user_id())
    if not ok:
        raise HTTPException(422, msg)
    return cron, tz, ts


@router.get("/jobs")
async def list_jobs():
    """当前用户的任务列表（含禁用）。

    不做 owner 过滤——库本身就是这个用户的，再按 owner 列过滤反而会让历史
    条目（owner 为空）凭空消失。owner 仅用于后台触发时注入资源身份。
    """
    me = get_current_user_id()
    jobs = store.list_jobs(uid=me)
    return {"jobs": [_with_display(j) for j in jobs]}


@router.post("/jobs")
async def create_job(payload: JobPayload):
    cron, tz, ts = await _validate(payload)
    me = get_current_user_id()
    job = store.create({
        "name": payload.name, "kind": payload.kind, "target_id": payload.target_id,
        "params": payload.params, "cron": cron, "timezone": tz,
        "enabled": payload.enabled, "owner": me or "",
        "timeout": payload.timeout, "misfire": payload.misfire, "next_run_at": ts,
    }, uid=me)
    cron_scheduler.upsert(job["id"], me)
    return {"job": _with_display(job)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = store.get(job_id, get_current_user_id())
    if not job:
        raise HTTPException(404, "任务不存在")
    return {"job": _with_display(job)}


@router.put("/jobs/{job_id}")
async def update_job(job_id: str, payload: JobPayload):
    me = get_current_user_id()
    if not store.get(job_id, me):
        raise HTTPException(404, "任务不存在")
    cron, tz, ts = await _validate(payload)
    job = store.update(job_id, {
        "name": payload.name, "kind": payload.kind, "target_id": payload.target_id,
        "params": payload.params, "cron": cron, "timezone": tz,
        "enabled": payload.enabled, "timeout": payload.timeout,
        "misfire": payload.misfire, "next_run_at": ts,
    }, uid=me)
    cron_scheduler.upsert(job_id, me)
    return {"job": _with_display(job or {})}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    me = get_current_user_id()
    if not store.delete(job_id, uid=me):
        raise HTTPException(404, "任务不存在")
    cron_scheduler.remove(job_id, me)
    return {"ok": True}


@router.post("/jobs/{job_id}/toggle")
async def toggle_job(job_id: str):
    """启用/禁用切换（不改动其它字段）。"""
    me = get_current_user_id()
    job = store.get(job_id, me)
    if not job:
        raise HTTPException(404, "任务不存在")
    enabled = not job["enabled"]
    updates: dict[str, Any] = {"enabled": enabled}
    if enabled:
        try:
            updates["next_run_at"] = runner.validate_job_payload(
                {"cron": job["cron"], "timezone": job["timezone"]})[2]
        except CronError as exc:
            raise HTTPException(422, f"cron 已失效，无法启用：{exc}") from exc
    job = store.update(job_id, updates, uid=me)
    cron_scheduler.upsert(job_id, me)
    return {"job": _with_display(job or {})}


@router.post("/jobs/{job_id}/trigger")
async def trigger_job(job_id: str):
    """手动立即执行（后台异步跑，前端轮询 /jobs/{id}/runs）。"""
    me = get_current_user_id()
    if not store.get(job_id, me):
        raise HTTPException(404, "任务不存在")
    result = cron_scheduler.trigger_now(job_id, me)
    if result.get("status") == "failed":
        raise HTTPException(409, result.get("error") or "触发失败")
    return result


@router.get("/jobs/{job_id}/runs")
async def job_runs(job_id: str, limit: int = Query(20, ge=1, le=100)):
    return {"runs": store.runs(job_id, limit, uid=get_current_user_id())}


@router.get("/runs/recent")
async def recent_runs(limit: int = Query(30, ge=1, le=100)):
    return {"runs": store.recent_runs(limit, uid=get_current_user_id())}


@router.get("/options")
async def cron_options():
    """前端表单的候选项：工具 / Agent 下拉列表 + 常用时区。"""
    from core.resource.registry.agent_registry import AgentRegistry
    from core.resource.registry.tool_registry import ToolRegistry
    tools = await ToolRegistry().list_all()
    agents = await AgentRegistry().list_all()
    return {
        "tools": [
            {"id": t["id"], "name": t.get("name") or t["id"],
             "description": (t.get("description") or "")[:120]}
            for t in tools if t.get("status", "active") == "active"
        ],
        "agents": [
            {"id": a["id"], "name": a.get("name") or a["id"],
             "description": (a.get("description") or "")[:120]}
            for a in agents
        ],
        "timezones": ["Asia/Shanghai", "UTC", "Asia/Tokyo", "Europe/London", "America/New_York"],
    }

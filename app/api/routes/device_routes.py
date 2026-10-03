"""设备空间 API 路由 — 设备运行状态（CPU / 内存 / 显存 / 外存）

- GET  /api/devices     当前快照（调试 / 测试用）
- WS   /api/devices/ws  首条 init（主机信息 + 历史曲线），之后每个采样周期推送 snapshot
- GET  /api/devices/allocations                当前调度预留（正在运行的工具 / Agent）
- GET  /api/devices/requirements/tool/{id}     工具的资源需求（声明值或推断值）
- GET  /api/devices/requirements/agent/{id}    Agent 的资源需求（含其调用工具）
"""

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from core.scheduler import sysinfo
from core.scheduler.allocator import get_allocator
from core.scheduler.monitor import get_device_monitor
from core.scheduler.requirements import (
    RequirementsError,
    resolve_agent_requirements,
    resolve_tool_requirements,
)

router = APIRouter(tags=["device"])


@router.get("")
async def get_devices():
    monitor = get_device_monitor()
    snap = await asyncio.to_thread(monitor.snapshot)
    return {
        "gpu_provider": monitor.gpu_provider.kind,
        "driver_version": monitor.gpu_provider.driver_version,
        "host": sysinfo.host_info(),
        "latest": snap,
    }


@router.get("/allocations")
async def get_allocations():
    return {"allocations": get_allocator().allocations()}


@router.get("/requirements/{kind}/{resource_id}")
async def get_requirements(kind: str, resource_id: str):
    resolvers = {"tool": resolve_tool_requirements, "agent": resolve_agent_requirements}
    if kind not in resolvers:
        raise HTTPException(404, f"未知资源类型 {kind!r}（可用：tool / agent）")
    try:
        req = await asyncio.to_thread(resolvers[kind], resource_id)
    except RequirementsError as e:
        raise HTTPException(422, str(e)) from e
    return {"kind": kind, "id": resource_id, "requirements": req.as_dict()}


@router.websocket("/ws")
async def devices_ws(ws: WebSocket):
    await ws.accept()
    monitor = get_device_monitor()
    queue = monitor.subscribe()
    try:
        await ws.send_json(monitor.init_message())
        while True:
            await ws.send_json(await queue.get())
    except (WebSocketDisconnect, RuntimeError):
        pass  # 客户端断开：send 抛 WebSocketDisconnect 或 RuntimeError（连接已关闭）
    finally:
        monitor.unsubscribe(queue)

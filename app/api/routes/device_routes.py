"""设备空间 API 路由 — 设备运行状态（CPU / 内存 / 显存 / 外存）

- GET  /api/devices     当前快照（调试 / 测试用）
- WS   /api/devices/ws  首条 init（主机信息 + 历史曲线），之后每个采样周期推送 snapshot
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.scheduler import sysinfo
from core.scheduler.monitor import get_device_monitor

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

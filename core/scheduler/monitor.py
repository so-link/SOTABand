"""设备监控器 — 共享采样 + 推送

一个进程只有一个 DeviceMonitor（get_device_monitor）：
- 有 WebSocket 订阅者时，后台循环每 interval 秒采样一次并广播给所有订阅者；
  最后一个订阅者离开后循环自动停止，不在后台空转。
- 最近 history_seconds 的精简数据点保存在环形缓冲区，新连接建立时一次性下发，
  前端曲线无需等待即可绘制。只在内存中，不持久化。
- snapshot() 供 REST 接口与同进程的调度器直接读取，数据足够新时复用缓存。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from pathlib import Path

from core.scheduler import sysinfo
from core.scheduler.gpu import GpuProvider, get_gpu_provider

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
log = logging.getLogger("sotaband.devices")


class DeviceMonitor:
    def __init__(
        self,
        gpu_provider: GpuProvider | None = None,
        project_root: Path = PROJECT_ROOT,
        interval: float = 2.0,
        history_seconds: float = 600,
    ):
        self.interval = interval
        self._gpu = gpu_provider or get_gpu_provider()
        self._project_root = project_root
        self._history: deque[dict] = deque(maxlen=max(1, int(history_seconds / interval)))
        self._latest: dict | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        # 采样可能同时来自后台循环线程与 REST / 调度器调用；psutil 的 cpu_percent
        # 基线与 Mock 的随机游走都是共享状态，需串行化
        self._lock = threading.Lock()
        sysinfo.prime_cpu()

    @property
    def gpu_provider(self) -> GpuProvider:
        return self._gpu

    # ── 采样 ──

    def sample(self) -> dict:
        """立即采样一次（同步阻塞），并记入历史"""
        with self._lock:
            try:
                gpus = self._gpu.list_gpus()
            except Exception:  # 显卡掉线 / 驱动异常：本轮 GPU 置空，CPU / 内存 / 外存照常
                log.exception("GPU 采集失败")
                gpus = []
            snap = {
                "ts": time.time(),
                "cpu": sysinfo.cpu_metrics(),
                "memory": sysinfo.memory_metrics(),
                "disks": sysinfo.disk_metrics(self._project_root),
                "gpus": gpus,
            }
            self._latest = snap
            self._history.append(_to_point(snap))
            return snap

    def snapshot(self) -> dict:
        """最近一次采样；超过一个采样周期则重新采样"""
        latest = self._latest
        if latest is not None and time.time() - latest["ts"] < self.interval:
            return latest
        return self.sample()

    def init_message(self) -> dict:
        """新连接的首条消息：主机信息 + 最新快照 + 历史曲线"""
        return {
            "type": "init",
            "interval": self.interval,
            "gpu_provider": self._gpu.kind,
            "driver_version": self._gpu.driver_version,
            "host": sysinfo.host_info(),
            "latest": self._latest,
            "history": list(self._history),
        }

    # ── 订阅 / 广播 ──

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.add(q)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        while self._subscribers:
            try:
                snap = await asyncio.to_thread(self.sample)
            except Exception:  # 单次采样失败不能让循环退出，否则所有已连接页面从此收不到数据
                log.exception("设备采样失败")
                await asyncio.sleep(self.interval)
                continue
            # 不取 _history[-1]：REST / 调度器可能在另一线程中刚追加了别的采样
            msg = {"type": "snapshot", "data": snap, "point": _to_point(snap)}
            for q in list(self._subscribers):
                if q.full():  # 慢消费者：丢最旧的一条，保证拿到的是最新状态
                    q.get_nowait()
                q.put_nowait(msg)
            await asyncio.sleep(self.interval)

    async def close(self) -> None:
        self._subscribers.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._gpu.close()


def _to_point(snap: dict) -> dict:
    """曲线用的精简数据点，避免历史缓冲携带每核 / 每盘的完整明细"""
    return {
        "ts": snap["ts"],
        "cpu": snap["cpu"]["usage"],
        "mem": snap["memory"]["usage"],
        "gpus": [
            {
                "util": g["utilization"],
                "mem": round(g["memory_used"] / g["memory_total"] * 100, 1) if g["memory_total"] else 0,
            }
            for g in snap["gpus"]
        ],
    }


_monitor: DeviceMonitor | None = None


def get_device_monitor() -> DeviceMonitor:
    global _monitor
    if _monitor is None:
        _monitor = DeviceMonitor()
    return _monitor


async def close_device_monitor() -> None:
    global _monitor
    if _monitor is not None:
        await _monitor.close()
        _monitor = None

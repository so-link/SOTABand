"""Agent 生命周期 × 设备调度：真实启动 echo-agent 子进程，检查其环境变量与预留的释放"""

import asyncio
import os
import signal

import psutil
import pytest

from core.agent import factory as factory_mod
from core.agent.factory import AgentFactory
from core.scheduler.allocator import ResourceAllocator
from core.scheduler.requirements import ResourceRequirements as Req

GB = 1024 ** 3
AGENT = "echo-agent"


class FakeMonitor:
    def __init__(self):
        self.gpus = [
            {"index": i, "name": f"GPU{i}", "memory_free": int(f * GB), "processes": []}
            for i, f in enumerate((8, 20))
        ]

    def snapshot(self):
        return {"gpus": self.gpus, "memory": {"available": 64 * GB}}


@pytest.fixture
def env(monkeypatch):
    alloc = ResourceAllocator(FakeMonitor(), cpu_count=8)
    monkeypatch.setattr(factory_mod, "get_allocator", lambda: alloc)
    req = {"value": Req(gpu="optional", min_vram_gb=4)}
    monkeypatch.setattr(factory_mod, "resolve_agent_requirements", lambda agent_id: req["value"])
    return AgentFactory(), alloc, req


def _child_env(pid: int) -> dict:
    return psutil.Process(pid).environ()


async def _wait_released(alloc, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while alloc.allocations() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
    return alloc.allocations()


async def test_agent_process_runs_on_scheduled_gpu_until_stopped(env):
    factory, alloc, _ = env
    started = await factory.start(AGENT)
    try:
        assert started["status"] == "started" and started["device"] == "gpu:1"
        [a] = alloc.allocations()
        assert a["owner"] == f"agent:{AGENT}" and a["vram_bytes"] == 4 * GB
        child = _child_env(a["pids"][0])
        assert child["CUDA_VISIBLE_DEVICES"] == "1" and child["SOTABAND_DEVICE"] == "gpu:1"
        assert factory.device_of(AGENT) == "gpu:1"
    finally:
        await factory.stop(AGENT)
    assert alloc.allocations() == []  # stop 返回时预留已归还
    assert factory.device_of(AGENT) is None


async def test_crashed_agent_releases_reservation(env):
    factory, alloc, _ = env
    await factory.start(AGENT)
    [a] = alloc.allocations()
    os.kill(a["pids"][0], signal.SIGKILL)  # 模拟崩溃 / 被外部杀掉
    assert await _wait_released(alloc) == []
    assert not factory.is_running(AGENT)


async def test_unschedulable_agent_is_not_started(env):
    factory, alloc, req = env
    req["value"] = Req(gpu="required", min_vram_gb=64)
    result = await factory.start(AGENT)
    assert result["status"] == "failed"
    assert "没有满足条件的 GPU" in result["error"]
    assert not factory.is_running(AGENT) and alloc.allocations() == []


async def test_restart_keeps_manually_chosen_device(env):
    factory, alloc, _ = env
    assert (await factory.start(AGENT, device="gpu:0"))["device"] == "gpu:0"
    try:
        restarted = await factory.restart(AGENT)
        assert restarted["device"] == "gpu:0"
        assert [a["device"] for a in alloc.allocations()] == ["gpu:0"]
    finally:
        await factory.stop(AGENT)


async def test_cancel_during_allocation_does_not_leak(env, monkeypatch):
    import time

    factory, alloc, _ = env
    real = alloc.allocate

    def slow_allocate(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(alloc, "allocate", slow_allocate)
    task = asyncio.create_task(factory.start(AGENT))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    assert alloc.allocations() == []
    assert not factory.is_running(AGENT)

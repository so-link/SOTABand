"""工具执行 × 设备调度：真实子进程中拿到的设备环境变量、失败返回、预留释放"""

import asyncio
import time

import pytest

from core.executor import tool_executor
from core.executor.tool_executor import ToolExecutor
from core.scheduler import requirements as rq
from core.scheduler.allocator import ResourceAllocator

GB = 1024 ** 3

# 工具代码：回显调度器注入的环境变量。import torch 放在不会被调用的函数里，
# 只影响静态推断（判为 gpu: optional），运行时不需要真的装 torch。
ECHO_ENV = """
import os

def _never_called():
    import torch

def execute(**kwargs):
    return {"status": "success", "data": {
        "cvd": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "device": os.environ.get("SOTABAND_DEVICE"),
        "omp": os.environ.get("OMP_NUM_THREADS"),
    }}
"""
ECHO_ENV_CPU_ONLY = ECHO_ENV.replace("    import torch", "    pass")


class FakeMonitor:
    def __init__(self, gpus_free_gb):
        self.gpus = [
            {"index": i, "name": f"GPU{i}", "memory_free": int(f * GB), "processes": []}
            for i, f in enumerate(gpus_free_gb)
        ]

    def snapshot(self):
        return {"gpus": self.gpus, "memory": {"available": 64 * GB}}


@pytest.fixture
def allocator(monkeypatch, tmp_path):
    alloc = ResourceAllocator(FakeMonitor((8, 20)), cpu_count=8)
    monkeypatch.setattr(tool_executor, "get_allocator", lambda: alloc)
    (tmp_path / "definitions").mkdir()
    monkeypatch.setattr(rq, "TOOLS_DIR", tmp_path)
    return alloc


async def test_inferred_optional_tool_gets_most_free_gpu(allocator):
    result = await ToolExecutor.execute("sched-probe", {}, code=ECHO_ENV)
    assert result["data"] == {"cvd": "1", "device": "gpu:1", "omp": None}
    assert allocator.allocations() == []  # 执行结束即释放


async def test_cpu_tool_cannot_see_gpus(allocator):
    result = await ToolExecutor.execute("sched-probe", {}, code=ECHO_ENV_CPU_ONLY)
    assert result["data"]["cvd"] == "" and result["data"]["device"] == "cpu"


async def test_manual_device_override(allocator):
    result = await ToolExecutor.execute("sched-probe", {}, code=ECHO_ENV, device="gpu:0")
    assert result["data"]["device"] == "gpu:0"


async def test_declared_requirements_drive_scheduling(allocator, tmp_path):
    (tmp_path / "definitions" / "sched-probe.md").write_text(
        "---\nid: sched-probe\nresources:\n  gpu: required\n  min_vram_gb: 100\n---\n", encoding="utf-8")
    result = await ToolExecutor.execute("sched-probe", {}, code=ECHO_ENV)
    assert result["status"] == "failed"
    assert result["error"] == "AllocationError"
    assert "GPU 1 剩余 20.0 GB（差 80.0 GB）" in result["message"]


async def test_invalid_declaration_is_reported(allocator, tmp_path):
    (tmp_path / "definitions" / "sched-probe.md").write_text(
        "---\nresources:\n  gpu: sometimes\n---\n", encoding="utf-8")
    result = await ToolExecutor.execute("sched-probe", {}, code=ECHO_ENV)
    assert result["error"] == "RequirementsError"


async def test_cpu_cores_limit_threads(allocator, tmp_path):
    (tmp_path / "definitions" / "sched-probe.md").write_text(
        "---\nresources:\n  cpu_cores: 2\n---\n", encoding="utf-8")
    result = await ToolExecutor.execute("sched-probe", {}, code=ECHO_ENV_CPU_ONLY)
    assert result["data"]["omp"] == "2"


def test_sync_path_is_scheduled_too(allocator):
    result = ToolExecutor.execute_sync("sched-probe", {}, code=ECHO_ENV)
    assert result["data"]["device"] == "gpu:1"
    assert allocator.allocations() == []


async def test_reservation_released_when_tool_crashes(allocator):
    result = await ToolExecutor.execute("sched-probe", {}, code="import torch\ndef execute(**k):\n    raise RuntimeError('boom')\n")
    assert result["status"] == "failed"
    assert allocator.allocations() == []


async def test_cancel_during_allocation_does_not_leak(allocator, monkeypatch):
    # 分配在线程里进行时调用方被取消（用户停止任务）：线程完成后预留必须归还
    real = allocator.allocate

    def slow_allocate(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(allocator, "allocate", slow_allocate)
    task = asyncio.create_task(ToolExecutor.execute("sched-probe", {}, code=ECHO_ENV))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    assert allocator.allocations() == []


@pytest.mark.parametrize("sync", [False, True])
async def test_scheduling_preserves_execution_identity(allocator, monkeypatch, sync):
    monkeypatch.setenv("SOTABAND_AGENT_HTTP_TOKEN", "agent-only-secret")
    code = '''
import os

def execute(**kwargs):
    return {"status": "success", "data": {
        "owner": os.environ.get("SOTABAND_USER_ID"),
        "token": os.environ.get("SOTABAND_AGENT_HTTP_TOKEN"),
        "device": os.environ.get("SOTABAND_DEVICE"),
    }}
'''
    kwargs = dict(code=code, owner_id="test-owner", device="cpu")
    if sync:
        result = await asyncio.to_thread(ToolExecutor.execute_sync, "sched-probe", {}, **kwargs)
    else:
        result = await ToolExecutor.execute("sched-probe", {}, **kwargs)
    assert result["data"] == {"owner": "test-owner", "token": None, "device": "cpu"}
    assert allocator.allocations() == []

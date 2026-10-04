"""设备监控器：历史环形缓冲、快照缓存、按订阅启停的共享采样循环"""

import asyncio

import pytest

from core.scheduler.gpu import MockGpuProvider
from core.scheduler.monitor import DeviceMonitor


@pytest.fixture
def monitor(tmp_path):
    return DeviceMonitor(MockGpuProvider("2x24", seed=0), project_root=tmp_path,
                         interval=0.05, history_seconds=0.5)


def test_sample_records_compact_history(monitor):
    for _ in range(25):
        monitor.sample()
    history = monitor.init_message()["history"]
    assert len(history) == 10  # history_seconds / interval
    point = history[-1]
    assert set(point) == {"ts", "cpu", "mem", "gpus"}
    assert len(point["gpus"]) == 2 and set(point["gpus"][0]) == {"util", "mem"}


def test_snapshot_reuses_fresh_sample(monitor):
    first = monitor.snapshot()
    assert monitor.snapshot() is first
    monitor._latest["ts"] -= monitor.interval
    assert monitor.snapshot() is not first


async def test_loop_runs_only_while_subscribed(monitor):
    assert not monitor.running
    q = monitor.subscribe()
    msg = await asyncio.wait_for(q.get(), timeout=2)
    assert msg["type"] == "snapshot"
    assert msg["point"]["ts"] == msg["data"]["ts"]
    assert len(msg["data"]["gpus"]) == 2

    monitor.unsubscribe(q)
    await asyncio.sleep(monitor.interval * 3)
    assert not monitor.running


async def test_broadcasts_to_every_subscriber(monitor):
    a, b = monitor.subscribe(), monitor.subscribe()
    ma = await asyncio.wait_for(a.get(), timeout=2)
    mb = await asyncio.wait_for(b.get(), timeout=2)
    assert ma["data"]["ts"] == mb["data"]["ts"]  # 一次采样，共享给所有订阅者
    await monitor.close()


async def test_slow_consumer_keeps_latest(monitor):
    q = monitor.subscribe()
    await asyncio.sleep(monitor.interval * 15)  # 不消费，让队列溢出
    assert q.qsize() <= q.maxsize
    msgs = [q.get_nowait() for _ in range(q.qsize())]
    assert msgs[-1]["data"]["ts"] == monitor._latest["ts"]
    await monitor.close()
    assert not monitor.running


def test_init_message_carries_host_and_provider(monitor):
    monitor.sample()
    init = monitor.init_message()
    assert init["type"] == "init"
    assert init["gpu_provider"] == "mock"
    assert init["host"]["cpu_cores"] > 0
    assert init["latest"]["gpus"][0]["name"] == "Mock GPU 24GB"


async def test_loop_survives_a_failed_sample(monitor):
    # 显卡掉线 / 驱动异常时某次采样抛错：循环不能就此停止，已连接的页面要继续收到数据
    real = monitor._gpu.list_gpus
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("GPU has fallen off the bus")
        return real()

    monitor._gpu.list_gpus = flaky
    q = monitor.subscribe()
    got = [await asyncio.wait_for(q.get(), timeout=2) for _ in range(3)]
    assert [m["type"] for m in got] == ["snapshot"] * 3
    assert monitor.running
    await monitor.close()


async def test_loop_survives_non_gpu_failure(monitor, monkeypatch):
    from core.scheduler import sysinfo
    real = sysinfo.disk_metrics
    calls = {"n": 0}

    def flaky(root):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("stale NFS handle")
        return real(root)

    monkeypatch.setattr(sysinfo, "disk_metrics", flaky)
    q = monitor.subscribe()
    got = [await asyncio.wait_for(q.get(), timeout=2) for _ in range(2)]
    assert all(m["type"] == "snapshot" for m in got)  # 失败的那一轮跳过，之后继续推送
    assert monitor.running
    await monitor.close()

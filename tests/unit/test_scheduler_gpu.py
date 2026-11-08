"""GPU 采集：Mock 规格解析、Mock 数据边界、Provider 选择、NVML 字段映射"""

import sys
from types import SimpleNamespace

import pytest
from pynvml import NVMLError

from core.scheduler import gpu
from core.scheduler.gpu import (
    MockGpuProvider,
    NullGpuProvider,
    NvmlGpuProvider,
    get_gpu_provider,
    parse_mock_spec,
)

GB = 1024 ** 3


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("24", [24]),
        ("24,16", [24, 16]),
        ("2x24", [24, 24]),
        (" 2X24 , 1x80 ", [24, 24, 80]),
        ("3*8.5", [8.5, 8.5, 8.5]),
        ("", []),
    ],
)
def test_parse_mock_spec(spec, expected):
    assert parse_mock_spec(spec) == expected


@pytest.mark.parametrize("spec", ["abc", "2x", "x24", "24GB"])
def test_parse_mock_spec_rejects_garbage(spec):
    with pytest.raises(ValueError):
        parse_mock_spec(spec)


def test_mock_provider_values_stay_in_bounds():
    p = MockGpuProvider("2x24,1x80", seed=0)
    for _ in range(500):
        gpus = p.list_gpus()
        assert [g["index"] for g in gpus] == [0, 1, 2]
        for g in gpus:
            assert 0 <= g["utilization"] <= 100
            assert 0 < g["memory_used"] < g["memory_total"]
            assert g["memory_used"] + g["memory_free"] == g["memory_total"]
    assert gpus[2]["memory_total"] == 80 * GB


def test_provider_selects_mock_when_env_set(monkeypatch):
    monkeypatch.setenv(gpu.MOCK_ENV, "2x24")
    p = get_gpu_provider()
    assert p.kind == "mock"
    assert len(p.list_gpus()) == 2


def test_provider_falls_back_to_none_without_driver(monkeypatch):
    monkeypatch.delenv(gpu.MOCK_ENV, raising=False)

    def no_driver():
        raise NVMLError(12)  # NVML_ERROR_LIBRARY_NOT_FOUND

    monkeypatch.setattr(gpu, "NvmlGpuProvider", no_driver)
    p = get_gpu_provider()
    assert isinstance(p, NullGpuProvider)
    assert p.list_gpus() == []


class _FakeNvml:
    """最小化的 pynvml 替身：一块卡，功耗查询不受支持"""

    NVML_TEMPERATURE_GPU = 0
    NVMLError = NVMLError

    def __init__(self):
        self.shutdown_called = False

    def nvmlInit(self): pass
    def nvmlShutdown(self): self.shutdown_called = True
    def nvmlSystemGetDriverVersion(self): return b"550.54"
    def nvmlDeviceGetCount(self): return 1
    def nvmlDeviceGetHandleByIndex(self, i): return f"h{i}"
    def nvmlDeviceGetUUID(self, h): return "GPU-abc"
    def nvmlDeviceGetName(self, h): return b"NVIDIA RTX 4090"
    def nvmlDeviceGetMemoryInfo(self, h): return SimpleNamespace(total=24 * GB, used=6 * GB, free=18 * GB)
    def nvmlDeviceGetUtilizationRates(self, h): return SimpleNamespace(gpu=42, memory=17)
    def nvmlDeviceGetTemperature(self, h, kind): return 61
    def nvmlDeviceGetPowerUsage(self, h): raise NVMLError(3)  # NVML_ERROR_NOT_SUPPORTED
    def nvmlDeviceGetEnforcedPowerLimit(self, h): return 450_000
    def nvmlDeviceGetMinorNumber(self, h): return 1
    def nvmlDeviceGetComputeRunningProcesses(self, h):
        return [SimpleNamespace(pid=999_999_999, usedGpuMemory=2 * GB)]


def test_nvml_provider_maps_fields(monkeypatch):
    fake = _FakeNvml()
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    seen = {}

    def fake_map(nvml_pids, minors):
        seen.update(nvml_pids=nvml_pids, minors=minors)
        return {999_999_999: 4321}

    monkeypatch.setattr(gpu, "map_host_pids", fake_map)
    p = NvmlGpuProvider()
    assert p.driver_version == "550.54"

    [g] = p.list_gpus()
    assert g["name"] == "NVIDIA RTX 4090"
    assert g["utilization"] == 42
    assert g["memory_used"] == 6 * GB
    assert g["temperature"] == 61
    assert g["power_draw_w"] is None        # 不支持的单项查询不影响整卡
    assert g["power_limit_w"] == 450.0      # mW → W
    assert seen == {"nvml_pids": {0: {999_999_999}}, "minors": {0: 1}}
    [proc] = g["processes"]
    assert (proc["pid"], proc["host_pid"], proc["used_memory"]) == (4321, 999_999_999, 2 * GB)

    p.close()
    assert fake.shutdown_called


# ── 容器内 PID 映射（数据取自 AutoDL 容器实测：GPU0=/dev/nvidia1，GPU1=/dev/nvidia7）──

def _fake_proc(root, procs):
    """procs: pid -> (启动时刻, [映射的设备])"""
    for pid, (start, devices) in procs.items():
        d = root / str(pid)
        d.mkdir()
        lines = ["7f00 r-xp 00000000 00:2f 99 /usr/lib/libc.so.6"]
        lines += [f"7f{i:02x} rw-s 00000000 00:05 {i} {dev}" for i, dev in enumerate(devices)]
        (d / "maps").write_text("\n".join(lines) + "\n")
        fields = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 10  # 第 22 列为启动时刻
        (d / "stat").write_text(f"{pid} (python) " + " ".join(fields))
    (root / "self").mkdir()
    return root


CTX = ["/dev/nvidiactl", "/dev/nvidia-uvm"]
AUTODL_PROCS = {
    2814: (100, []),                                                # uvicorn：只用 NVML
    3913: (500, []),                                                # 只用 NVML
    3914: (500, CTX + ["/dev/nvidia7"]),                            # CUDA_VISIBLE_DEVICES=1
    3915: (500, CTX + ["/dev/nvidia1"]),                            # CUDA_VISIBLE_DEVICES=0
    3916: (500, CTX + ["/dev/nvidia1", "/dev/nvidia7"]),            # 两块卡都用
    3917: (500, []),                                                # import torch 但没有上下文
}
AUTODL_NVML = {0: {166267, 166266}, 1: {166265, 166267}}
MINORS = {0: 1, 1: 7}


def test_map_host_pids_in_container(tmp_path):
    proc = _fake_proc(tmp_path, AUTODL_PROCS)
    assert gpu.map_host_pids(AUTODL_NVML, MINORS, proc) == {166265: 3914, 166266: 3915, 166267: 3916}


def test_map_host_pids_identity_on_bare_metal(tmp_path):
    proc = _fake_proc(tmp_path, {41: (1, CTX + ["/dev/nvidia0"]), 42: (2, CTX + ["/dev/nvidia1"])})
    assert gpu.map_host_pids({0: {41}, 1: {42}}, {0: 0, 1: 1}, proc) == {41: 41, 42: 42}


def test_map_host_pids_orders_by_start_time_across_pid_wraparound(tmp_path):
    # 本机 pid 编号回绕：99998 先创建、5 后创建；宿主机编号未回绕
    proc = _fake_proc(tmp_path, {99998: (100, CTX + ["/dev/nvidia1"]), 5: (200, CTX + ["/dev/nvidia1"])})
    assert gpu.map_host_pids({0: {700, 701}}, {0: 1}, proc) == {700: 99998, 701: 5}


@pytest.mark.parametrize(
    "procs, nvml, why",
    [
        # 本机少看到一个持有上下文的进程（已退出 / 无权限读取 maps）
        ({3914: (1, CTX + ["/dev/nvidia7"])}, {1: {10, 11}}, "数量不一致"),
        # 数量对得上但按顺序配对后与各卡集合矛盾（例如正好在采样间隙建立了上下文）
        ({3914: (1, CTX + ["/dev/nvidia1"]), 3915: (2, CTX + ["/dev/nvidia7"])},
         {0: {11}, 1: {10}}, "逐卡核对失败"),
    ],
)
def test_map_host_pids_gives_up_when_inconsistent(tmp_path, procs, nvml, why):
    proc = _fake_proc(tmp_path, procs)
    assert gpu.map_host_pids(nvml, MINORS, proc) == {}, why


def test_map_host_pids_without_processes_or_minor(tmp_path):
    proc = _fake_proc(tmp_path, AUTODL_PROCS)
    assert gpu.map_host_pids({0: set(), 1: set()}, MINORS, proc) == {}
    assert gpu.map_host_pids(AUTODL_NVML, {0: None, 1: 7}, proc) == {}


def test_identity_wins_on_bare_metal_even_after_pid_wraparound(tmp_path):
    # 物理机上 pid 回绕且两进程在同一块卡：按创建顺序配对会把 5 和 99998 对调，
    # 而逐卡集合核对无法发现（同一张卡上集合相同），必须先走「进程号本来就一致」的直通路径
    proc = _fake_proc(tmp_path, {99998: (100, CTX + ["/dev/nvidia0"]), 5: (200, CTX + ["/dev/nvidia0"])})
    assert gpu.map_host_pids({0: {5, 99998}}, {0: 0}, proc) == {5: 5, 99998: 99998}

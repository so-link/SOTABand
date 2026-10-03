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
    def nvmlDeviceGetComputeRunningProcesses(self, h):
        return [SimpleNamespace(pid=999_999_999, usedGpuMemory=2 * GB)]


def test_nvml_provider_maps_fields(monkeypatch):
    fake = _FakeNvml()
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    p = NvmlGpuProvider()
    assert p.driver_version == "550.54"

    [g] = p.list_gpus()
    assert g["name"] == "NVIDIA RTX 4090"
    assert g["utilization"] == 42
    assert g["memory_used"] == 6 * GB
    assert g["temperature"] == 61
    assert g["power_draw_w"] is None        # 不支持的单项查询不影响整卡
    assert g["power_limit_w"] == 450.0      # mW → W
    assert g["processes"] == [{"pid": 999_999_999, "name": "", "used_memory": 2 * GB}]

    p.close()
    assert fake.shutdown_called

"""GPU 采集 — DeviceProvider 抽象 + NVIDIA / Mock / 无 GPU 三种实现

选择规则（get_gpu_provider）：
1. 设置了 SOTABAND_MOCK_GPUS → MockGpuProvider（开发机无 GPU 时调试 / 演示用）
2. 能加载 NVML（有 NVIDIA 驱动）→ NvmlGpuProvider
3. 否则 → NullGpuProvider（GPU 列表为空，不报错）

SOTABAND_MOCK_GPUS 格式：逗号分隔的显存大小（GB），可用 NxS 表示 N 块同规格卡，
如 "24,16"、"2x24"、"2x24,1x80"。

每块卡统一输出为 dict（字段见 GpuProvider.list_gpus），显存单位为字节，
与 sysinfo 的内存 / 外存口径一致。
"""

from __future__ import annotations

import os
import random
import re

import psutil
from pynvml import NVMLError

MOCK_ENV = "SOTABAND_MOCK_GPUS"
_GB = 1024 ** 3


class GpuProvider:
    """GPU 采集接口。kind 用于前端标注数据来源（nvml / mock / none）。"""

    kind = "none"
    driver_version = ""

    def list_gpus(self) -> list[dict]:
        """返回每块卡的状态：
        index, uuid, name, utilization(%), memory_utilization(%),
        memory_total / memory_used / memory_free (bytes),
        temperature(°C|None), power_draw_w / power_limit_w (W|None),
        processes: [{pid, name, used_memory(bytes|None)}]
        """
        return []

    def close(self) -> None:
        pass


class NullGpuProvider(GpuProvider):
    kind = "none"


class NvmlGpuProvider(GpuProvider):
    kind = "nvml"

    def __init__(self):
        import pynvml  # nvidia-ml-py；无驱动时 nvmlInit 抛 NVMLError_LibraryNotFound

        self._nvml = pynvml
        pynvml.nvmlInit()
        self.driver_version = _text(pynvml.nvmlSystemGetDriverVersion())

    def list_gpus(self) -> list[dict]:
        nv = self._nvml
        gpus = []
        for i in range(nv.nvmlDeviceGetCount()):
            h = nv.nvmlDeviceGetHandleByIndex(i)
            mem = nv.nvmlDeviceGetMemoryInfo(h)
            util = self._query(nv.nvmlDeviceGetUtilizationRates, h)
            power = self._query(nv.nvmlDeviceGetPowerUsage, h)
            limit = self._query(nv.nvmlDeviceGetEnforcedPowerLimit, h)
            gpus.append({
                "index": i,
                "uuid": _text(nv.nvmlDeviceGetUUID(h)),
                "name": _text(nv.nvmlDeviceGetName(h)),
                "utilization": util.gpu if util else None,
                "memory_utilization": util.memory if util else None,
                "memory_total": mem.total,
                "memory_used": mem.used,
                "memory_free": mem.free,
                "temperature": self._query(nv.nvmlDeviceGetTemperature, h, nv.NVML_TEMPERATURE_GPU),
                "power_draw_w": round(power / 1000, 1) if power is not None else None,
                "power_limit_w": round(limit / 1000, 1) if limit is not None else None,
                "processes": self._processes(h),
            })
        return gpus

    def _processes(self, handle) -> list[dict]:
        procs = self._query(self._nvml.nvmlDeviceGetComputeRunningProcesses, handle) or []
        out = []
        for p in procs:
            try:
                name = psutil.Process(p.pid).name()
            except (psutil.Error, OSError):
                name = ""
            out.append({"pid": p.pid, "name": name, "used_memory": p.usedGpuMemory})
        return out

    def _query(self, fn, *args):
        """单项查询在部分卡 / 驱动 / WSL 上不支持，失败返回 None 而不是整卡丢失"""
        try:
            return fn(*args)
        except self._nvml.NVMLError:
            return None

    def close(self) -> None:
        self._query(self._nvml.nvmlShutdown)


class MockGpuProvider(GpuProvider):
    """模拟 GPU：利用率与显存占用做有界随机游走，便于观察界面刷新"""

    kind = "mock"
    driver_version = "mock"

    def __init__(self, spec: str, seed: int | None = None):
        self._sizes_gb = parse_mock_spec(spec)
        self._rng = random.Random(seed)
        self._util = [self._rng.uniform(0, 30) for _ in self._sizes_gb]
        self._used_frac = [self._rng.uniform(0.05, 0.3) for _ in self._sizes_gb]

    def list_gpus(self) -> list[dict]:
        gpus = []
        for i, size_gb in enumerate(self._sizes_gb):
            self._util[i] = _walk(self._rng, self._util[i], 8, 0, 100)
            self._used_frac[i] = _walk(self._rng, self._used_frac[i], 0.03, 0.02, 0.95)
            total = int(size_gb * _GB)
            used = int(total * self._used_frac[i])
            gpus.append({
                "index": i,
                "uuid": f"MOCK-GPU-{i}",
                "name": f"Mock GPU {size_gb:g}GB",
                "utilization": round(self._util[i]),
                "memory_utilization": round(self._used_frac[i] * 100),
                "memory_total": total,
                "memory_used": used,
                "memory_free": total - used,
                "temperature": round(35 + self._util[i] * 0.45),
                "power_draw_w": round(50 + self._util[i] * 2.5, 1),
                "power_limit_w": 300.0,
                "processes": [],
            })
        return gpus


def parse_mock_spec(spec: str) -> list[float]:
    """"2x24,1x80" → [24, 24, 80]；非法片段抛 ValueError，避免静默生成错误的卡数"""
    sizes: list[float] = []
    for part in filter(None, (s.strip() for s in spec.split(","))):
        m = re.fullmatch(r"(?:(\d+)\s*[xX*]\s*)?(\d+(?:\.\d+)?)", part)
        if not m:
            raise ValueError(f"{MOCK_ENV} 格式错误: {part!r}（示例: 2x24,1x80）")
        sizes += [float(m.group(2))] * int(m.group(1) or 1)
    return sizes


def get_gpu_provider() -> GpuProvider:
    spec = os.environ.get(MOCK_ENV, "").strip()
    if spec:
        return MockGpuProvider(spec)
    try:
        return NvmlGpuProvider()
    except (ImportError, OSError, NVMLError):  # 未安装驱动 / 非 NVIDIA 机器 / macOS
        return NullGpuProvider()


def _walk(rng: random.Random, value: float, step: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value + rng.uniform(-step, step)))


def _text(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)

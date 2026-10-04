"""资源分配器 — 为工具 / Agent 匹配最佳设备并记账预留

匹配规则（todo §12.2）：
- gpu: required → 选「有效剩余显存」最多且满足 min_vram_gb 的卡；没有则报错（说明哪块卡差多少）
- gpu: optional → 同上，但没有合适的卡时退回 CPU
- gpu: none     → 不分配 GPU，并对子进程隐藏所有显卡
- 内存 / CPU 核数只做准入检查（单机只有一份，无需「选」）
- device 参数可手动指定：cpu / gpu / gpu:N（cuda:N、N 同义）

有效剩余 = 实时剩余 − 各任务「尚未兑现」的预留。任务进程实际占用的显存 / 内存已经
体现在 NVML / psutil 的实时值里，只扣「预留量 − 已占用量」，避免同一份占用被算两次；
刚启动、还没来得及申请显存的任务则按预留量全额扣除，防止两个任务挤到同一块卡上。

预留只记在内存里（不写注册表），进程重启即清空——此时也不会有存活的子任务。
"""

from __future__ import annotations

import itertools
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import psutil

from core.scheduler.monitor import DeviceMonitor, get_device_monitor
from core.scheduler.requirements import ResourceRequirements

GB = 1024 ** 3

# 数值计算库读取的线程数变量：cpu_cores 的软限制
THREAD_ENV_VARS = (
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
)


class AllocationError(RuntimeError):
    """没有满足需求的设备"""


@dataclass
class Allocation:
    id: int
    owner: str                 # "tool:<id>" / "agent:<id>"
    gpu_index: int | None      # None = 运行在 CPU 上
    gpu_name: str
    vram_bytes: int
    ram_bytes: int
    cpu_cores: int
    requirements: ResourceRequirements
    started_at: float = field(default_factory=time.time)
    pids: set[int] = field(default_factory=set)

    @property
    def device(self) -> str:
        return "cpu" if self.gpu_index is None else f"gpu:{self.gpu_index}"

    def attach_pid(self, pid: int) -> None:
        """登记子进程，使其实际占用可从预留中抵扣"""
        self.pids.add(pid)

    def env(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """子进程环境变量：base（通常为 os.environ）叠加设备分配"""
        env = dict(base or {})
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # 与 NVML 编号一致
        env["CUDA_VISIBLE_DEVICES"] = "" if self.gpu_index is None else str(self.gpu_index)
        env["SOTABAND_DEVICE"] = self.device
        if self.cpu_cores:
            for var in THREAD_ENV_VARS:
                env[var] = str(self.cpu_cores)
        return env

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "owner": self.owner,
            "device": self.device,
            "gpu_index": self.gpu_index,
            "gpu_name": self.gpu_name,
            "vram_bytes": self.vram_bytes,
            "ram_bytes": self.ram_bytes,
            "cpu_cores": self.cpu_cores,
            "requirements": self.requirements.as_dict(),
            "started_at": self.started_at,
            "pids": sorted(self.pids),
        }


def parse_device(device: str | None) -> tuple[str, int | None] | None:
    """手动指定的设备 → ("cpu", None) / ("gpu", None 表示任意卡) / ("gpu", N)；未指定返回 None"""
    if device is None or not str(device).strip():
        return None
    d = str(device).strip().lower()
    if d == "cpu":
        return ("cpu", None)
    if d in ("gpu", "cuda"):
        return ("gpu", None)
    m = re.fullmatch(r"(?:(?:gpu|cuda):)?(\d+)", d)
    if not m:
        raise AllocationError(f"无法识别的设备 {device!r}（可用：cpu / gpu / gpu:N）")
    return ("gpu", int(m.group(1)))


def _proc_tree_rss(pids: set[int]) -> int:
    total = 0
    for pid in pids:
        try:
            p = psutil.Process(pid)
            for q in [p, *p.children(recursive=True)]:
                total += q.memory_info().rss
        except psutil.Error:
            continue
    return total


class ResourceAllocator:
    def __init__(
        self,
        monitor: DeviceMonitor,
        cpu_count: int | None = None,
        rss_probe: Callable[[set[int]], int] = _proc_tree_rss,
    ):
        self._monitor = monitor
        self._cpu_count = cpu_count or psutil.cpu_count(logical=True) or 1
        self._rss = rss_probe
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._active: dict[int, Allocation] = {}

    # ── 对外接口 ──

    def allocate(self, owner: str, req: ResourceRequirements, device: str | None = None) -> Allocation:
        """匹配设备并记录预留；失败抛 AllocationError。必须与 release() 成对调用。"""
        forced = parse_device(device)
        snap = self._monitor.snapshot()
        with self._lock:
            gpu = self._choose_gpu(owner, req, forced, snap["gpus"])
            self._check_ram(req, snap["memory"]["available"])
            self._check_cpu(req)
            alloc = Allocation(
                id=next(self._ids),
                owner=owner,
                gpu_index=gpu["index"] if gpu else None,
                gpu_name=gpu["name"] if gpu else "",
                vram_bytes=int(req.min_vram_gb * GB) if gpu else 0,
                ram_bytes=int(req.min_ram_gb * GB),
                cpu_cores=req.cpu_cores,
                requirements=req,
            )
            self._active[alloc.id] = alloc
            return alloc

    def release(self, alloc: Allocation) -> None:
        with self._lock:
            self._active.pop(alloc.id, None)

    def allocations(self) -> list[dict]:
        with self._lock:
            return [a.as_dict() for a in self._active.values()]

    # ── 匹配 ──

    def _choose_gpu(self, owner, req, forced, gpus: list[dict]) -> dict | None:
        what = _subject(owner)
        if forced is not None and forced[0] == "cpu":
            if req.gpu == "required":
                raise AllocationError(f"{what}需要 GPU，不能指定在 CPU 上运行")
            return None
        if req.gpu == "none" and forced is None:
            return None

        need = int(req.min_vram_gb * GB)
        if forced is not None and forced[1] is not None:
            idx = forced[1]
            gpu = next((g for g in gpus if g["index"] == idx), None)
            if gpu is None:
                raise AllocationError(f"指定的 GPU {idx} 不存在（本机共 {len(gpus)} 块 GPU）")
            free = self._effective_free(gpu)
            if free < need:
                raise AllocationError(
                    f"指定的 GPU {idx} 显存不足：{what}需要 {_gb(need)}，{self._describe_free(gpu, free)}"
                )
            return gpu

        must = req.gpu == "required" or forced is not None  # 指定 device=gpu 即视为必须
        if not gpus:
            if must:
                raise AllocationError(f"{what}需要 GPU，但本机未检测到 GPU")
            return None

        ranked = sorted(((self._effective_free(g), g) for g in gpus), key=lambda t: -t[0])
        best_free, best = ranked[0]
        if best_free >= need:
            return best
        if not must:
            return None  # optional：退回 CPU
        detail = "；".join(
            f"GPU {g['index']} {self._describe_free(g, f)}（差 {_gb(need - max(f, 0))}）"
            for f, g in sorted(ranked, key=lambda t: t[1]["index"])
        )
        raise AllocationError(f"{what}需要 {_gb(need)} 显存，没有满足条件的 GPU：{detail}")

    def _effective_free(self, gpu: dict) -> int:
        used_by_pid = {p["pid"]: p["used_memory"] or 0 for p in gpu.get("processes", [])}
        outstanding = sum(
            max(0, a.vram_bytes - sum(used_by_pid.get(pid, 0) for pid in a.pids))
            for a in self._active.values()
            if a.gpu_index == gpu["index"]
        )
        return gpu["memory_free"] - outstanding

    def _describe_free(self, gpu: dict, free: int) -> str:
        """「剩余 X GB」；有效剩余被运行中任务的预留压低时注明，避免误以为卡本身已满"""
        held = gpu["memory_free"] - free
        note = f"，已扣除运行中任务预留 {_gb(held)}" if held > 0 else ""
        return f"剩余 {_gb(max(free, 0))}{note}"

    def _check_ram(self, req: ResourceRequirements, available: int) -> None:
        need = int(req.min_ram_gb * GB)
        if not need:
            return
        outstanding = sum(max(0, a.ram_bytes - self._rss(a.pids)) for a in self._active.values() if a.ram_bytes)
        free = available - outstanding
        if free < need:
            held = f"（已扣除其他任务预留 {_gb(outstanding)}）" if outstanding else ""
            raise AllocationError(f"内存不足：需要 {_gb(need)}，可用 {_gb(max(free, 0))}{held}")

    def _check_cpu(self, req: ResourceRequirements) -> None:
        if not req.cpu_cores:
            return
        reserved = sum(a.cpu_cores for a in self._active.values())
        if reserved + req.cpu_cores > self._cpu_count:
            raise AllocationError(
                f"CPU 核数不足：需要 {req.cpu_cores} 核，已预留 {reserved}/{self._cpu_count} 核"
            )


def _subject(owner: str) -> str:
    kind, _, name = owner.partition(":")
    return {"tool": f"工具 {name} ", "agent": f"Agent {name} "}.get(kind, f"{owner} ")


def _gb(n: int) -> str:
    return f"{n / GB:.1f} GB"


_allocator: ResourceAllocator | None = None
_allocator_lock = threading.Lock()


def get_allocator() -> ResourceAllocator:
    global _allocator
    with _allocator_lock:
        if _allocator is None:
            monitor = get_device_monitor()
            _allocator = ResourceAllocator(monitor)
            monitor.attach_allocations(_allocator.allocations)
        return _allocator

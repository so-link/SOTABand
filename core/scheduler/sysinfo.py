"""主机指标采集 — CPU / 内存 / 外存

字段口径参照 clonitor-agent 的 pkg/sysinfo（gopsutil），这里用 psutil 实现。
GPU 由 gpu.py 的 GpuProvider 负责，不在这里。

所有函数都是同步阻塞调用（psutil 读 /proc 等），异步上下文中应放进线程执行。
"""

from __future__ import annotations

import os
import platform
import socket
import time
from pathlib import Path

import psutil

# 只展示真实文件系统；tmpfs / overlay / proc 等虚拟文件系统一律不显示。
# 9p / drvfs 是 WSL 挂载的 Windows 盘符（/mnt/c 等）。
REAL_FSTYPES = {
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs",
    "ntfs", "ntfs3", "fuseblk", "vfat", "exfat", "apfs", "hfs", "refs",
    "9p", "drvfs",
}

# 系统目录下的挂载（WSL 驱动目录、WSLg、引导分区等）对用户没有意义
IGNORED_MOUNT_PREFIXES = (
    "/usr/", "/mnt/wslg", "/boot", "/proc", "/sys", "/dev", "/run", "/snap", "/var/lib/docker",
)


def host_info() -> dict:
    """稳定的主机信息，进程生命周期内基本不变，采一次即可"""
    boot = psutil.boot_time()
    return {
        "hostname": socket.gethostname(),
        "ip": _first_non_loopback_ip(),
        "os": platform.system(),
        "distro": _distro_name(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_cores": psutil.cpu_count(logical=True) or 0,
        "cpu_physical_cores": psutil.cpu_count(logical=False) or 0,
        "mem_total": psutil.virtual_memory().total,
        "boot_time": boot,
        "uptime_seconds": int(time.time() - boot),
    }


def cpu_metrics() -> dict:
    """CPU 使用率（自上次调用以来的平均值，首次调用前应先 prime_cpu()）"""
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    usage = sum(per_core) / len(per_core) if per_core else 0.0
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):  # Windows 无 loadavg
        load1 = load5 = load15 = 0.0
    return {
        "usage": round(usage, 1),
        "per_core": [round(v, 1) for v in per_core],
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
    }


def prime_cpu() -> None:
    """psutil.cpu_percent(interval=None) 首次调用恒为 0，先调一次建立基线"""
    psutil.cpu_percent(interval=None, percpu=True)


def memory_metrics() -> dict:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total": vm.total,
        "used": vm.used,
        "available": vm.available,
        "free": vm.free,
        "cached": getattr(vm, "cached", 0),
        "buffers": getattr(vm, "buffers", 0),
        "shared": getattr(vm, "shared", 0),
        "usage": round(vm.percent, 1),
        "swap_total": swap.total,
        "swap_used": swap.used,
    }


def disk_metrics(project_root: Path | str) -> list[dict]:
    """真实文件系统的用量；同一设备只保留最短挂载点；项目所在分区排第一"""
    by_device = {}  # device -> psutil 分区记录
    for p in psutil.disk_partitions(all=True):
        if p.fstype.lower() not in REAL_FSTYPES:
            continue
        if p.mountpoint != "/" and p.mountpoint.startswith(IGNORED_MOUNT_PREFIXES):
            continue
        kept = by_device.get(p.device)
        if kept is None or len(p.mountpoint) < len(kept.mountpoint):
            by_device[p.device] = p

    project_mount = _containing_mount(str(project_root), [p.mountpoint for p in by_device.values()])
    disks = []
    for p in by_device.values():
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:  # 光驱 / 断开的网络盘
            continue
        disks.append({
            "device": p.device,
            "mount_point": p.mountpoint,
            "fs_type": p.fstype,
            "total": u.total,
            "used": u.used,
            "free": u.free,
            "usage": round(u.percent, 1),
            "is_project": p.mountpoint == project_mount,
        })
    disks.sort(key=lambda d: (not d["is_project"], d["mount_point"]))
    return disks


def _containing_mount(path: str, mounts: list[str]) -> str | None:
    """path 所在的挂载点 = 作为 path 前缀的最长挂载点"""
    path = os.path.realpath(path)
    best = None
    for m in mounts:
        prefix = m if m.endswith(os.sep) else m + os.sep
        if (path == m or path.startswith(prefix)) and (best is None or len(m) > len(best)):
            best = m
    return best


def _first_non_loopback_ip() -> str:
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith("127."):
                return a.address
    return ""


def _distro_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()

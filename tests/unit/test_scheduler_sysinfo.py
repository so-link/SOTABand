"""主机指标：外存过滤规则（WSL 实际挂载表）与指标结构"""

from types import SimpleNamespace

import pytest

from core.scheduler import sysinfo


def _part(device, mountpoint, fstype):
    return SimpleNamespace(device=device, mountpoint=mountpoint, fstype=fstype)


# 取自 WSL2 上 psutil.disk_partitions(all=True) 的真实输出（节选）
WSL_PARTITIONS = [
    _part("", "/usr/lib/modules/6.6.87.2-microsoft-standard-WSL2", "overlay"),
    _part("drivers", "/usr/lib/wsl/drivers", "9p"),
    _part("/dev/sdd", "/", "ext4"),
    _part("/dev/sdd", "/mnt/wslg/distro", "ext4"),
    _part("/dev/sdd", "/home/kk/bind-data", "ext4"),  # 同设备的 bind mount
    _part("none", "/run/user/1000", "tmpfs"),
    _part("/dev/sdd", "/init", "rootfs"),
    _part("C:\\", "/mnt/c", "9p"),
    _part("D:\\", "/mnt/d", "9p"),
]


@pytest.fixture
def fake_disks(monkeypatch):
    def usage(mountpoint):
        if mountpoint == "/mnt/d":
            raise OSError("device not ready")
        return SimpleNamespace(total=100, used=40, free=60, percent=40.0)

    monkeypatch.setattr(sysinfo.psutil, "disk_partitions", lambda all: WSL_PARTITIONS)
    monkeypatch.setattr(sysinfo.psutil, "disk_usage", usage)


def test_disks_keep_only_real_filesystems(fake_disks):
    mounts = [d["mount_point"] for d in sysinfo.disk_metrics("/home/kk/SOTABand")]
    # overlay / tmpfs / rootfs、WSL 驱动目录被过滤；同设备只保留最短挂载点；
    # /mnt/d 读取失败被跳过而不是整体报错
    assert mounts == ["/", "/mnt/c"]


def test_project_partition_comes_first(fake_disks, monkeypatch):
    monkeypatch.setattr(sysinfo.psutil, "disk_usage",
                        lambda m: SimpleNamespace(total=1, used=0, free=1, percent=0.0))
    disks = sysinfo.disk_metrics("/mnt/d/work/SOTABand")
    assert [d["mount_point"] for d in disks] == ["/mnt/d", "/", "/mnt/c"]
    assert [d["is_project"] for d in disks] == [True, False, False]


def test_containing_mount_respects_path_boundaries():
    mounts = ["/", "/mnt/c", "/mnt/cdrom"]
    assert sysinfo._containing_mount("/mnt/cdrom/x", mounts) == "/mnt/cdrom"
    assert sysinfo._containing_mount("/mnt/c/x", mounts) == "/mnt/c"
    assert sysinfo._containing_mount("/mnt/cx", mounts) == "/"


def test_cpu_and_memory_metrics_shape():
    sysinfo.prime_cpu()
    cpu = sysinfo.cpu_metrics()
    assert 0 <= cpu["usage"] <= 100
    assert len(cpu["per_core"]) == sysinfo.host_info()["cpu_cores"]

    mem = sysinfo.memory_metrics()
    assert 0 < mem["used"] <= mem["total"]
    assert {"available", "cached", "swap_total", "swap_used"} <= mem.keys()

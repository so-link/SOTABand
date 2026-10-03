"""资源分配器：选卡策略、预留记账、准入检查、手动指定设备、并发安全"""

import threading

import pytest

from core.scheduler.allocator import AllocationError, ResourceAllocator, parse_device
from core.scheduler.requirements import ResourceRequirements as Req

GB = 1024 ** 3


class FakeMonitor:
    """只提供 allocator 需要的 snapshot() 字段"""

    def __init__(self, gpus_free_gb=(24, 16), available_ram_gb=32):
        self.gpus = [
            {"index": i, "name": f"GPU{i}", "memory_free": int(f * GB), "processes": []}
            for i, f in enumerate(gpus_free_gb)
        ]
        self.available = int(available_ram_gb * GB)

    def snapshot(self):
        return {"gpus": self.gpus, "memory": {"available": self.available}}


def make(gpus=(24, 16), ram=32, cpus=8, rss=lambda pids: 0, tree=None):
    mon = FakeMonitor(gpus, ram)
    # 默认不展开进程树：测试里的 pid 是虚构的，不能去读真实系统进程
    tree = tree or (lambda pids: set(pids))
    return ResourceAllocator(mon, cpu_count=cpus, rss_probe=rss, pid_tree=tree), mon


def test_none_runs_on_cpu_with_gpus_hidden():
    alloc, _ = make()
    a = alloc.allocate("tool:t", Req(gpu="none"))
    assert a.gpu_index is None
    env = a.env({"PATH": "/bin"})
    assert env["CUDA_VISIBLE_DEVICES"] == "" and env["SOTABAND_DEVICE"] == "cpu"
    assert env["PATH"] == "/bin"


def test_optional_picks_most_free_gpu():
    alloc, _ = make(gpus=(8, 20, 12))
    a = alloc.allocate("tool:t", Req(gpu="optional", min_vram_gb=4))
    assert a.gpu_index == 1
    assert a.env()["CUDA_VISIBLE_DEVICES"] == "1"
    assert a.env()["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"


@pytest.mark.parametrize("gpus", [(2, 3), ()])
def test_optional_falls_back_to_cpu(gpus):
    alloc, _ = make(gpus=gpus)
    assert alloc.allocate("tool:t", Req(gpu="optional", min_vram_gb=4)).gpu_index is None


def test_required_without_gpu_fails():
    alloc, _ = make(gpus=())
    with pytest.raises(AllocationError, match="本机未检测到 GPU"):
        alloc.allocate("tool:train", Req(gpu="required"))


def test_required_shortfall_message_lists_every_card():
    alloc, _ = make(gpus=(3, 5))
    with pytest.raises(AllocationError) as e:
        alloc.allocate("tool:train", Req(gpu="required", min_vram_gb=8))
    msg = str(e.value)
    assert "工具 train" in msg
    assert "GPU 0 剩余 3.0 GB（差 5.0 GB）" in msg
    assert "GPU 1 剩余 5.0 GB（差 3.0 GB）" in msg


def test_reservations_prevent_double_booking_until_released():
    alloc, _ = make(gpus=(24, 16))
    a = alloc.allocate("tool:a", Req(gpu="required", min_vram_gb=20))
    b = alloc.allocate("tool:b", Req(gpu="required", min_vram_gb=12))
    assert (a.gpu_index, b.gpu_index) == (0, 1)  # GPU 0 只剩 4 GB 有效显存
    with pytest.raises(AllocationError):
        alloc.allocate("tool:c", Req(gpu="required", min_vram_gb=12))

    alloc.release(a)
    assert alloc.allocate("tool:c", Req(gpu="required", min_vram_gb=12)).gpu_index == 0
    assert [x["owner"] for x in alloc.allocations()] == ["tool:b", "tool:c"]


def test_shortfall_message_explains_reservations_and_never_goes_negative():
    alloc, mon = make(gpus=(24,))
    alloc.allocate("tool:a", Req(gpu="required", min_vram_gb=20))
    with pytest.raises(AllocationError, match=r"GPU 0 剩余 4.0 GB，已扣除运行中任务预留 20.0 GB（差 2.0 GB）"):
        alloc.allocate("tool:b", Req(gpu="required", min_vram_gb=6))
    # 任务实际占用无法按 pid 抵扣（如容器内）时有效剩余为负，显示为 0
    mon.gpus[0]["memory_free"] = 6 * GB
    with pytest.raises(AllocationError, match=r"指定的 GPU 0 显存不足：工具 b 需要 2.0 GB，剩余 0.0 GB，已扣除"):
        alloc.allocate("tool:b", Req(gpu="required", min_vram_gb=2), device="gpu:0")


def test_actual_usage_is_not_counted_twice():
    alloc, mon = make(gpus=(24,))
    a = alloc.allocate("tool:a", Req(gpu="required", min_vram_gb=20))
    a.attach_pid(4242)
    # 进程已实际申请 18 GB：NVML 剩余变为 6 GB，预留中只剩 2 GB 未兑现
    mon.gpus[0]["memory_free"] = 6 * GB
    mon.gpus[0]["processes"] = [{"pid": 4242, "used_memory": 18 * GB}]
    assert alloc.allocate("tool:b", Req(gpu="required", min_vram_gb=4)).gpu_index == 0
    with pytest.raises(AllocationError):
        alloc.allocate("tool:c", Req(gpu="required", min_vram_gb=1))


def test_child_process_usage_is_credited_to_parent():
    # Agent（pid 100）调用的工具是它的子进程（pid 101），NVML 里显示的是子进程
    alloc, mon = make(gpus=(24,), tree=lambda pids: set(pids) | ({101} if 100 in pids else set()))
    a = alloc.allocate("agent:a", Req(gpu="required", min_vram_gb=20))
    a.attach_pid(100)
    mon.gpus[0]["memory_free"] = 6 * GB
    mon.gpus[0]["processes"] = [{"pid": 101, "used_memory": 18 * GB}]
    assert alloc.allocate("tool:b", Req(gpu="required", min_vram_gb=4)).gpu_index == 0


def test_unknown_process_usage_keeps_full_reservation():
    alloc, mon = make(gpus=(24,))
    a = alloc.allocate("tool:a", Req(gpu="required", min_vram_gb=20))
    a.attach_pid(4242)
    mon.gpus[0]["processes"] = [{"pid": 4242, "used_memory": None}]  # WSL 等环境拿不到进程显存
    with pytest.raises(AllocationError):
        alloc.allocate("tool:b", Req(gpu="required", min_vram_gb=8))


@pytest.mark.parametrize(
    "device, expected",
    [(None, None), ("", None), ("cpu", ("cpu", None)), ("GPU", ("gpu", None)),
     ("gpu:1", ("gpu", 1)), ("cuda:2", ("gpu", 2)), ("3", ("gpu", 3))],
)
def test_parse_device(device, expected):
    assert parse_device(device) == expected


def test_parse_device_rejects_garbage():
    with pytest.raises(AllocationError, match="无法识别的设备"):
        parse_device("tpu:0")


def test_forced_gpu_overrides_selection():
    alloc, _ = make(gpus=(24, 16))
    assert alloc.allocate("tool:t", Req(gpu="optional"), device="gpu:1").gpu_index == 1
    # 即使工具声明不用 GPU，用户显式要求 GPU 也照做
    assert alloc.allocate("tool:t", Req(gpu="none"), device="gpu").gpu_index == 0


@pytest.mark.parametrize(
    "req, device, fragment",
    [
        (Req(gpu="optional"), "gpu:5", "GPU 5 不存在"),
        (Req(gpu="required", min_vram_gb=20), "gpu:1", "GPU 1 显存不足"),
        (Req(gpu="required"), "cpu", "不能指定在 CPU 上运行"),
    ],
)
def test_forced_device_errors(req, device, fragment):
    alloc, _ = make(gpus=(24, 16))
    with pytest.raises(AllocationError, match=fragment):
        alloc.allocate("tool:t", req, device=device)


def test_forced_cpu_for_optional_tool():
    alloc, _ = make()
    assert alloc.allocate("tool:t", Req(gpu="optional"), device="cpu").gpu_index is None


def test_ram_admission_counts_unfulfilled_reservations():
    rss = {frozenset({7}): 1 * GB}
    alloc, _ = make(ram=10, rss=lambda pids: rss.get(frozenset(pids), 0))
    a = alloc.allocate("tool:a", Req(min_ram_gb=4))
    a.attach_pid(7)  # 已实际占用 1 GB → 预留中 3 GB 未兑现
    with pytest.raises(AllocationError, match=r"内存不足：需要 8.0 GB，可用 7.0 GB（已扣除其他任务预留 3.0 GB）"):
        alloc.allocate("tool:b", Req(min_ram_gb=8))
    assert alloc.allocate("tool:b", Req(min_ram_gb=7)).ram_bytes == 7 * GB


def test_cpu_core_admission_and_thread_env():
    alloc, _ = make(cpus=8)
    a = alloc.allocate("tool:a", Req(cpu_cores=6))
    assert a.env()["OMP_NUM_THREADS"] == "6"
    with pytest.raises(AllocationError, match="已预留 6/8 核"):
        alloc.allocate("tool:b", Req(cpu_cores=4))
    assert "OMP_NUM_THREADS" not in alloc.allocate("tool:c", Req()).env()


def test_concurrent_allocations_never_overbook():
    alloc, _ = make(gpus=(24,))
    granted, barrier = [], threading.Barrier(20)

    def worker(i):
        barrier.wait()
        try:
            granted.append(alloc.allocate(f"tool:{i}", Req(gpu="required", min_vram_gb=4)))
        except AllocationError:
            pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 6  # 24 GB / 4 GB

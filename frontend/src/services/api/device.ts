// 设备空间 — 后端 /api/devices 的数据结构（字段与 core/scheduler 一致，字节 / 百分比口径）

export interface HostInfo {
  hostname: string
  ip: string
  os: string
  distro: string
  kernel: string
  arch: string
  cpu_model: string
  cpu_cores: number
  cpu_physical_cores: number
  mem_total: number
  boot_time: number
  uptime_seconds: number
}

export interface CpuMetrics {
  usage: number
  per_core: number[]
  load1: number
  load5: number
  load15: number
}

export interface MemoryMetrics {
  total: number
  used: number
  available: number
  free: number
  cached: number
  buffers: number
  shared: number
  usage: number
  swap_total: number
  swap_used: number
}

export interface DiskMetrics {
  device: string
  mount_point: string
  fs_type: string
  total: number
  used: number
  free: number
  usage: number
  is_project: boolean
}

export interface GpuProcess {
  /** 本机（容器内）进程号；容器内无法与宿主机进程号对应时为 null */
  pid: number | null
  /** NVML 原始进程号（容器部署时为宿主机进程号） */
  host_pid: number
  name: string
  used_memory: number | null
}

export interface GpuMetrics {
  index: number
  uuid: string
  name: string
  utilization: number | null
  memory_utilization: number | null
  memory_total: number
  memory_used: number
  memory_free: number
  temperature: number | null
  power_draw_w: number | null
  power_limit_w: number | null
  processes: GpuProcess[]
}

/** 调度器的一条预留：正在运行的工具 / Agent 及其占用的设备（todo §12.2 / §12.3） */
export interface Allocation {
  id: number
  owner: string // "tool:<id>" / "agent:<id>"
  device: string // "cpu" / "gpu:N"
  gpu_index: number | null
  gpu_name: string
  vram_bytes: number
  ram_bytes: number
  cpu_cores: number
  requirements: {
    gpu: 'none' | 'optional' | 'required'
    min_vram_gb: number
    min_ram_gb: number
    cpu_cores: number
    source: 'declared' | 'inferred' | 'default'
    reason: string
  }
  started_at: number
  pids: number[]
}

export interface DeviceSnapshot {
  ts: number
  cpu: CpuMetrics
  memory: MemoryMetrics
  disks: DiskMetrics[]
  gpus: GpuMetrics[]
  allocations: Allocation[]
}

/** 曲线用的精简数据点（百分比） */
export interface HistoryPoint {
  ts: number
  cpu: number
  mem: number
  gpus: { util: number | null; mem: number }[]
}

/** nvml = 真实 NVIDIA 设备；mock = SOTABAND_MOCK_GPUS 模拟；none = 无 GPU */
export type GpuProviderKind = 'nvml' | 'mock' | 'none'

export type DeviceWsMessage =
  | {
      type: 'init'
      interval: number
      gpu_provider: GpuProviderKind
      driver_version: string
      host: HostInfo
      latest: DeviceSnapshot | null
      history: HistoryPoint[]
    }
  | { type: 'snapshot'; data: DeviceSnapshot; point: HistoryPoint }

export function devicesWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/api/devices/ws`
}

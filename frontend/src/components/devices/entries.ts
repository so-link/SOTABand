import type { DeviceKey } from '@/stores/device-store'
import type { DeviceSnapshot } from '@/services/api/device'

export interface DeviceEntry {
  key: DeviceKey
  label: string
  usage: number | null
}

/** 设备空间的条目顺序：CPU → 内存 → 各 GPU → 外存（外存取项目所在分区的用量） */
export function deviceEntries(snap: DeviceSnapshot | null): DeviceEntry[] {
  if (!snap) return []
  const project = snap.disks.find((d) => d.is_project) ?? snap.disks[0]
  return [
    { key: 'cpu', label: 'CPU', usage: snap.cpu.usage },
    { key: 'memory', label: '内存', usage: snap.memory.usage },
    ...snap.gpus.map((g): DeviceEntry => ({
      key: `gpu-${g.index}`,
      label: `GPU ${g.index} · ${g.name}`,
      usage: g.utilization,
    })),
    { key: 'disk', label: '外存', usage: project?.usage ?? null },
  ]
}


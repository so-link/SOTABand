import { Separator } from '@/components/ui/separator'
import { useDeviceStore, type DeviceKey } from '@/stores/device-store'
import type { DeviceSnapshot, GpuMetrics, HistoryPoint, HostInfo } from '@/services/api/device'
import { Meter, Sparkline, type SeriesPoint } from './charts'
import { formatBytes, formatPercent, formatUptime } from './format'

// 注意：index.css 的全局 `* { margin: 0; padding: 0 }` 不在 @layer 中，会覆盖 Tailwind 的
// p-* / m-* / space-y-* 等工具类。本目录组件统一用 flex gap 排间距、inline style 设内边距。
const BADGE_STYLE = { padding: '0 4px' }
const CELL_STYLE = { padding: '2px 0' }

/** 右栏：选中设备的详情（实时） */
export function DeviceDetail({ device }: { device: DeviceKey }) {
  const { latest, history, host, gpuProvider, driverVersion } = useDeviceStore()
  const gpuIndex = device.startsWith('gpu-') ? Number(device.slice(4)) : null
  const gpu = gpuIndex != null ? latest?.gpus.find((g) => g.index === gpuIndex) : undefined

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="border-b border-maia-border bg-maia-bg flex items-center gap-2" style={{ padding: '8px 12px' }}>
        <h3 className="text-xs font-semibold text-maia-text">设备详情</h3>
        {gpu && gpuProvider === 'mock' && (
          <span className="text-[9px] text-maia-warning border border-maia-warning/40 rounded" style={BADGE_STYLE}>MOCK</span>
        )}
      </div>
      <div className="flex flex-col gap-3 text-sm" style={{ padding: 12 }}>
        {!latest ? (
          <p className="text-xs text-maia-text-muted">正在获取设备状态…</p>
        ) : device === 'cpu' ? (
          <CpuDetail snap={latest} history={history} host={host} />
        ) : device === 'memory' ? (
          <MemoryDetail snap={latest} history={history} />
        ) : device === 'disk' ? (
          <DiskDetail snap={latest} />
        ) : gpu ? (
          <GpuDetail gpu={gpu} history={history} driverVersion={driverVersion} />
        ) : (
          <p className="text-xs text-maia-text-muted">该 GPU 已不可用</p>
        )}
      </div>
    </div>
  )
}

function CpuDetail({ snap, history, host }: { snap: DeviceSnapshot; history: HistoryPoint[]; host: HostInfo | null }) {
  const { cpu } = snap
  return (
    <>
      <Property label="型号" value={host?.cpu_model} />
      <Property label="核数" value={host ? `${host.cpu_cores} 逻辑核 / ${host.cpu_physical_cores} 物理核` : undefined} />
      <Usage label="使用率" value={cpu.usage} />
      <Sparkline label="CPU 使用率" points={series(history, (p) => p.cpu)} />
      <Property label="负载 (1 / 5 / 15 分钟)" value={`${cpu.load1} / ${cpu.load5} / ${cpu.load15}`} mono />
      <div className="flex flex-col gap-1">
        <Label>各核使用率</Label>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          {cpu.per_core.map((v, i) => (
            <div key={i} className="flex items-center gap-1.5" title={`核 ${i}: ${formatPercent(v)}`}>
              <span className="w-5 text-[10px] text-maia-text-muted tabular-nums">{i}</span>
              <Meter value={v} className="flex-1" />
              <span className="w-8 text-right text-[10px] text-maia-text-secondary tabular-nums">{formatPercent(v)}</span>
            </div>
          ))}
        </div>
      </div>
      {host && (
        <>
          <Separator />
          <Property label="主机名" value={host.hostname} />
          <Property label="地址" value={host.ip} mono />
          <Property label="系统" value={`${host.distro} · ${host.arch}`} />
          <Property label="内核" value={host.kernel} mono />
          <Property label="运行时长" value={formatUptime(Date.now() / 1000 - host.boot_time)} />
        </>
      )}
    </>
  )
}

function MemoryDetail({ snap, history }: { snap: DeviceSnapshot; history: HistoryPoint[] }) {
  const m = snap.memory
  return (
    <>
      <Usage label="使用率" value={m.usage} detail={`${formatBytes(m.used)} / ${formatBytes(m.total)}`} />
      <Sparkline label="内存使用率" points={series(history, (p) => p.mem)} />
      <div className="grid grid-cols-2 gap-3">
        <Property label="可用" value={formatBytes(m.available)} />
        <Property label="空闲" value={formatBytes(m.free)} />
        <Property label="缓存" value={formatBytes(m.cached)} />
        <Property label="缓冲" value={formatBytes(m.buffers)} />
      </div>
      <Separator />
      <Usage
        label="Swap"
        value={m.swap_total ? (m.swap_used / m.swap_total) * 100 : null}
        detail={`${formatBytes(m.swap_used)} / ${formatBytes(m.swap_total)}`}
      />
    </>
  )
}

function DiskDetail({ snap }: { snap: DeviceSnapshot }) {
  if (snap.disks.length === 0) return <p className="text-xs text-maia-text-muted">未检测到外存分区</p>
  return (
    <>
      {snap.disks.map((d) => (
        <div key={d.mount_point} className="flex flex-col gap-1">
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-xs text-maia-text-heading">{d.mount_point}</span>
            {d.is_project && (
              <span className="text-[9px] text-maia-accent border border-maia-accent-border rounded" style={BADGE_STYLE}>项目所在</span>
            )}
            <span className="flex-1 text-right text-[10px] text-maia-text-muted">{d.fs_type}</span>
          </div>
          <Meter value={d.usage} />
          <div className="flex justify-between text-[10px] text-maia-text-muted tabular-nums">
            <span>{formatPercent(d.usage)} · 剩余 {formatBytes(d.free)}</span>
            <span>{formatBytes(d.used)} / {formatBytes(d.total)}</span>
          </div>
        </div>
      ))}
    </>
  )
}

function GpuDetail({ gpu, history, driverVersion }: { gpu: GpuMetrics; history: HistoryPoint[]; driverVersion: string }) {
  const memPct = gpu.memory_total ? (gpu.memory_used / gpu.memory_total) * 100 : null
  return (
    <>
      <Property label="型号" value={gpu.name} />
      <Usage label="利用率" value={gpu.utilization} />
      <Sparkline label={`GPU ${gpu.index} 利用率`} points={series(history, (p) => p.gpus[gpu.index]?.util ?? null)} />
      <Usage label="显存" value={memPct} detail={`${formatBytes(gpu.memory_used)} / ${formatBytes(gpu.memory_total)}`} />
      <Sparkline label={`GPU ${gpu.index} 显存占用`} points={series(history, (p) => p.gpus[gpu.index]?.mem ?? null)} />
      <div className="grid grid-cols-2 gap-3">
        <Property label="温度" value={gpu.temperature != null ? `${gpu.temperature} °C` : undefined} />
        <Property
          label="功耗"
          value={gpu.power_draw_w != null
            ? `${gpu.power_draw_w} W${gpu.power_limit_w != null ? ` / ${gpu.power_limit_w} W` : ''}`
            : undefined}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label>占用进程</Label>
        {gpu.processes.length === 0 ? (
          <p className="text-xs text-maia-text-muted">无</p>
        ) : (
          <table className="w-full text-[11px]">
            <tbody>
              {gpu.processes.map((p) => (
                <tr key={p.pid} className="border-t border-maia-border">
                  <td className="font-mono text-maia-text-muted tabular-nums" style={CELL_STYLE}>{p.pid}</td>
                  <td className="truncate max-w-[8rem] text-maia-text-secondary" style={CELL_STYLE}>{p.name || '—'}</td>
                  <td className="text-right tabular-nums text-maia-text-secondary" style={CELL_STYLE}>{formatBytes(p.used_memory)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <Separator />
      <Property label="UUID" value={gpu.uuid} mono />
      <Property label="驱动版本" value={driverVersion} mono />
    </>
  )
}

function series(history: HistoryPoint[], pick: (p: HistoryPoint) => number | null): SeriesPoint[] {
  return history.map((p) => ({ ts: p.ts, v: pick(p) }))
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-[10px] text-maia-text-muted uppercase">{children}</span>
}

function Property({ label, value, mono }: { label: string; value?: string; mono?: boolean }) {
  return (
    <div>
      <Label>{label}</Label>
      <p className={mono ? 'font-mono text-xs text-maia-text-secondary' : 'text-sm text-maia-text-heading'}>
        {value || '—'}
      </p>
    </div>
  )
}

function Usage({ label, value, detail }: { label: string; value: number | null; detail?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <Label>{label}</Label>
        <span className="text-sm font-semibold text-maia-text-heading">{formatPercent(value)}</span>
      </div>
      <Meter value={value} />
      {detail && <p className="text-[10px] text-maia-text-muted text-right tabular-nums">{detail}</p>}
    </div>
  )
}

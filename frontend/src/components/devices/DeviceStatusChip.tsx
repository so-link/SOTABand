import { useEffect } from 'react'
import { Activity } from 'lucide-react'
import { useDeviceStore } from '@/stores/device-store'
import { useUIStore } from '@/stores/ui-store'
import { cn } from '@/lib/utils'
import { formatPercent } from './format'

/** 顶栏状态：连接状态 + CPU / 内存 / GPU 概览，点击打开设备详情 */
export function DeviceStatusChip() {
  const { latest, connection, gpuProvider, subscribe, selectDevice } = useDeviceStore()

  useEffect(() => subscribe(), [subscribe])

  const online = connection === 'open' && latest != null
  const gpus = latest?.gpus ?? []
  const gpuUtil = gpus.length
    ? gpus.reduce((sum, g) => sum + (g.utilization ?? 0), 0) / gpus.length
    : null

  const openDetail = () => {
    const ui = useUIStore.getState()
    ui.setLeftPanelTab('resources')
    if (!ui.rightPanelOpen) ui.toggleRightPanel()
    selectDevice(gpus.length ? `gpu-${gpus[0].index}` : 'cpu')
  }

  return (
    <button
      type="button"
      onClick={openDetail}
      title="查看设备空间"
      className="flex items-center gap-1.5 px-2.5 py-0.5 rounded border border-maia-border-glow bg-maia-accent-light/30 hover:bg-maia-accent-light/60 transition-colors"
    >
      <Activity className={cn('h-3 w-3', online ? 'text-maia-success' : 'text-maia-danger')} />
      <span className="text-[10px] text-maia-text-muted tracking-wider font-mono tabular-nums">
        {online ? (
          <>
            SYS.ONLINE · CPU {formatPercent(latest.cpu.usage)} · MEM {formatPercent(latest.memory.usage)} ·{' '}
            {gpus.length ? `GPU×${gpus.length} ${formatPercent(gpuUtil)}` : 'GPU:0'}
            {gpuProvider === 'mock' && <span className="text-maia-warning"> MOCK</span>}
          </>
        ) : latest != null || connection === 'closed' ? (
          // 曾经连上过：重连期间状态在 connecting / closed 间切换，对用户统一显示离线
          'SYS.OFFLINE · 重连中'
        ) : (
          'SYS.CONNECTING'
        )}
      </span>
    </button>
  )
}

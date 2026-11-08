import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Server } from 'lucide-react'
import { useDeviceStore, type DeviceKey } from '@/stores/device-store'
import { useUIStore } from '@/stores/ui-store'
import { cn } from '@/lib/utils'
import { formatPercent } from './format'
import { deviceEntries } from './entries'

/** 资源空间中的「设备空间」分区，样式与数据 / 工具 / Agent 空间保持一致 */
export function DeviceSection() {
  const { latest, gpuProvider, connection, selectedDevice, subscribe, selectDevice } = useDeviceStore()
  const [expanded, setExpanded] = useState(true)

  useEffect(() => subscribe(), [subscribe])

  const entries = deviceEntries(latest)
  const open = (key: DeviceKey) => {
    selectDevice(key)
    const ui = useUIStore.getState()
    if (!ui.rightPanelOpen) ui.toggleRightPanel()
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setExpanded((v) => !v)
          }
        }}
        className="flex items-center gap-1.5 w-full py-1 px-2 rounded hover:bg-maia-sidebar-hover transition-colors text-[11px] font-medium tracking-wider uppercase cursor-pointer select-none"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-maia-text-muted" />
        ) : (
          <ChevronRight className="h-3 w-3 text-maia-text-muted" />
        )}
        <Server className="h-3.5 w-3.5 text-cyan-500" />
        <span className="flex-1 text-left text-maia-text-secondary">设备空间</span>
        {gpuProvider === 'mock' && (
          <span className="text-[9px] text-maia-warning border border-maia-warning/40 rounded tracking-wider"
            style={{ padding: '0 4px' }} title="GPU 数据由 SOTABAND_MOCK_GPUS 模拟">
            MOCK
          </span>
        )}
        <span className="text-[10px] text-maia-text-muted bg-maia-bg rounded-full px-1.5 py-0.5 tracking-tight">
          {entries.length}
        </span>
      </div>

      {expanded && (
        <div>
          {entries.map((e) => (
            <div
              key={e.key}
              role="button"
              tabIndex={0}
              onClick={() => open(e.key)}
              onKeyDown={(ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') {
                  ev.preventDefault()
                  open(e.key)
                }
              }}
              style={{ paddingLeft: '52px' }}
              className={cn(
                'flex items-center gap-1.5 w-full py-[3px] pr-1.5 rounded text-[11px] tracking-wide',
                'hover:bg-maia-sidebar-hover transition-colors text-left text-maia-text-secondary cursor-pointer',
                selectedDevice === e.key && 'bg-maia-accent-light text-maia-accent font-medium',
              )}
            >
              <span className="truncate flex-1">{e.label}</span>
              <span className="text-[10px] text-maia-text-muted tabular-nums shrink-0">{formatPercent(e.usage)}</span>
            </div>
          ))}
          {gpuProvider === 'none' && (
            <p className="text-[10px] text-maia-text-muted py-[3px] tracking-wide" style={{ paddingLeft: '52px' }}>
              未检测到 GPU
            </p>
          )}
          {entries.length === 0 && (
            <p className="text-[10px] text-maia-text-muted py-1 tracking-wide" style={{ paddingLeft: '52px' }}>
              {connection === 'closed' ? '设备监控连接已断开，正在重连…' : '正在连接…'}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

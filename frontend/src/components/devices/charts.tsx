import { useState } from 'react'
import { cn } from '@/lib/utils'
import { formatClock, formatPercent, severity } from './format'

// 填充色表达严重程度，底槽是同色系的浅色，状态在整条上都可读
const METER_FILL = {
  normal: 'bg-maia-accent',
  warning: 'bg-maia-warning',
  critical: 'bg-maia-danger',
} as const
const METER_TRACK = {
  normal: 'bg-maia-accent/15',
  warning: 'bg-maia-warning/15',
  critical: 'bg-maia-danger/15',
} as const

/** 单个比率对上限的 meter；数值始终以文字给出，不靠颜色单独表达状态 */
export function Meter({ value, className }: { value: number | null; className?: string }) {
  const level = severity(value)
  const pct = Math.max(0, Math.min(100, value ?? 0))
  return (
    <div
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value ?? undefined}
      className={cn('h-1.5 w-full rounded-full overflow-hidden', METER_TRACK[level], className)}
    >
      <div className={cn('h-full rounded-full transition-[width] duration-500', METER_FILL[level])}
        style={{ width: `${pct}%` }} />
    </div>
  )
}

export interface SeriesPoint {
  ts: number
  v: number | null
}

const H = 40
const WINDOW_SECONDS = 600

/**
 * 最近 10 分钟的单序列曲线（百分比，y 轴固定 0–100）。
 * 相邻点间隔超过 gapSeconds 时断开（无人订阅时后端暂停采样，曲线如实留空）。
 * 悬停：竖向十字线吸附最近的点，提示框给出时间与数值。
 */
export function Sparkline({ points, label, gapSeconds = 6 }: {
  points: SeriesPoint[]
  label: string
  gapSeconds?: number
}) {
  const [hover, setHover] = useState<number | null>(null)
  if (points.length === 0) {
    return <div className="h-10 flex items-center text-[10px] text-maia-text-muted">等待数据…</div>
  }

  const end = points[points.length - 1].ts
  const start = end - WINDOW_SECONDS
  const x = (ts: number) => ((ts - start) / WINDOW_SECONDS) * 100
  const y = (v: number) => H - (Math.max(0, Math.min(100, v)) / 100) * (H - 2) - 1

  const segments: string[] = []
  let cur: string[] = []
  points.forEach((p, i) => {
    const gap = i > 0 && p.ts - points[i - 1].ts > gapSeconds
    if (p.v == null || gap) {
      if (cur.length > 1) segments.push(cur.join(' '))
      cur = []
    }
    if (p.v != null) cur.push(`${x(p.ts).toFixed(2)},${y(p.v).toFixed(2)}`)
  })
  if (cur.length > 1) segments.push(cur.join(' '))

  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const ts = start + ((e.clientX - rect.left) / rect.width) * WINDOW_SECONDS
    let best = 0
    points.forEach((p, i) => {
      if (Math.abs(p.ts - ts) < Math.abs(points[best].ts - ts)) best = i
    })
    setHover(best)
  }

  const hp = hover != null ? points[hover] : null
  const latest = points[points.length - 1]
  return (
    <div
      className="relative h-10 cursor-crosshair"
      onPointerMove={onMove}
      onPointerLeave={() => setHover(null)}
      aria-label={`${label}，最近 10 分钟，当前 ${formatPercent(latest.v)}`}
      role="img"
    >
      <svg viewBox={`0 0 100 ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
        <line x1="0" x2="100" y1={H - 0.5} y2={H - 0.5}
          className="stroke-maia-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {segments.map((pts, i) => (
          <polyline key={i} points={pts} fill="none" className="stroke-maia-accent"
            strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        ))}
        {hp && (
          <line x1={x(hp.ts)} x2={x(hp.ts)} y1="0" y2={H}
            className="stroke-maia-text-muted" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
      </svg>
      {hp && (
        <div
          className="pointer-events-none absolute -top-7 z-10 flex gap-1.5 whitespace-nowrap rounded border border-maia-border bg-maia-surface text-[10px] shadow"
          style={{ left: `${x(hp.ts)}%`, transform: `translateX(${x(hp.ts) > 70 ? '-100%' : '0'})`, padding: '2px 6px' }}
        >
          <span className="font-semibold text-maia-text-heading">{formatPercent(hp.v)}</span>
          <span className="text-maia-text-muted">{formatClock(hp.ts)}</span>
        </div>
      )}
    </div>
  )
}

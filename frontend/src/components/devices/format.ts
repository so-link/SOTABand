const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

/** 字节 → 人类可读（1024 进制，与后端 min_vram_gb 等 GB 口径一致） */
export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—'
  let v = bytes
  let i = 0
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024
    i += 1
  }
  return `${i === 0 ? v : v.toFixed(digits)} ${UNITS[i]}`
}

export function formatPercent(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v)}%`
}

export function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d} 天 ${h} 小时`
  if (h > 0) return `${h} 小时 ${m} 分`
  if (m > 0) return `${m} 分钟`
  return `${Math.max(0, Math.floor(seconds))} 秒`
}

export function formatClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

/** 使用率严重程度：meter 填充色与顶栏告警共用同一阈值 */
export type Severity = 'normal' | 'warning' | 'critical'

export function severity(percent: number | null | undefined): Severity {
  if (percent == null) return 'normal'
  if (percent >= 90) return 'critical'
  if (percent >= 70) return 'warning'
  return 'normal'
}

import { useEffect, useState } from 'react'
import { ShieldCheck, LogOut } from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'

/** 代管模式横幅：超管切到系统用户（uid=100）身份期间常驻显示，
 *  提供剩余时间与退出入口——代管态下用户管理页已不可见，这是主退路。 */
export function ImpersonationBanner() {
  const impersonating = useAuthStore((s) => s.impersonating)
  const expiresAt = useAuthStore((s) => s.impersonateExpiresAt)
  const exitImpersonate = useAuthStore((s) => s.exitImpersonate)
  const [busy, setBusy] = useState(false)
  const [remainMin, setRemainMin] = useState<number | null>(null)

  useEffect(() => {
    if (!impersonating || !expiresAt) { setRemainMin(null); return }
    const tick = () => setRemainMin(Math.max(0, Math.round((expiresAt - Date.now()) / 60000)))
    tick()
    const t = setInterval(tick, 15000)
    return () => clearInterval(t)
  }, [impersonating, expiresAt])

  if (!impersonating) return null

  return (
    <div className="flex items-center justify-center gap-2 px-3 py-1.5 bg-maia-warning/15 border-b border-maia-warning/40 text-maia-warning text-[12px]">
      <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
      <span>
        正在以<span className="font-semibold">系统用户（uid=100）</span>身份操作——写入将落在公共层
        {remainMin !== null && <span className="text-maia-text-muted"> · 约 {remainMin} 分钟后自动失效</span>}
      </span>
      <button
        disabled={busy}
        onClick={() => { setBusy(true); void exitImpersonate().finally(() => setBusy(false)) }}
        className="flex items-center gap-1 ml-1 px-2 py-0.5 rounded border border-maia-warning/50 hover:bg-maia-warning/20 transition-colors disabled:opacity-50"
      >
        <LogOut className="h-3 w-3" />退出代管
      </button>
    </div>
  )
}

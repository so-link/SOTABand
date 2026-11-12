import { useState, useRef, useEffect } from 'react'
import { Settings, Sun, Moon, LogIn, LogOut, User, Users, ChevronDown, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { useUIStore } from '@/stores/ui-store'
import { useAuthStore } from '@/stores/auth-store'
import { LoginDialog } from '@/components/topbar/LoginDialog'
import { APP_VERSION } from '@/lib/utils'

export function TopBar() {
  const { theme, toggleTheme, setActiveView } = useUIStore()
  const user = useAuthStore((s) => s.user)
  const openLogin = useAuthStore((s) => s.openLogin)

  return (
    <header className="flex h-9 items-center justify-between px-3 shrink-0 select-none glass border-b border-white/[0.04] relative">
      <div className="absolute bottom-0 left-0 right-0 gradient-line" />
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-2 ml-1">
          <img src="/image.png" alt="Logo" className="w-5 h-5 rounded object-contain"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none'
              const fallback = (e.target as HTMLElement).nextElementSibling as HTMLElement
              if (fallback) fallback.style.display = 'flex'
            }} />
          <div className="flex items-center justify-center w-5 h-5 rounded bg-maia-accent/10 border border-maia-accent-border" style={{ display: 'none' }}>
            <span className="h-3 w-3 text-maia-accent text-[12px] font-semibold flex items-center justify-center">S</span>
          </div>
          <span className="text-[16px] font-semibold text-maia-text-heading">SOTABand 优智联邦</span>
        </div>
      </div>

      <div className="flex items-center gap-0.5">
        {user ? (
          <UserMenu />
        ) : (
          <Tooltip content="登录（未登录以本机实例身份使用）">
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={openLogin}>
              <LogIn className="h-3.5 w-3.5" />
            </Button>
          </Tooltip>
        )}
        <Tooltip content={theme === 'dark' ? '浅色模式' : '深色模式'}>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={toggleTheme}>
            {theme === 'dark' ? <Sun className="h-3.5 w-3.5 text-amber-400" /> : <Moon className="h-3.5 w-3.5 text-maia-accent" />}
          </Button>
        </Tooltip>
        {user?.role === 'admin' && (
          <Tooltip content="用户管理（仅超级用户）">
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setActiveView('users')}>
              <Users className="h-3.5 w-3.5" />
            </Button>
          </Tooltip>
        )}
        {/* 设置是系统级配置（LLM/服务），仅管理员可见 */}
        {user?.role === 'admin' && (
          <Tooltip content="设置（系统级配置，仅管理员）">
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setActiveView('settings')}>
              <Settings className="h-3.5 w-3.5" />
            </Button>
          </Tooltip>
        )}
      </div>
      <LoginDialog />
    </header>
  )
}

/** 用户名下拉菜单：个人设置 / 用户管理 / 退出登录都收在这里 */
function UserMenu() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const exitImpersonate = useAuthStore((s) => s.exitImpersonate)
  const setActiveView = useUIStore((s) => s.setActiveView)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  const item = (
    icon: React.ReactNode,
    label: string,
    onClick: () => void,
    danger = false,
  ) => (
    <button
      onClick={() => { setOpen(false); onClick() }}
      className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-[12px] text-left transition-colors ${
        danger ? 'text-maia-danger hover:bg-maia-danger-bg' : 'text-maia-text hover:bg-maia-bg'
      }`}
    >
      {icon}
      {label}
    </button>
  )

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 px-1.5 h-7 rounded hover:bg-maia-sidebar-hover text-[12px] text-maia-text-secondary"
        title={user?.role === 'admin' ? '管理员' : '已登录'}
      >
        <User className="h-3.5 w-3.5 text-maia-accent" />
        <span className="max-w-[120px] truncate">{user?.displayName || user?.username}</span>
        <ChevronDown className="h-3 w-3 text-maia-text-muted" />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 w-44 rounded-lg border border-maia-border bg-maia-surface shadow-lg py-1">
          <div className="px-2.5 py-1.5 border-b border-maia-border mb-1">
            <div className="text-[12px] font-medium text-maia-text truncate">{user?.displayName || user?.username}</div>
            <div className="text-[11px] text-maia-text-muted">
              {user?.acting_as_system ? '系统身份代管中' : user?.role === 'admin' ? '超级用户' : '普通用户'}
            </div>
          </div>
          {user?.acting_as_system ? (
            item(<ShieldCheck className="h-3.5 w-3.5" />, '退出系统身份', () => void exitImpersonate())
          ) : (
            item(<User className="h-3.5 w-3.5" />, '个人设置', () => setActiveView('account'))
          )}
          <div className="my-1 border-t border-maia-border" />
          {item(<LogOut className="h-3.5 w-3.5" />, '退出登录', () => void logout(), true)}
        </div>
      )}
    </div>
  )
}

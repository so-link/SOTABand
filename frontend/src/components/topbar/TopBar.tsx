import { Settings, Sun, Moon, LogIn, LogOut, User } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { useUIStore } from '@/stores/ui-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { useAuthStore } from '@/stores/auth-store'
import { LoginDialog } from '@/components/topbar/LoginDialog'
import { APP_VERSION } from '@/lib/utils'

export function TopBar() {
  const { theme, toggleTheme, setActiveView } = useUIStore()
  const workspaceName = useFileTreeStore((s) => s.root?.name)
  const user = useAuthStore((s) => s.user)
  const openLogin = useAuthStore((s) => s.openLogin)
  const logout = useAuthStore((s) => s.logout)

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
          <span className="text-[13px] font-semibold text-maia-text-heading">SOTABand</span>
        </div>
        {workspaceName && workspaceName !== '工作区间' && (
          <>
            <span className="text-maia-text-muted mx-0.5 text-[13px] font-mono">/</span>
            <span className="text-[13px] text-maia-text-secondary font-mono">{workspaceName}</span>
          </>
        )}
        <Badge variant="accent" className="text-[12px] ml-1 font-mono">v{APP_VERSION}</Badge>
      </div>

      <div className="flex items-center gap-0.5">
        {user ? (
          <>
            <Tooltip content={user.role === 'admin' ? '管理员' : '已登录'}>
              <span className="flex items-center gap-1 px-1.5 h-7 text-[12px] text-maia-text-secondary">
                <User className="h-3.5 w-3.5 text-maia-accent" />
                {user.username}
              </span>
            </Tooltip>
            <Tooltip content="退出登录">
              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => void logout()}>
                <LogOut className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
          </>
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
        <Tooltip content="设置">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setActiveView('settings')}><Settings className="h-3.5 w-3.5" /></Button>
        </Tooltip>
      </div>
      <LoginDialog />
    </header>
  )
}

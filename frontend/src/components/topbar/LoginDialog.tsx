import { useState } from 'react'
import { createPortal } from 'react-dom'
import { X, LogIn } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

export function LoginDialog() {
  const { loginOpen, closeLogin, login, busy, error } = useAuthStore()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  if (!loginOpen) return null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy || !username || !password) return
    await login(username, password)
  }

  // 必须挂到 body：TopBar 的 .glass 带 backdrop-filter，会让 header 成为
  // fixed 定位的包含块，导致弹层被限制在顶栏内、遮罩和关闭按钮点不到
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={closeLogin}>
      <div className="w-[340px] bg-maia-surface rounded-lg border border-maia-border shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-maia-border">
          <span className="text-[13px] font-semibold text-maia-text-heading">登录</span>
          <button onClick={closeLogin} className="p-1 rounded hover:bg-maia-sidebar-hover">
            <X className="h-4 w-4 text-maia-text-muted" />
          </button>
        </div>
        <form onSubmit={submit} className="px-4 py-4 flex flex-col gap-3">
          <Input placeholder="用户名" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
          <Input placeholder="密码" type="password" value={password} autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)} />
          {error && <div className="text-[12px] text-red-400">{error}</div>}
          <div className="text-[12px] text-maia-text-muted leading-relaxed">
            未配置超级用户密码时本机可直接使用；配置后需登录。普通用户账号请联系超级用户创建。
          </div>
          <div className="flex items-center justify-end gap-2 pt-1">
            <Button type="submit" disabled={busy || !username || !password} className="gap-1.5">
              <LogIn className="h-3.5 w-3.5" />
              {busy ? '请稍候…' : '登录'}
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  )
}

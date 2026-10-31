import { useState } from 'react'
import { createPortal } from 'react-dom'
import { X, LogIn, UserPlus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

export function LoginDialog() {
  const { loginOpen, closeLogin, login, register, busy, error } = useAuthStore()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  if (!loginOpen) return null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy || !username || !password) return
    if (mode === 'login') await login(username, password)
    else await register(username, password)
  }

  // 必须挂到 body：TopBar 的 .glass 带 backdrop-filter，会让 header 成为
  // fixed 定位的包含块，导致弹层被限制在顶栏内、遮罩和关闭按钮点不到
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={closeLogin}>
      <div className="w-[340px] bg-maia-surface rounded-lg border border-maia-border shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-maia-border">
          <span className="text-[13px] font-semibold text-maia-text-heading">
            {mode === 'login' ? '登录' : '注册新用户'}
          </span>
          <button onClick={closeLogin} className="p-1 rounded hover:bg-maia-sidebar-hover">
            <X className="h-4 w-4 text-maia-text-muted" />
          </button>
        </div>
        <form onSubmit={submit} className="px-4 py-4 flex flex-col gap-3">
          <Input placeholder="用户名" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
          <Input placeholder="密码（至少 8 位）" type="password" value={password}
            onChange={(e) => setPassword(e.target.value)} />
          {error && <div className="text-[12px] text-red-400">{error}</div>}
          <div className="text-[12px] text-maia-text-muted leading-relaxed">
            {mode === 'register'
              ? '首个注册的用户成为管理员；实例已有用户时需管理员在用户设置中添加。'
              : '未登录时以本机实例身份使用；登录后可区分资源归属。'}
          </div>
          <div className="flex items-center justify-between gap-2 pt-1">
            <button type="button" className="text-[12px] text-maia-accent hover:underline"
              onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
              {mode === 'login' ? '注册新用户' : '返回登录'}
            </button>
            <Button type="submit" disabled={busy || !username || !password} className="gap-1.5">
              {mode === 'login' ? <LogIn className="h-3.5 w-3.5" /> : <UserPlus className="h-3.5 w-3.5" />}
              {busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录'}
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  )
}

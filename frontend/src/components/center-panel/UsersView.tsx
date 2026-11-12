import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  Users as UsersIcon, Plus, Loader2, AlertCircle, RefreshCw, ShieldCheck,
  Pencil, Tag, KeyRound, Trash2, X, CheckCircle2, UserCog,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { authApi, type ManagedUser } from '@/services/api/auth'
import { useAuthStore } from '@/stores/auth-store'

/** 把后端 UTC 时间（2026-11-04T03:00:00Z）转成本地可读格式。 */
function fmtCreatedAt(raw?: string): string {
  if (!raw) return '—'
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

type DialogKind = 'rename' | 'display' | 'password'

export function UsersView() {
  const me = useAuthStore((s) => s.user)
  const isAdmin = me?.role === 'admin'
  const impersonateSystem = useAuthStore((s) => s.impersonateSystem)

  const [users, setUsers] = useState<ManagedUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [switching, setSwitching] = useState(false)
  const [dialog, setDialog] = useState<{ kind: DialogKind; user: ManagedUser } | null>(null)
  const [confirmDel, setConfirmDel] = useState<ManagedUser | null>(null)

  /** 切换到系统身份（代管）：令牌换血后本视图会被超管守卫弹出，退出走顶栏横幅。 */
  const switchToSystem = async () => {
    setSwitching(true)
    setError('')
    try {
      await impersonateSystem()
    } catch (e) {
      setError(e instanceof Error ? e.message : '切换到系统身份失败')
      setSwitching(false)
    }
  }

  const reload = useCallback(async () => {
    try {
      const r = await authApi.listUsers()
      setUsers(r.users ?? [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载用户列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!isAdmin) { setLoading(false); return }
    void reload()
  }, [isAdmin, reload])

  if (!isAdmin) {
    return (
      <div className="h-full overflow-auto">
        <div className="max-w-2xl mx-auto px-6 py-16 text-center text-[13px] text-maia-text-muted">
          仅超级用户可管理用户。
        </div>
      </div>
    )
  }

  const afterMutate = async (msg: string) => {
    setNotice(msg)
    await reload()
  }

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-4xl mx-auto px-6 py-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-maia-accent/10 border border-maia-accent-border">
            <UsersIcon className="h-4.5 w-4.5 text-maia-accent" />
          </div>
          <div className="min-w-0">
            <h1 className="text-[15px] font-semibold text-maia-text-heading">用户管理</h1>
            <p className="text-[12px] text-maia-text-muted">创建账号、改名与重置密码（仅超级用户可见）</p>
          </div>
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={() => void reload()} className="gap-1.5 shrink-0">
            <RefreshCw className="h-3.5 w-3.5" />刷新
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)} className="gap-1.5 shrink-0">
            <Plus className="h-3.5 w-3.5" />新建用户
          </Button>
        </div>

        <Card className="mb-4">
          <CardBody className="flex items-start gap-2 text-[12px] text-maia-text-muted leading-relaxed">
            <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5 text-maia-accent" />
            <span>
              每个普通用户的数据存放在 <span className="font-mono">/root/SB-USER/&lt;用户ID&gt;/</span> 下
              （对话、私有资源、偏好、定时任务），彼此隔离，互相看不到。用户 ID 由系统从 1000 起分配且不可修改——
              它就是目录名，改 ID 等于搬走整个数据目录。
            </span>
          </CardBody>
        </Card>

        {error && (
          <div className="flex items-center gap-2 px-3 py-2.5 mb-3 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-[13px]">
            <AlertCircle className="h-4 w-4 shrink-0" />{error}
          </div>
        )}
        {notice && !error && (
          <div className="flex items-center gap-2 px-3 py-2.5 mb-3 rounded border border-maia-success/30 bg-maia-success/10 text-maia-success text-[13px]">
            <CheckCircle2 className="h-4 w-4 shrink-0" />{notice}
          </div>
        )}

        <Card>
          <CardBody className="p-0">
            {loading ? (
              <div className="flex items-center gap-2 px-4 py-8 text-[13px] text-maia-text-muted">
                <Loader2 className="h-4 w-4 animate-spin" />加载用户列表…
              </div>
            ) : (
              <table className="w-full text-[13px] border-collapse">
                <thead>
                  <tr className="text-left text-[12px] text-maia-text-muted border-b border-maia-border">
                    <th className="px-3 py-2 font-medium w-20">用户 ID</th>
                    <th className="px-3 py-2 font-medium">用户名</th>
                    <th className="px-3 py-2 font-medium">显示名</th>
                    <th className="px-3 py-2 font-medium w-24">角色</th>
                    <th className="px-3 py-2 font-medium w-36">创建时间</th>
                    <th className="px-3 py-2 font-medium w-40 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => {
                    const isSystem = u.system === true || u.id === '100'
                    const builtin = !isSystem && (u.builtin === true || u.id === '0')
                    return (
                      <tr key={u.id} className="border-b border-maia-border last:border-0">
                        <td className="px-3 py-2 font-mono text-maia-text">{u.id}</td>
                        <td className="px-3 py-2 text-maia-text">
                          <div className="flex items-center gap-1.5">
                            <span className="truncate">{u.username}</span>
                            {isSystem && <Badge variant="default" className="text-[11px] shrink-0">系统账号</Badge>}
                            {builtin && <Badge variant="warning" className="text-[11px] shrink-0">内置账号</Badge>}
                          </div>
                        </td>
                        <td className="px-3 py-2 text-maia-text-secondary">{u.display_name || '—'}</td>
                        <td className="px-3 py-2 text-maia-text-secondary">
                          {isSystem ? '系统用户' : u.role === 'admin' ? '超级用户' : '普通用户'}
                        </td>
                        <td className="px-3 py-2 text-maia-text-muted font-mono text-[12px]">{fmtCreatedAt(u.created_at)}</td>
                        <td className="px-3 py-2">
                          {isSystem ? (
                            <div className="flex items-center justify-end gap-0.5">
                              <IconAction title="修改显示名"
                                onClick={() => setDialog({ kind: 'display', user: u })}>
                                <Tag className="h-3.5 w-3.5" />
                              </IconAction>
                              <Button variant="outline" size="sm" className="gap-1.5 h-7 text-[12px]"
                                disabled={switching}
                                title="以系统用户身份编辑公共层资源（内置工具/数据集/模型/Agent），1 小时后自动失效"
                                onClick={() => void switchToSystem()}>
                                {switching
                                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  : <UserCog className="h-3.5 w-3.5" />}
                                切换身份
                              </Button>
                            </div>
                          ) : (
                          <div className="flex items-center justify-end gap-0.5">
                            <IconAction title="修改用户名"
                              onClick={() => setDialog({ kind: 'rename', user: u })}>
                              <Pencil className="h-3.5 w-3.5" />
                            </IconAction>
                            <IconAction title="修改显示名"
                              onClick={() => setDialog({ kind: 'display', user: u })}>
                              <Tag className="h-3.5 w-3.5" />
                            </IconAction>
                            <IconAction title={builtin ? '内置账号的密码在 .env 中配置，不可重置' : '重置密码'}
                              disabled={builtin} onClick={() => setDialog({ kind: 'password', user: u })}>
                              <KeyRound className="h-3.5 w-3.5" />
                            </IconAction>
                            <IconAction title={builtin ? '不能删除超级用户' : '删除账号'}
                              danger disabled={builtin} onClick={() => setConfirmDel(u)}>
                              <Trash2 className="h-3.5 w-3.5" />
                            </IconAction>
                          </div>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                  {users.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-3 py-8 text-center text-maia-text-muted">
                        暂无普通用户。点击右上角「新建用户」创建第一个账号。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>
      </div>

      {createOpen && (
        <CreateUserDialog
          onClose={() => setCreateOpen(false)}
          onCreated={async (uid, username) => {
            setCreateOpen(false)
            await afterMutate(`用户已创建：${username}（系统分配的用户 ID：${uid}）`)
          }}
        />
      )}
      {dialog && (
        <EditUserDialog
          kind={dialog.kind}
          user={dialog.user}
          onClose={() => setDialog(null)}
          onSaved={async (text) => { setDialog(null); await afterMutate(text) }}
        />
      )}
      {confirmDel && (
        <DeleteUserDialog
          user={confirmDel}
          onClose={() => setConfirmDel(null)}
          onDeleted={async (text) => { setConfirmDel(null); await afterMutate(text) }}
        />
      )}
    </div>
  )
}

function IconAction({ title, disabled, danger, onClick, children }: {
  title: string; disabled?: boolean; danger?: boolean
  onClick: () => void; children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        'flex items-center justify-center h-6 w-6 rounded transition-colors',
        'text-maia-text-muted hover:bg-maia-sidebar-hover',
        danger ? 'hover:text-maia-danger' : 'hover:text-maia-accent',
        disabled && 'opacity-30 pointer-events-none',
      )}
    >
      {children}
    </button>
  )
}

/** 弹窗通用外壳：必须 portal 到 body（CenterPanel 的父容器带 backdrop-filter，
 *  会把 fixed 定位困在内部，导致遮罩与关闭按钮点不到）。 */
function ModalShell({ title, onClose, children, width }: {
  title: string; onClose: () => void; children: React.ReactNode; width: string
}) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onMouseDown={onClose}>
      <div onMouseDown={(e) => e.stopPropagation()}
        className={cn('w-full rounded-lg bg-maia-surface border border-maia-border shadow-xl', width)}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-maia-border">
          <span className="text-[13px] font-semibold text-maia-text-heading">{title}</span>
          <button onClick={onClose} className="p-1 rounded hover:bg-maia-sidebar-hover">
            <X className="h-4 w-4 text-maia-text-muted" />
          </button>
        </div>
        <div className="px-4 py-4">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

const Field = ({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) => (
  <label className="block">
    <span className="block text-[12px] text-maia-text-secondary mb-1">{label}</span>
    {children}
    {hint && <span className="block text-[11px] text-maia-text-muted mt-1">{hint}</span>}
  </label>
)

function CreateUserDialog({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (uid: string, username: string) => Promise<void>
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    setErr('')
    if (!username.trim()) { setErr('请输入用户名'); return }
    if (password.length < 8) { setErr('初始密码至少 8 位'); return }
    setBusy(true)
    try {
      const { user } = await authApi.createUser(username.trim(), password, displayName.trim())
      await onCreated(String(user.id), user.username)
    } catch (e) {
      setErr(e instanceof Error ? e.message : '创建用户失败')
      setBusy(false)
    }
  }

  return (
    <ModalShell title="新建用户" onClose={onClose} width="max-w-sm">
      <div className="space-y-3">
        <Field label="用户名">
          <Input value={username} autoFocus maxLength={32} placeholder="登录用，唯一"
            onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="初始密码" hint="至少 8 位；请通过安全渠道告知用户">
          <Input type="password" value={password} autoComplete="new-password"
            onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="显示名（可选）" hint="留空则与用户名相同">
          <Input value={displayName} maxLength={64}
            onChange={(e) => setDisplayName(e.target.value)} />
        </Field>
        <p className="text-[11px] text-maia-text-muted leading-relaxed">
          用户 ID 由系统自动分配（从 1000 起）且不可修改，它就是该用户的数据目录名。
        </p>
        {err && (
          <div className="flex items-center gap-2 px-3 py-2 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-[12px]">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />{err}
          </div>
        )}
        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>取消</Button>
          <Button size="sm" onClick={submit} disabled={busy || !username.trim() || password.length < 8}>
            {busy ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />创建中…</> : '创建'}
          </Button>
        </div>
      </div>
    </ModalShell>
  )
}

function EditUserDialog({ kind, user, onClose, onSaved }: {
  kind: DialogKind
  user: ManagedUser
  onClose: () => void
  onSaved: (text: string) => Promise<void>
}) {
  const [value, setValue] = useState(
    kind === 'rename' ? user.username : kind === 'display' ? (user.display_name ?? '') : '',
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const title = kind === 'rename' ? '修改用户名' : kind === 'display' ? '修改显示名' : '重置密码'
  const builtin = user.builtin === true || user.id === '0'

  const submit = async () => {
    setErr('')
    const v = value.trim()
    if (kind !== 'password' && !v) { setErr('不能为空'); return }
    if (kind === 'password' && v.length < 8) { setErr('新密码至少 8 位'); return }
    setBusy(true)
    try {
      if (kind === 'rename') {
        await authApi.renameUser(user.id, v)
        await onSaved(`用户名已改为 ${v}（用户 ID ${user.id} 不变）`)
      } else if (kind === 'display') {
        await authApi.setUserDisplayName(user.id, v)
        await onSaved(`「${user.username}」的显示名已更新`)
      } else {
        await authApi.resetUserPassword(user.id, v)
        await onSaved(`已重置「${user.username}」的登录密码`)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : '操作失败')
      setBusy(false)
    }
  }

  return (
    <ModalShell title={`${title} · ${user.username}`} onClose={onClose} width="max-w-sm">
      <div className="space-y-3">
        <Field
          label={kind === 'rename' ? '新用户名' : kind === 'display' ? '新显示名' : '新密码'}
          hint={kind === 'rename'
            ? (builtin
                ? '内置超级用户：改的是登录名（存于用户表，密码仍由 .env 配置、不受影响）'
                : '用户 ID 与数据目录不受影响')
            : kind === 'display' ? '仅影响界面展示' : '至少 8 位；用户下次登录即用新密码'}
        >
          <Input
            value={value}
            autoFocus
            type={kind === 'password' ? 'password' : 'text'}
            maxLength={kind === 'rename' ? 32 : 64}
            autoComplete={kind === 'password' ? 'new-password' : 'off'}
            placeholder={kind === 'password' ? '至少 8 位' : ''}
            onChange={(e) => setValue(e.target.value)}
          />
        </Field>
        {err && (
          <div className="flex items-center gap-2 px-3 py-2 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-[12px]">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />{err}
          </div>
        )}
        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>取消</Button>
          <Button size="sm" onClick={submit} disabled={busy}>
            {busy ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />保存中…</> : '保存'}
          </Button>
        </div>
      </div>
    </ModalShell>
  )
}

function DeleteUserDialog({ user, onClose, onDeleted }: {
  user: ManagedUser
  onClose: () => void
  onDeleted: (text: string) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    setErr('')
    setBusy(true)
    try {
      const r = await authApi.deleteUser(user.id)
      await onDeleted(`账号「${user.username}」已删除；用户目录保留在 ${r.home_kept}`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : '删除失败')
      setBusy(false)
    }
  }

  return (
    <ModalShell title="删除账号" onClose={onClose} width="max-w-md">
      <div className="space-y-3">
        <p className="text-[13px] text-maia-text">
          确定删除账号「<span className="font-semibold">{user.username}</span>」（用户 ID {user.id}）？
        </p>
        <div className="px-3 py-2.5 rounded border border-maia-warning/30 bg-maia-warning/10 text-maia-warning text-[12px] leading-relaxed">
          只删除登录账号，用户目录及其数据（对话、私有资源、偏好）不会被删除。
          该账号将立即无法登录；如需彻底清除数据，请在服务器上手工删除{' '}
          <span className="font-mono">/root/SB-USER/{user.id}/</span>。
        </div>
        {err && (
          <div className="flex items-center gap-2 px-3 py-2 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-[12px]">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />{err}
          </div>
        )}
        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>取消</Button>
          <Button variant="danger" size="sm" onClick={submit} disabled={busy}>
            {busy ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />删除中…</> : '确认删除账号'}
          </Button>
        </div>
      </div>
    </ModalShell>
  )
}

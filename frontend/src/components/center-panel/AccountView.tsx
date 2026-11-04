import { useEffect, useState } from 'react'
import {
  User as UserIcon, ShieldCheck, KeyRound, IdCard, LogOut, Loader2,
  CheckCircle2, AlertCircle, Info,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardBody } from '@/components/ui/card'
import { useAuthStore } from '@/stores/auth-store'

/** 个人设置：账号信息（只读）、显示名、密码、登出。 */
export function AccountView() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const updateDisplayName = useAuthStore((s) => s.updateDisplayName)
  const changePassword = useAuthStore((s) => s.changePassword)

  const isBuiltin = user?.builtin === true

  const [displayName, setDisplayName] = useState(user?.displayName ?? '')
  const [savingName, setSavingName] = useState(false)
  const [nameMsg, setNameMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [savingPw, setSavingPw] = useState(false)
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // 登录后 /api/auth/me 与偏好同步可能晚于首次渲染，身份到达后再回填显示名
  useEffect(() => { setDisplayName(user?.displayName ?? '') }, [user?.displayName])

  if (!user) {
    return (
      <div className="h-full overflow-auto">
        <div className="max-w-2xl mx-auto px-6 py-16 text-center text-[13px] text-maia-text-muted">
          当前未登录，没有可显示的账号信息。单用户模式下本机可直接使用，无需登录。
        </div>
      </div>
    )
  }

  const saveName = async () => {
    const name = displayName.trim()
    if (!name) { setNameMsg({ ok: false, text: '显示名不能为空' }); return }
    setSavingName(true)
    setNameMsg(null)
    const ok = await updateDisplayName(name)
    setSavingName(false)
    setNameMsg(ok
      ? { ok: true, text: '显示名已保存' }
      : { ok: false, text: useAuthStore.getState().error || '保存失败' })
  }

  const submitPassword = async () => {
    setPwMsg(null)
    if (newPw.length < 8) { setPwMsg({ ok: false, text: '新密码至少 8 位' }); return }
    if (newPw !== confirmPw) { setPwMsg({ ok: false, text: '两次输入的新密码不一致' }); return }
    if (!oldPw) { setPwMsg({ ok: false, text: '请输入原密码' }); return }
    setSavingPw(true)
    const ok = await changePassword(oldPw, newPw)
    setSavingPw(false)
    if (ok) {
      setOldPw(''); setNewPw(''); setConfirmPw('')
      setPwMsg({ ok: true, text: '密码已修改，下次登录请使用新密码' })
    } else {
      setPwMsg({ ok: false, text: useAuthStore.getState().error || '修改密码失败' })
    }
  }

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-2xl mx-auto px-6 py-6">
        {/* 标题 */}
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-maia-accent/10 border border-maia-accent-border">
            <UserIcon className="h-4.5 w-4.5 text-maia-accent" />
          </div>
          <div>
            <h1 className="text-[15px] font-semibold text-maia-text-heading">个人设置</h1>
            <p className="text-[12px] text-maia-text-muted">账号信息、显示名与登录密码</p>
          </div>
        </div>

        {/* 账号信息 */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <IdCard className="h-4 w-4 text-maia-accent" />
              <span className="text-[13px] font-semibold text-maia-text-heading">账号信息</span>
              {isBuiltin
                ? <Badge variant="warning" className="text-[12px] shrink-0">超级用户</Badge>
                : <Badge variant="default" className="text-[12px] shrink-0">普通用户</Badge>}
            </div>
          </CardHeader>
          <CardBody className="space-y-0.5">
            {isBuiltin && (
              <div className="flex items-start gap-2 px-3 py-2.5 mb-2 rounded border border-maia-warning/30 bg-maia-warning/10 text-maia-warning text-[12px] leading-relaxed">
                <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  这是内置的超级用户：密码在服务器 <span className="font-mono">.env</span> 中
                  （<span className="font-mono">SOTABAND_ROOT_PASSWORD</span>）配置，界面上不可修改——
                  否则界面改动会在下次重启时被 .env 覆盖回去。用户名与显示名可以在界面上修改
                  （保存在用户数据目录，优先级高于 .env 默认值）。仅该账号可以管理用户。
                </span>
              </div>
            )}
            <InfoRow label="用户名" value={user.username} hint={isBuiltin ? '默认值来自 .env，可在「用户管理」中改名覆盖' : undefined} />
            <InfoRow label="用户 ID" value={<span className="font-mono">{user.id}</span>}
              hint="由系统分配，不可修改" />
            <InfoRow label="角色" value={user.role === 'admin' ? '超级用户' : '普通用户'} />
            <InfoRow label="显示名" value={user.displayName || user.username} />
          </CardBody>
        </Card>

        {/* 修改显示名 */}
        <Card className="mt-4">
          <CardHeader>
            <span className="text-[13px] font-semibold text-maia-text-heading">修改显示名</span>
          </CardHeader>
          <CardBody className="space-y-2">
            <p className="text-[12px] text-maia-text-muted">
              显示名用于界面展示，不影响登录用户名与用户 ID。
            </p>
            <div className="flex items-center gap-2">
              <Input
                value={displayName}
                maxLength={64}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="例如：张三"
                className="max-w-xs"
              />
              <Button variant="outline" onClick={saveName} disabled={savingName} className="shrink-0">
                {savingName ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />保存中…</> : '保存'}
              </Button>
            </div>
            {nameMsg && <MsgLine msg={nameMsg} />}
          </CardBody>
        </Card>

        {/* 修改密码 */}
        <Card className="mt-4">
          <CardHeader>
            <div className="flex items-center gap-2">
              <KeyRound className="h-4 w-4 text-maia-accent" />
              <span className="text-[13px] font-semibold text-maia-text-heading">修改密码</span>
            </div>
          </CardHeader>
          <CardBody className="space-y-3">
            {isBuiltin ? (
              <div className="flex items-start gap-2 px-3 py-2.5 rounded border border-maia-border bg-maia-sidebar-hover text-maia-text-muted text-[12px] leading-relaxed">
                <Info className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  超级用户的密码只能由服务器管理员修改 <span className="font-mono">.env</span> 中的{' '}
                  <span className="font-mono">SOTABAND_ROOT_PASSWORD</span> 并重启实例。界面上改密码会被后端拒绝。
                </span>
              </div>
            ) : (
              <>
                <div className="max-w-xs space-y-2">
                  <Input type="password" value={oldPw} placeholder="原密码" autoComplete="current-password"
                    onChange={(e) => setOldPw(e.target.value)} />
                  <Input type="password" value={newPw} placeholder="新密码（至少 8 位）" autoComplete="new-password"
                    onChange={(e) => setNewPw(e.target.value)} />
                  <Input type="password" value={confirmPw} placeholder="确认新密码" autoComplete="new-password"
                    onChange={(e) => setConfirmPw(e.target.value)} />
                </div>
                {pwMsg && <MsgLine msg={pwMsg} />}
                <div>
                  <Button onClick={submitPassword} disabled={savingPw || !oldPw || !newPw || !confirmPw}>
                    {savingPw ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />提交中…</> : '修改密码'}
                  </Button>
                </div>
              </>
            )}
          </CardBody>
        </Card>

        {/* 登出 */}
        <Card className="mt-4">
          <CardBody className="flex items-center justify-between gap-4">
            <div className="min-w-0 text-[12px] text-maia-text-muted">
              退出当前账号；本地缓存的界面偏好不会清除，重新登录后仍会从服务器恢复。
            </div>
            <Button variant="outline" onClick={() => void logout()} className="shrink-0 gap-1.5">
              <LogOut className="h-3.5 w-3.5" />退出登录
            </Button>
          </CardBody>
        </Card>

        <p className="text-[12px] text-maia-text-muted mt-4 leading-relaxed">
          个人偏好（主题、三栏宽度、打开的标签页、资源空间展开状态、对话确认策略）登录后随账号保存在服务器
          你的用户目录中；换一台浏览器登录同一账号会自动恢复。未登录（单用户模式）时仅保存在本浏览器。
        </p>
      </div>
    </div>
  )
}

function InfoRow({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="text-[12px] text-maia-text-muted shrink-0 pt-0.5">{label}</span>
      <div className="min-w-0 text-right">
        <div className="text-[13px] text-maia-text break-all">{value}</div>
        {hint && <div className="text-[11px] text-maia-text-muted mt-0.5">{hint}</div>}
      </div>
    </div>
  )
}

function MsgLine({ msg }: { msg: { ok: boolean; text: string } }) {
  return (
    <div className={`flex items-center gap-2 px-3 py-2 rounded border text-[12px] ${
      msg.ok ? 'border-maia-success/30 bg-maia-success/10 text-maia-success'
             : 'border-maia-danger/30 bg-maia-danger/10 text-maia-danger'}`}>
      {msg.ok ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : <AlertCircle className="h-3.5 w-3.5 shrink-0" />}
      {msg.text}
    </div>
  )
}

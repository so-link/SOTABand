/**
 * 登录 / 注册页。
 *
 * 契约见 `docs/contract-08-auth-network.md` §3.1、§3.4。
 *
 * ## 密码规则（2026-10-27 调整）
 *
 * **8–20 位，且必须同时包含字母和数字。**
 *
 * 旧规则是「8–72 字节，中文按 3 字节计」—— 用户根本没法自己数，提示也没法写清楚。
 * 现在长度按**字符数**计，并给出可直接理解的规则清单（下方三条实时打勾）。
 *
 * 真正的裁决仍在服务端（`core/auth/passwords.py`）；前端这份只是为了**即时反馈**，
 * 不让用户填完点提交才被拒。两边的规则要一起改。
 *
 * ## 为什么用规则清单而不是一句提示
 *
 * 一句「密码需 8-20 位且含字母和数字」用户要点提交才知道哪条不满足。清单把三条
 * 规则分别实时标记，用户改一个字符就能看到对应那条变绿，不用猜。
 *
 * ## 不做客户端"记住我"
 *
 * 会话令牌在 HttpOnly Cookie 里，前端**读不到也不该读**。刷新页面后由
 * `authStore.bootstrap()` 探测会话。
 */

import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  Check,
  Cpu,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Mail,
  User,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  checkPassword,
  describePasswordProblem,
} from '@/lib/password'
import { useAuthStore } from '@/stores/auth-store'

type Mode = 'login' | 'register'

function validateEmail(value: string): string | null {
  if (!value.trim()) return '请输入邮箱'
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value.trim())) return '邮箱格式不正确'
  return null
}

/** 一条密码规则 —— 满足时变绿打勾，否则灰色。 */
function PasswordRule({
  ok,
  text,
  testId,
}: {
  ok: boolean
  text: string
  testId: string
}) {
  return (
    <li
      data-testid={testId}
      data-ok={ok ? 'true' : 'false'}
      className={cn(
        'flex items-center gap-1.5 text-[10px] leading-none tracking-wide transition-colors',
        ok ? 'text-maia-success' : 'text-maia-text-muted'
      )}
    >
      {ok ? <Check className="h-3 w-3 shrink-0" /> : <X className="h-3 w-3 shrink-0" />}
      {text}
    </li>
  )
}

export function LoginView() {
  const status = useAuthStore((s) => s.status)
  const serverError = useAuthStore((s) => s.error)
  const submitting = useAuthStore((s) => s.submitting)
  const login = useAuthStore((s) => s.login)
  const register = useAuthStore((s) => s.register)
  const clearError = useAuthStore((s) => s.clearError)

  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  /**
   * 确认密码的显示开关 —— **必须和上面的密码分开**。
   *
   * 共用一个开关是个容易想当然的错误：用户在「确认密码」里点显示，
   * 上面的密码框也会跟着明文，等于把本来只想核对一处的内容全部暴露；
   * 更糟的是清空确认密码重填时，上面的密码会一直保持明文。
   */
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)

  const emailRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    emailRef.current?.focus()
  }, [])

  const shownError = localError ?? serverError
  const expired = status === 'expired'
  const checks = checkPassword(password)

  function switchMode(next: Mode) {
    if (next === mode) return
    setMode(next)
    setLocalError(null)
    setFieldError(null)
    setConfirmPassword('')
    // 确认框的明文状态跟着模式一起重置 —— 否则切到登录再切回注册时，
    // 一个空着的新确认框会莫名其妙处于"显示"状态
    setShowConfirmPassword(false)
    clearError()
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    clearError()
    setFieldError(null)

    const emailError = validateEmail(email)
    if (emailError) {
      setFieldError(emailError)
      return
    }

    if (mode === 'register') {
      // 逐条给出**具体**问题，而不是一句笼统的"密码不符合要求"。
      // 规则本身来自 lib/password.ts（与服务端同源），这里只负责转成提示。
      const problem = describePasswordProblem(password)
      if (problem) {
        setFieldError(problem)
        return
      }
      if (password !== confirmPassword) {
        setFieldError('两次输入的密码不一致')
        return
      }
    } else if (!password) {
      setFieldError('请输入密码')
      return
    }

    setLocalError(null)

    if (mode === 'register') {
      await register({
        email: email.trim(),
        password,
        displayName: displayName.trim() || undefined,
      })
    } else {
      await login({ email: email.trim(), password })
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-maia-bg p-4">
      <div className="w-full max-w-sm">
        {/* ── 品牌区 ── */}
        <div className="mb-7 flex select-none flex-col items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-maia-accent-border bg-maia-accent-light glow-accent">
            <Cpu className="h-6 w-6 text-maia-accent" />
          </div>
          <div className="text-center">
            <h1 className="text-[15px] font-semibold tracking-wider text-maia-text-heading">
              SOTABand 优智联邦
            </h1>
            <p className="mt-1 text-[11px] tracking-wide text-maia-text-muted">
              多智能体驱动的多模态智能处理引擎
            </p>
          </div>
        </div>

        {/* ── 表单卡片 ── */}
        <div className="rounded-xl border border-maia-border bg-maia-surface p-6 shadow-2xl">
          {/* 模式切换 */}
          <div className="mb-5 flex rounded-lg border border-maia-border bg-maia-bg p-0.5">
            {(
              [
                { key: 'login', label: '登录' },
                { key: 'register', label: '注册' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.key}
                type="button"
                data-testid={`tab-${tab.key}`}
                onClick={() => switchMode(tab.key)}
                className={cn(
                  'flex-1 rounded-md py-1.5 text-[12px] font-semibold tracking-wider transition-all duration-150',
                  mode === tab.key
                    ? 'bg-maia-accent-light text-maia-accent'
                    : 'text-maia-text-muted hover:text-maia-text-secondary'
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {expired && (
            <p className="mb-4 rounded border border-maia-warning-bg bg-maia-warning-bg px-3 py-2 text-[11px] tracking-wide text-maia-warning">
              会话已过期，请重新登录
            </p>
          )}

          <form onSubmit={handleSubmit} className="flex flex-col gap-3.5" noValidate>
            {/* 邮箱 */}
            <label className="flex flex-col gap-1.5">
              <span className="text-[11px] tracking-wider text-maia-text-secondary">邮箱</span>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-maia-text-muted" />
                <Input
                  ref={emailRef}
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  autoComplete="email"
                  className="pl-8"
                  disabled={submitting}
                />
              </div>
            </label>

            {/* 显示名称（仅注册） */}
            {mode === 'register' && (
              <label className="flex flex-col gap-1.5">
                <span className="text-[11px] tracking-wider text-maia-text-secondary">
                  显示名称 <span className="text-maia-text-muted">（可选）</span>
                </span>
                <div className="relative">
                  <User className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-maia-text-muted" />
                  <Input
                    type="text"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="不填则用邮箱前缀"
                    autoComplete="nickname"
                    maxLength={128}
                    className="pl-8"
                    disabled={submitting}
                  />
                </div>
              </label>
            )}

            {/* 密码 */}
            <label className="flex flex-col gap-1.5">
              <span className="text-[11px] tracking-wider text-maia-text-secondary">密码</span>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-maia-text-muted" />
                <Input
                  data-testid="password-input"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={
                    mode === 'register'
                      ? `${PASSWORD_MIN_LENGTH}-${PASSWORD_MAX_LENGTH} 位，含字母和数字`
                      : '请输入密码'
                  }
                  autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                  maxLength={mode === 'register' ? PASSWORD_MAX_LENGTH : undefined}
                  className="pl-8 pr-9"
                  disabled={submitting}
                />
                {/* 显示/隐藏密码 —— 位置由 pr-9 让开，不会压住文字 */}
                <button
                  type="button"
                  data-testid="toggle-password"
                  onClick={() => setShowPassword((v) => !v)}
                  tabIndex={-1}
                  aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-maia-text-muted transition-colors hover:text-maia-text-secondary"
                >
                  {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>

              {mode === 'register' && (
                <ul className="mt-0.5 flex flex-col gap-1" data-testid="pwd-rules">
                  <PasswordRule
                    ok={checks.length}
                    testId="pwd-rule-length"
                    text={`${PASSWORD_MIN_LENGTH}-${PASSWORD_MAX_LENGTH} 位`}
                  />
                  <PasswordRule ok={checks.letter} testId="pwd-rule-letter" text="包含字母" />
                  <PasswordRule ok={checks.digit} testId="pwd-rule-digit" text="包含数字" />
                </ul>
              )}
            </label>

            {/* 确认密码（仅注册） */}
            {mode === 'register' && (
              <label className="flex flex-col gap-1.5">
                <span className="text-[11px] tracking-wider text-maia-text-secondary">
                  确认密码
                </span>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-maia-text-muted" />
                  <Input
                    data-testid="confirm-password-input"
                    type={showConfirmPassword ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="再输入一次"
                    autoComplete="new-password"
                    maxLength={PASSWORD_MAX_LENGTH}
                    className="pl-8 pr-9"
                    disabled={submitting}
                  />
                  {/* 确认密码自己的显示开关 —— 与上方密码框互不影响 */}
                  <button
                    type="button"
                    data-testid="toggle-confirm-password"
                    onClick={() => setShowConfirmPassword((v) => !v)}
                    tabIndex={-1}
                    aria-label={showConfirmPassword ? '隐藏确认密码' : '显示确认密码'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-maia-text-muted transition-colors hover:text-maia-text-secondary"
                  >
                    {showConfirmPassword ? (
                      <EyeOff className="h-3.5 w-3.5" />
                    ) : (
                      <Eye className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
                {confirmPassword.length > 0 && confirmPassword !== password && (
                  <span
                    data-testid="confirm-mismatch"
                    className="text-[10px] tracking-wide text-maia-danger"
                  >
                    两次输入的密码不一致
                  </span>
                )}
              </label>
            )}

            {fieldError && (
              <div
                data-testid="field-error"
                className="flex items-start gap-1.5 rounded border border-maia-danger-bg bg-maia-danger-bg px-2.5 py-2"
              >
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-maia-danger" />
                <span className="text-[11px] leading-relaxed tracking-wide text-maia-danger">
                  {fieldError}
                </span>
              </div>
            )}

            {shownError && (
              <div className="flex items-start gap-1.5 rounded border border-maia-danger-bg bg-maia-danger-bg px-2.5 py-2">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-maia-danger" />
                <span className="text-[11px] leading-relaxed tracking-wide text-maia-danger">
                  {shownError}
                </span>
              </div>
            )}

            <Button type="submit" size="lg" className="mt-1 w-full" disabled={submitting}>
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {mode === 'register' ? '创建账号' : '登录'}
            </Button>
          </form>

          <p className="mt-4 text-center text-[10px] leading-relaxed tracking-wide text-maia-text-muted">
            {mode === 'register'
              ? `密码 ${PASSWORD_MIN_LENGTH}-${PASSWORD_MAX_LENGTH} 位，需同时包含字母和数字 · 注册后自动创建你的个人空间`
              : '还没有账号？点击上方「注册」'}
          </p>
        </div>

        <p className="mt-4 text-center text-[10px] tracking-wide text-maia-text-muted">
          MVP 阶段不做邮箱验证 · 请使用你能记住的密码
        </p>
      </div>
    </div>
  )
}

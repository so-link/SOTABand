/**
 * 修改密码弹窗（PRD FR-A5）。
 *
 * ## 成功后为什么直接把人踢到登录页
 *
 * 后端按 PRD 原文**吊销该用户全部会话**（含当前这条）。当前 Cookie 已经失效，
 * 任何后续请求都会 401 —— 与其让用户继续点下去然后到处报"会话已过期"，
 * 不如明确告诉他"密码已修改，请重新登录"。
 *
 * ## 为什么当前密码错误不该踢人
 *
 * 后端刻意用 **400 而不是 401** 返回「当前密码不正确」。这里也不能把 400 当成
 * 登录失效处理 —— 打错一次密码就被登出是明显的体验问题。
 */

import { useState } from 'react'
import { AlertCircle, Check, Eye, EyeOff, Loader2, Lock, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  checkPassword,
  describePasswordProblem,
} from '@/lib/password'
import { authApi } from '@/services/api/auth'
import { describeError } from '@/services/api/client'
import { useAuthStore } from '@/stores/auth-store'

interface Props {
  open: boolean
  onClose: () => void
}

function Rule({ ok, text, testId }: { ok: boolean; text: string; testId: string }) {
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

export function ChangePasswordDialog({ open, onClose }: Props) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  // 三个密码框各自一个显示开关 —— 共用一个会把不想暴露的那几栏也变成明文
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!open) return null

  const checks = checkPassword(next)
  const mismatch = confirm.length > 0 && confirm !== next

  function reset() {
    setCurrent('')
    setNext('')
    setConfirm('')
    setError(null)
    setShowCurrent(false)
    setShowNew(false)
    setShowConfirm(false)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (!current) {
      setError('请输入当前密码')
      return
    }
    const problem = describePasswordProblem(next)
    if (problem) {
      setError(problem)
      return
    }
    if (next !== confirm) {
      setError('两次输入的新密码不一致')
      return
    }

    setSubmitting(true)
    try {
      const result = await authApi.changePassword({
        currentPassword: current,
        newPassword: next,
      })
      reset()
      onClose()
      // 全部会话已被吊销 → 主动切到未登录，并在登录页说明原因
      await useAuthStore.getState().logout()
      useAuthStore.setState({
        error: `${result.message}（已吊销 ${result.revokedSessions} 个会话）`,
      })
    } catch (err) {
      setError(describeError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      data-testid="change-password-dialog"
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={(e) => {
        // 点遮罩关闭，但提交中不允许（避免请求还在跑就丢了界面）
        if (e.target === e.currentTarget && !submitting) onClose()
      }}
    >
      <div className="w-full max-w-sm rounded-xl border border-maia-border bg-maia-surface p-5 shadow-2xl">
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <h2 className="text-[13px] font-semibold tracking-wider text-maia-text-heading">
              修改密码
            </h2>
            <p className="mt-1 text-[10px] leading-relaxed text-maia-text-muted">
              修改后所有设备都需要重新登录。
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              if (!submitting) {
                reset()
                onClose()
              }
            }}
            className="shrink-0 rounded p-1 text-maia-text-muted transition-colors hover:text-maia-text"
            aria-label="关闭"
            data-testid="cp-close"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3" noValidate>
          {/* 当前密码 */}
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] tracking-wider text-maia-text-secondary">当前密码</span>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-maia-text-muted" />
              <Input
                data-testid="cp-current"
                type={showCurrent ? 'text' : 'password'}
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                placeholder="输入当前密码"
                autoComplete="current-password"
                className="pl-8 pr-9"
                disabled={submitting}
              />
              <button
                type="button"
                data-testid="cp-toggle-current"
                onClick={() => setShowCurrent((v) => !v)}
                tabIndex={-1}
                aria-label={showCurrent ? '隐藏当前密码' : '显示当前密码'}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-maia-text-muted transition-colors hover:text-maia-text-secondary"
              >
                {showCurrent ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          </label>

          {/* 新密码 */}
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] tracking-wider text-maia-text-secondary">新密码</span>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-maia-text-muted" />
              <Input
                data-testid="cp-new"
                type={showNew ? 'text' : 'password'}
                value={next}
                onChange={(e) => setNext(e.target.value)}
                placeholder={`${PASSWORD_MIN_LENGTH}-${PASSWORD_MAX_LENGTH} 位，含字母和数字`}
                autoComplete="new-password"
                maxLength={PASSWORD_MAX_LENGTH}
                className="pl-8 pr-9"
                disabled={submitting}
              />
              <button
                type="button"
                data-testid="cp-toggle"
                onClick={() => setShowNew((v) => !v)}
                tabIndex={-1}
                aria-label={showNew ? '隐藏密码' : '显示密码'}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-maia-text-muted transition-colors hover:text-maia-text-secondary"
              >
                {showNew ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
            <ul className="mt-0.5 flex flex-col gap-1">
              <Rule ok={checks.length} testId="cp-rule-length" text={`${PASSWORD_MIN_LENGTH}-${PASSWORD_MAX_LENGTH} 位`} />
              <Rule ok={checks.letter} testId="cp-rule-letter" text="包含字母" />
              <Rule ok={checks.digit} testId="cp-rule-digit" text="包含数字" />
            </ul>
          </label>

          {/* 确认新密码 */}
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] tracking-wider text-maia-text-secondary">确认新密码</span>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-maia-text-muted" />
              <Input
                data-testid="cp-confirm"
                type={showConfirm ? 'text' : 'password'}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="再输入一次新密码"
                autoComplete="new-password"
                maxLength={PASSWORD_MAX_LENGTH}
                className="pl-8 pr-9"
                disabled={submitting}
              />
              <button
                type="button"
                data-testid="cp-toggle-confirm"
                onClick={() => setShowConfirm((v) => !v)}
                tabIndex={-1}
                aria-label={showConfirm ? '隐藏确认密码' : '显示确认密码'}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-maia-text-muted transition-colors hover:text-maia-text-secondary"
              >
                {showConfirm ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
            {mismatch && (
              <span data-testid="cp-mismatch" className="text-[10px] tracking-wide text-maia-danger">
                两次输入的新密码不一致
              </span>
            )}
          </label>

          {error && (
            <div
              data-testid="cp-error"
              className="flex items-start gap-1.5 rounded border border-maia-danger-bg bg-maia-danger-bg px-2.5 py-2"
            >
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-maia-danger" />
              <span className="text-[11px] leading-relaxed tracking-wide text-maia-danger">
                {error}
              </span>
            </div>
          )}

          <div className="mt-1 flex justify-end gap-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                reset()
                onClose()
              }}
              disabled={submitting}
            >
              取消
            </Button>
            <Button type="submit" size="sm" data-testid="cp-submit" disabled={submitting}>
              {submitting && <Loader2 className="h-3 w-3 animate-spin" />}
              确认修改
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

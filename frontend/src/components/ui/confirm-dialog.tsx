/**
 * 页内确认 / 提示对话框 — 替代原生 window.confirm / window.alert。
 *
 * 原生弹窗会阻塞渲染线程、样式不可控、在演示与录屏里像浏览器默认框。
 * 这里提供两种用法：
 *  1) 组件内：const ask = useConfirm()；await ask({ title, description })
 *  2) 任意位置（含 zustand store、非 React 代码）：await confirmDialog({...})
 * 两者都返回 Promise<boolean>，调用侧写法与原 confirm 一致。
 */
import { useEffect, useRef } from 'react'
import { create } from 'zustand'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'

export interface ConfirmOptions {
  title: string
  description?: string
  /** 确认按钮文案，默认「确认」 */
  confirmText?: string
  /** 取消按钮文案，默认「取消」；为 null 时只显示确认按钮（纯提示） */
  cancelText?: string | null
  /** 确认按钮是否使用危险样式（删除类操作） */
  danger?: boolean
}

interface DialogState extends ConfirmOptions {
  open: boolean
  resolve: ((ok: boolean) => void) | null
}

const useConfirmStore = create<DialogState>(() => ({
  open: false,
  title: '',
  resolve: null,
}))

// 同一时刻只允许一个对话框占用状态槽。并发调用（例如编辑器里点「生成」的
// 同时点了「关闭」）如果后者直接覆盖前者，前一个 Promise 就永远不会 resolve，
// 调用方的 await 会永久挂起。这里把后来的请求排队，前一个结束再开下一个。
type QueueItem = { opts: ConfirmOptions; resolve: (ok: boolean) => void }
const pending: QueueItem[] = []

function showNext() {
  const next = pending.shift()
  if (!next) return
  useConfirmStore.setState({
    open: true,
    confirmText: '确认',
    cancelText: '取消',
    danger: false,
    ...next.opts,
    resolve: next.resolve,
  })
}

/** 打开页内对话框，resolve 用户选择。纯提示（cancelText=null）确认后返回 true。 */
export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    // 已有对话框在显示：排队，等前一个关闭后再出现
    if (useConfirmStore.getState().open) {
      pending.push({ opts, resolve })
      return
    }
    useConfirmStore.setState({
      open: true,
      confirmText: '确认',
      cancelText: '取消',
      danger: false,
      ...opts,
      resolve,
    })
  })
}

/** 组件内使用的 hook；返回的函数用法同 confirmDialog。 */
export function useConfirm() {
  return confirmDialog
}

/** 纯提示（等价 window.alert），只有一个「知道了」按钮。 */
export function alertDialog(title: string, description?: string): Promise<void> {
  return confirmDialog({ title, description, confirmText: '知道了', cancelText: null, danger: false })
    .then(() => undefined)
}

export function ConfirmDialogHost() {
  const { open, title, description, confirmText, cancelText, danger, resolve } = useConfirmStore()

  // 用 ref 持有最新的 resolve/danger：键盘监听只在 open 变化时重绑，
  // 不必把 close 放进依赖里跟着每次渲染重建。
  const resolveRef = useRef(resolve)
  resolveRef.current = resolve
  const dangerRef = useRef(danger)
  dangerRef.current = danger

  const close = (ok: boolean) => {
    resolveRef.current?.(ok)
    useConfirmStore.setState({ open: false, resolve: null })
    // 队列里还有请求（并发调用）时紧接着显示下一个
    showNext()
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false)
      // 危险操作（删除等）不响应 Enter：焦点还在页面上时误按一下就把数据删了，
      // 必须由用户明确点按钮。
      if (e.key === 'Enter' && !dangerRef.current) close(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-[2px]"
      onClick={() => close(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="w-[420px] max-w-[90vw] rounded-lg border border-maia-border bg-maia-surface shadow-2xl p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          {danger && <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5 text-maia-danger" />}
          <div className="min-w-0">
            <h3 className="text-[14px] font-semibold text-maia-text break-words">{title}</h3>
            {description && (
              <p className="mt-2 text-[13px] leading-relaxed text-maia-text-secondary whitespace-pre-line break-words">
                {description}
              </p>
            )}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          {cancelText !== null && (
            <Button variant="outline" size="md" onClick={() => close(false)}>{cancelText}</Button>
          )}
          <Button variant={danger ? 'danger' : 'default'} size="md" onClick={() => close(true)}>
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  )
}

import { useEffect, useRef } from 'react'

/**
 * 绑定 Ctrl+S（Windows/Linux）/ Cmd+S（macOS）保存快捷键。
 *
 * 要点：
 * 1. 必须 preventDefault，否则浏览器会弹出「保存网页」对话框。
 * 2. 监听挂在 window 上，这样即使焦点在编辑框内部也能触发。
 * 3. 用 ref 持有最新回调，避免因回调变化而反复解绑/重绑监听器。
 *
 * @param onSave 触发保存时的回调
 * @param enabled 是否启用（例如未打开编辑器时应关闭）
 */
export function useSaveShortcut(onSave: () => void, enabled = true): void {
  const cbRef = useRef(onSave)
  cbRef.current = onSave

  useEffect(() => {
    if (!enabled) return

    const handler = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey
      if (!mod) return
      // 兼容大小写（Shift+S 时 e.key 为 'S'）
      if (e.key !== 's' && e.key !== 'S') return

      e.preventDefault()
      e.stopPropagation()
      cbRef.current()
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [enabled])
}

import { useEffect, type RefObject } from 'react'

/**
 * 内容增长时让滚动容器跟随到底部（流式输出的标准行为）。
 *
 * 只在「使用者本来就停在底部附近」时跟随：生成过程中如果使用者
 * 上滚翻看前面的内容，不会被每个 token 拽回底部；滚回底部后自动
 * 恢复跟随。工具与 Agent 编辑器的代码生成共用本 hook，行为一致。
 *
 * 用法：ref 挂到会随 dep 变高的滚动容器上，dep 传会增长的内容。
 */
export function useStickToBottom(ref: RefObject<HTMLElement | null>, dep: unknown) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    // 判定「贴底」留 48px 容差：行数按行跳动时不至于因 1-2px 误差失联
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [dep])
}

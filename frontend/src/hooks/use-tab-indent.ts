import { useCallback } from 'react'

type TabHandler = (e: React.KeyboardEvent<HTMLTextAreaElement>) => void

/**
 * 让 textarea 支持 Tab 缩进。
 *
 * 浏览器默认行为是「把焦点移到下一个可聚焦元素」，编辑 Markdown 文档
 * 或 Python 代码时体验很差（缩进打不出来，还会意外跳走焦点）。
 *
 * 行为：
 * - 光标处按 Tab        ：在光标位置插入缩进
 * - 选中跨多行 + Tab    ：整块缩进（每行行首加缩进）
 * - 选中跨多行 + Shift+Tab：整块反缩进
 * - 光标处 + Shift+Tab  ：删除光标前的一个缩进单位
 *
 * 统一使用空格而非制表符 \t，避免 Python 混用 tab/space 导致 TabError。
 *
 * @param indent 缩进字符串，Markdown 建议 2 空格，Python 建议 4 空格
 */
export function useTabIndent(indent: string = '  '): TabHandler {
  return useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key !== 'Tab') return
      e.preventDefault()

      const ta = e.currentTarget
      const { selectionStart: selStart, selectionEnd: selEnd, value } = ta
      const hasSel = selStart !== selEnd
      const multiline = hasSel && value.slice(selStart, selEnd).includes('\n')

      // editRange: 要被替换的字符区间；replaceWith: 替换成的文本
      let editRange: [number, number]
      let replaceWith: string
      let caretStart: number
      let caretEnd: number

      if (multiline) {
        // ── 选中跨多行：整块缩进 / 反缩进 ──
        const lineStart = value.lastIndexOf('\n', selStart - 1) + 1
        let lineEnd = value.indexOf('\n', selEnd)
        if (lineEnd === -1) lineEnd = value.length

        const lines = value.slice(lineStart, lineEnd).split('\n')
        const out = e.shiftKey
          ? lines.map((ln) => {
              if (ln.startsWith(indent)) return ln.slice(indent.length)
              if (ln.startsWith('\t')) return ln.slice(1)
              // 行首空格不足一个缩进单位时，有多少删多少
              const m = ln.match(/^ +/)
              if (m) return ln.slice(Math.min(m[0].length, indent.length))
              return ln
            })
          : lines.map((ln) => indent + ln)

        const newBlock = out.join('\n')
        editRange = [lineStart, lineEnd]
        replaceWith = newBlock
        // 缩进后保持整块选中，方便连续按 Tab 逐级缩进
        caretStart = lineStart
        caretEnd = lineStart + newBlock.length
      } else if (e.shiftKey) {
        // ── 光标处反缩进：删除光标前的一个缩进单位 ──
        // 单行内选中文字时反缩进没有明确语义，忽略以免误删
        if (hasSel) return

        const before = value.slice(0, selStart)
        let cut = 0
        if (before.endsWith(indent)) cut = indent.length
        else if (before.endsWith('\t')) cut = 1
        else return

        // 删除 = 把该区间替换为空串
        editRange = [selStart - cut, selStart]
        replaceWith = ''
        caretStart = caretEnd = selStart - cut
      } else {
        // ── 在光标处插入缩进（有选中则替换选中内容）──
        editRange = [selStart, selEnd]
        replaceWith = indent
        caretStart = caretEnd = selStart + indent.length
      }

      // 写入编辑框。
      // 优先走 document.execCommand('insertText')：它会进入浏览器的编辑历史，
      // 从而保留 Ctrl+Z / Ctrl+Y 的撤销能力。
      // 直接赋值 value + 派发 input 事件虽然同样能触发 React onChange，
      // 但会清空 textarea 的原生 undo 栈，导致撤销功能失效。
      ta.focus()
      ta.setSelectionRange(...editRange)
      let ok = false
      try {
        ok = document.execCommand('insertText', false, replaceWith)
      } catch {
        ok = false
      }
      // 删除操作（替换为空串）在部分浏览器可能"返回 true 却没生效"，
      // 用值是否真的变化来兜底判断
      if (ok && replaceWith === '' && ta.value === value) ok = false
      if (!ok) {
        // 降级方案：牺牲 undo 栈，但保证缩进功能本身可用
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype,
          'value'
        )?.set
        if (!setter) return
        const cur = ta.value
        setter.call(ta, cur.slice(0, editRange[0]) + replaceWith + cur.slice(editRange[1]))
        ta.dispatchEvent(new Event('input', { bubbles: true }))
      }

      // 等待 React 重渲染后再设置光标
      requestAnimationFrame(() => ta.setSelectionRange(caretStart, caretEnd))
    },
    [indent]
  )
}

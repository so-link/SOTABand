import { useRef, useEffect, type KeyboardEvent } from 'react'
import { Send, X, Paperclip, Square, Folder, Database, Wand2 } from 'lucide-react'
import { useChatStore } from '@/stores/chat-store'
import { Button } from '@/components/ui/button'

export function ChatInput() {
  const { inputText, setInputText, attachedFiles, removeAttachment, pathRefs, removePathRef, sendMessage, stopMessage, isSending,
    confirmPolicy, setConfirmPolicy } =
    useChatStore()
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // 回复完成后自动聚焦输入框，方便多轮对话
  const prevSendingRef = useRef(isSending)
  useEffect(() => {
    if (prevSendingRef.current && !isSending) {
      // isSending 从 true → false，回复刚完成
      setTimeout(() => {
        textareaRef.current?.focus()
      }, 50)
    }
    prevSendingRef.current = isSending
  }, [isSending])

  const handleSend = () => {
    if (isSending) {
      stopMessage()
      return
    }
    if (!inputText.trim() && attachedFiles.length === 0 && pathRefs.length === 0) return
    sendMessage()
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`
  }

  return (
    <div className="border-t border-maia-border bg-maia-surface px-4 pt-3 pb-2.5">
      <div className="w-full">
        {/* Attachment bar */}
        {(attachedFiles.length > 0 || pathRefs.length > 0) && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {pathRefs.map((ref, i) => (
              <div
                key={`${ref.name}-${i}`}
                className="flex items-center gap-1 rounded-full bg-blue-500/15 border border-blue-500/40 px-2.5 py-1 text-[12px] text-blue-600 dark:text-blue-400"
                title={ref.kind === 'dataset' ? `数据集引用: ${ref.fileId}` : `${ref.fileId}`}
              >
                {ref.kind === 'dataset' ? <Database className="h-3 w-3" /> : <Folder className="h-3 w-3" />}
                <span className="max-w-[160px] truncate">@{ref.name}</span>
                <button
                  onClick={() => removePathRef(ref.name)}
                  className="ml-0.5 hover:bg-blue-500/20 rounded-full p-0.5"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
            {attachedFiles.map((file) => (
              <div
                key={file.id}
                className="flex items-center gap-1 rounded-full bg-maia-accent-light border border-maia-accent-border px-2.5 py-1 text-[12px] text-maia-accent"
              >
                <Paperclip className="h-3 w-3" />
                <span className="max-w-[120px] truncate">{file.fileName}</span>
                <button
                  onClick={() => removeAttachment(file.id)}
                  className="ml-0.5 hover:bg-maia-accent-border rounded-full p-0.5"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Text input area */}
        <div className="flex items-end gap-2 bg-maia-sidebar rounded-xl border border-maia-border focus-within:border-maia-accent/50 p-2 transition-colors">
          {/* 平台操作确认策略下拉：控制助手发送的操作卡是自动执行还是等用户点
              —— 放在输入框最左侧，让每次看到输入框都知道当前策略，不会盲等自动执行或误点手弹卡 */}
          <div className="relative shrink-0 mb-0.5">
            <Wand2 className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-maia-text-muted pointer-events-none" />
            <select
              value={confirmPolicy}
              onChange={(e) => setConfirmPolicy(e.target.value as 'manual' | 'auto')}
              disabled={isSending}
              title="助手发送的工具/资源操作卡的执行策略"
              className="h-8 appearance-none rounded-lg border border-maia-border bg-maia-surface pl-7 pr-6 text-[12px] text-maia-text focus:outline-none focus:border-maia-accent cursor-pointer disabled:opacity-50"
            >
              <option value="manual">手工确认</option>
              <option value="auto">自动确认</option>
            </select>
            <span className="absolute right-1.5 top-1/2 -translate-y-1/2 pointer-events-none text-maia-text-muted text-[10px]">▾</span>
          </div>

          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => {
              setInputText(e.target.value)
              handleInput()
            }}
            onKeyDown={handleKeyDown}
            placeholder={isSending ? '工具执行中…' : '输入你的需求…（Enter 发送，Shift+Enter 换行，拖拽文件自动附上文件凭证）'}
            rows={1}
            className="flex-1 bg-transparent px-1 py-1.5 text-[14px] leading-[22px] text-maia-text outline-none focus-visible:outline-none resize-none min-h-[32px] max-h-[150px] placeholder:text-maia-text-muted"
            disabled={isSending}
          />
          {isSending ? (
            <Button
              size="icon"
              onClick={handleSend}
              className="shrink-0 h-8 w-8 rounded-lg bg-maia-danger hover:bg-maia-danger/90 text-white"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              size="icon"
              onClick={handleSend}
              disabled={!inputText.trim() && attachedFiles.length === 0 && pathRefs.length === 0}
              className="shrink-0 h-8 w-8 rounded-lg"
            >
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>

      </div>
    </div>
  )
}

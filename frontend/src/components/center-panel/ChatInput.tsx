import { useRef, type KeyboardEvent } from 'react'
import { Send, X, Paperclip } from 'lucide-react'
import { useChatStore } from '@/stores/chat-store'
import { Button } from '@/components/ui/button'

export function ChatInput() {
  const { inputText, setInputText, attachedFiles, removeAttachment, sendMessage, isSending } =
    useChatStore()
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = () => {
    if (!inputText.trim() && attachedFiles.length === 0) return
    sendMessage()
    // Reset textarea height
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
    // Auto-resize textarea
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`
  }

  return (
    <div className="border-t border-gray-200 bg-white px-4 py-3">
      <div className="max-w-3xl mx-auto">
        {/* Attachment bar */}
        {attachedFiles.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {attachedFiles.map((file) => (
              <div
                key={file.id}
                className="flex items-center gap-1 rounded-full bg-maia-accent-light border border-maia-accent-border px-2.5 py-1 text-[11px] tracking-wide text-maia-accent"
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
        <div className="flex items-end gap-2 bg-maia-bg rounded-lg border border-maia-border p-2">
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => {
              setInputText(e.target.value)
              handleInput()
            }}
            onKeyDown={handleKeyDown}
            placeholder="输入你的需求... (Enter 发送，Shift+Enter 换行)"
            rows={1}
            className="flex-1 bg-transparent text-[13px] tracking-wide outline-none focus-visible:outline-none resize-none max-h-[150px] placeholder:text-maia-text-muted"
            disabled={isSending}
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={isSending || (!inputText.trim() && attachedFiles.length === 0)}
            className="shrink-0 h-8 w-8 rounded-lg"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex items-center gap-3 mt-1.5 text-[10px] tracking-wide text-maia-text-muted">
          <span>📎 从左侧拖拽文件到此处附加</span>
          <span>@ 提及 Agent</span>
          <span>/ 命令</span>
        </div>
      </div>
    </div>
  )
}

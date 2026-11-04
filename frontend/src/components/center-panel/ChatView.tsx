import { useState, type DragEvent } from 'react'
import { useChatStore } from '@/stores/chat-store'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { ChatEmptyState } from './ChatEmptyState'
import { MessageSquarePlus, Loader2 } from 'lucide-react'
import { extractDraggedNode, attachDroppedOsFiles } from '@/lib/file-dnd'

export function ChatView() {
  const messages = useChatStore((s) => s.messages)
  const isSending = useChatStore((s) => s.isSending)
  const clearMessages = useChatStore((s) => s.clearMessages)
  const addPathRef = useChatStore((s) => s.addPathRef)
  const [isDragOver, setIsDragOver] = useState(false)
  const [isUploading, setIsUploading] = useState(false)

  // 整个对话界面都是 drop 目标：拖文件到消息列表/输入框任意位置都能附上
  const handleDragOver = (e: DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setIsDragOver(true)
  }
  const handleDragLeave = (e: DragEvent) => {
    // 仅在真正离开对话区域时取消高亮（避免子元素间移动导致闪烁）
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget as Node)) return
    setIsDragOver(false)
  }
  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    if (isSending) return
    const node = extractDraggedNode(e)
    if (node?.fileId) {
      addPathRef({ name: node.name, fileId: node.fileId })
      return
    }
    // OS 文件拖入：先上传到「我的文件」，再记录 file_id 引用
    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      setIsUploading(true)
      void attachDroppedOsFiles(files, addPathRef).finally(() => setIsUploading(false))
    }
  }

  return (
    <div
      className={`relative flex flex-col h-full ${isDragOver ? 'ring-2 ring-inset ring-maia-accent' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {messages.length > 0 && (
        <div className="flex items-center justify-end px-4 pt-2 shrink-0">
          <button
            onClick={() => clearMessages()}
            className="inline-flex items-center gap-1 h-6 px-1.5 rounded text-[13px] text-maia-text-muted hover:text-maia-accent hover:bg-maia-sidebar-hover transition-colors"
            title="开启一个新会话（当前会话仍保留在右侧对话历史）"
          >
            <MessageSquarePlus className="h-3 w-3" />
            新建对话
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0">
        {messages.length === 0 ? (
          <ChatEmptyState />
        ) : (
          <MessageList messages={messages} isSending={isSending} />
        )}
      </div>

      {isUploading && (
        <div className="px-4 pb-1 flex items-center gap-1 text-[12px] text-maia-text-muted shrink-0">
          <Loader2 className="h-3 w-3 animate-spin" />
          正在上传…
        </div>
      )}

      <ChatInput />

      {/* 拖动文件进入对话时的半透明模态提示（不拦截 drop 事件） */}
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-maia-accent/10">
          <span className="rounded-lg border border-maia-accent bg-maia-surface px-4 py-2 text-[14px] text-maia-accent shadow-lg">
            松开以附加文件
          </span>
        </div>
      )}
    </div>
  )
}

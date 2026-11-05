import { useState, useEffect, type DragEvent } from 'react'
import { useChatStore } from '@/stores/chat-store'
import { useUIStore } from '@/stores/ui-store'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { ChatEmptyState } from './ChatEmptyState'
import { MessageSquarePlus, Loader2 } from 'lucide-react'
import { extractDraggedPayload, attachDroppedOsFiles } from '@/lib/file-dnd'
import { useAuthStore } from '@/stores/auth-store'

export function ChatView() {
  const messages = useChatStore((s) => s.messages)
  const isSending = useChatStore((s) => s.isSending)
  const clearMessages = useChatStore((s) => s.clearMessages)
  const addPathRef = useChatStore((s) => s.addPathRef)
  const dragKind = useUIStore((s) => s.dragKind)
  const setDragKind = useUIStore((s) => s.setDragKind)
  const [isUploading, setIsUploading] = useState(false)
  const isDragOver = dragKind !== null

  const handleDragEnter = (e: DragEvent) => {
    if (
      e.dataTransfer.types.includes('Files') ||
      e.dataTransfer.types.includes('application/json') ||
      e.dataTransfer.types.includes('application/x-sotaband-dataset')
    ) {
      e.preventDefault()
    }
  }
  const handleDragOver = (e: DragEvent) => {
    // dragover 必须 preventDefault，否则 drop 不触发；无条件允许放置
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }
  const handleDragLeave = (e: DragEvent) => {
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget as Node)) return
  }
  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    if (isSending) {
      setDragKind(null)
      return
    }
    const { node, datasetOnly } = extractDraggedPayload(e)
    if (node?.fileId) {
      // 数据集节点 → 引用（只带 id）；文件节点 → 附件（带内容）
      addPathRef({
        name: node.name,
        fileId: node.fileId,
        kind: datasetOnly ? 'dataset' : 'file',
      })
      setDragKind(null)
      return
    }
    // OS 文件拖入：先上传到「我的文件」，再记录 file_id 引用
    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      // 拖入 OS 文件会触发上传：未登录时先弹登录框，避免 401 后静默失败
      if (!useAuthStore.getState().requireLogin()) {
        setDragKind(null)
        return
      }
      setIsUploading(true)
      void attachDroppedOsFiles(files, addPathRef).finally(() => {
        setIsUploading(false)
        setDragKind(null)
      })
    } else {
      setDragKind(null)
    }
  }

  // 拖拽结束兜底清除全局拖拽态（拖出窗口/松手都会触发）
  useEffect(() => {
    const clear = () => setDragKind(null)
    window.addEventListener('dragend', clear)
    return () => window.removeEventListener('dragend', clear)
  }, [setDragKind])

  return (
    <div
      className="relative flex flex-col h-full"
      onDragEnter={handleDragEnter}
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

      {/* 拖拽提示：由全局 dragKind 驱动，一拖就显示（无需拖入本区域） */}
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-[50] flex items-center justify-center">
          <div className="absolute inset-0 border-2 border-dashed border-maia-accent/70 bg-maia-accent/5 rounded" />
          <span className="relative rounded-lg border border-maia-accent bg-maia-surface px-4 py-2 text-[14px] text-maia-accent shadow-lg">
            {dragKind === 'dataset' ? '松开以引用数据集（不带内容，助手会查到数据）' : '松开以附加文件'}
          </span>
        </div>
      )}
    </div>
  )
}

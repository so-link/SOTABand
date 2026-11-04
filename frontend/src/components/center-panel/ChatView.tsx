import { useState, useEffect, type DragEvent } from 'react'
import { useChatStore } from '@/stores/chat-store'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { ChatEmptyState } from './ChatEmptyState'
import { MessageSquarePlus, Loader2 } from 'lucide-react'
import { extractDraggedPayload, attachDroppedOsFiles } from '@/lib/file-dnd'

export function ChatView() {
  const messages = useChatStore((s) => s.messages)
  const isSending = useChatStore((s) => s.isSending)
  const clearMessages = useChatStore((s) => s.clearMessages)
  const addPathRef = useChatStore((s) => s.addPathRef)
  const [isDragOver, setIsDragOver] = useState(false)
  const [isDatasetDrag, setIsDatasetDrag] = useState(false)
  const [isUploading, setIsUploading] = useState(false)

  // dragenter 立即响应（dragover 在部分浏览器对自定义 MIME 有节流，
  // 且子元素间移动会反复触发 dragleave 导致提示闪烁）
  const handleDragEnter = (e: DragEvent) => {
    if (
      e.dataTransfer.types.includes('Files') ||
      e.dataTransfer.types.includes('application/json') ||
      e.dataTransfer.types.includes('application/x-sotaband-dataset')
    ) {
      e.preventDefault()
      setIsDragOver(true)
      setIsDatasetDrag(e.dataTransfer.types.includes('application/x-sotaband-dataset'))
    }
  }
  const handleDragOver = (e: DragEvent) => {
    // dragover 必须 preventDefault，否则 drop 不触发；无条件允许放置
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }
  const handleDragLeave = (e: DragEvent) => {
    // 仅在真正离开对话区域时取消高亮
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget as Node)) return
    setIsDragOver(false)
    setIsDatasetDrag(false)
  }
  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    setIsDatasetDrag(false)
    if (isSending) return
    const { node } = extractDraggedPayload(e)
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

  // 拖拽结束时兜底清除高亮（drop 之外，拖出窗口也会触发 dragend 之外的路径）
  useEffect(() => {
    const clear = () => {
      setIsDragOver(false)
      setIsDatasetDrag(false)
    }
    window.addEventListener('dragend', clear)
    return () => window.removeEventListener('dragend', clear)
  }, [])

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

      {/* 拖拽提示浮层（最高 z，盖住输入框白底，避免高亮框被"切断"） */}
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-[50] flex items-center justify-center">
          <div className="absolute inset-0 border-2 border-dashed border-maia-accent/70 bg-maia-accent/5 rounded" />
          <span className="relative rounded-lg border border-maia-accent bg-maia-surface px-4 py-2 text-[14px] text-maia-accent shadow-lg">
            {isDatasetDrag ? '数据集暂不支持拖入对话（请到资源空间引用）' : '松开以附加文件'}
          </span>
        </div>
      )}
    </div>
  )
}

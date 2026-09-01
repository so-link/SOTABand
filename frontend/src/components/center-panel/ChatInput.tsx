import { useRef, useEffect, useState, type DragEvent, type KeyboardEvent } from 'react'
import { Send, X, Paperclip, Square, Loader2 } from 'lucide-react'
import { useChatStore } from '@/stores/chat-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { hasDroppableContent, readWorkspaceFileDragData } from '@/lib/dnd'
import type { FileAttachment } from '@/types/chat'
import type { FileTreeNode } from '@/types/workspace'

/** 递归收集所有文件节点（目录本身不是有效附件） */
function collectFileNodes(node: FileTreeNode | null, out: FileTreeNode[] = []): FileTreeNode[] {
  if (!node) return out
  if (node.type === 'file') out.push(node)
  node.children?.forEach((child) => collectFileNodes(child, out))
  return out
}

/** 文件树节点 → 附件。与工作区间的双击附加保持同一形状 */
function toAttachment(node: FileTreeNode): FileAttachment {
  return {
    id: node.id,
    fileName: node.name,
    filePath: node.path,
    fileSize: node.size || 0,
    format: node.format || 'unknown',
  }
}

export function ChatInput() {
  const { inputText, setInputText, attachedFiles, removeAttachment, sendMessage, stopMessage, isSending } =
    useChatStore()
  const addAttachment = useChatStore((s) => s.addAttachment)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [isDraggingOver, setIsDraggingOver] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  // dragenter/dragleave 会在掠过每个子元素时反复触发，用计数器判断是否真正离开容器，
  // 否则高亮会在输入框内移动鼠标时不停闪烁。
  const dragDepthRef = useRef(0)

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
    if (!inputText.trim() && attachedFiles.length === 0) return
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

  // ── 拖拽附加 ──
  // 拖拽源在左侧工作区间（WorkspaceFileTree），这里只负责接收。
  // 二者通过 lib/dnd.ts 的 MIME 契约通信，避免各写一份字符串导致对不上。

  const resetDragState = () => {
    dragDepthRef.current = 0
    setIsDraggingOver(false)
  }

  const handleDragEnter = (e: DragEvent) => {
    if (isSending || !hasDroppableContent(e.dataTransfer)) return
    e.preventDefault()
    dragDepthRef.current += 1
    setIsDraggingOver(true)
  }

  const handleDragOver = (e: DragEvent) => {
    if (isSending || !hasDroppableContent(e.dataTransfer)) return
    // 必须阻止默认行为，否则浏览器不会触发 drop，文件会被当作导航直接打开
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const handleDragLeave = (e: DragEvent) => {
    if (!hasDroppableContent(e.dataTransfer)) return
    e.preventDefault()
    dragDepthRef.current -= 1
    if (dragDepthRef.current <= 0) resetDragState()
  }

  /** 从操作系统拖入的文件：先上传到工作区间，再把新生成的节点附加到对话 */
  const attachDroppedOsFiles = async (files: FileList) => {
    const beforeIds = new Set(collectFileNodes(useFileTreeStore.getState().root).map((n) => n.id))
    setIsUploading(true)
    try {
      await useFileTreeStore.getState().uploadFiles(files)
    } finally {
      setIsUploading(false)
    }
    // uploadFiles 只负责入库不返回新节点，故用「上传前后的差集」取回新增项
    const added = collectFileNodes(useFileTreeStore.getState().root).filter(
      (n) => !beforeIds.has(n.id),
    )
    added.forEach((node) => addAttachment(toAttachment(node)))
    if (added.length > 0) textareaRef.current?.focus()
  }

  const handleDrop = (e: DragEvent) => {
    if (!hasDroppableContent(e.dataTransfer)) return
    e.preventDefault()
    resetDragState()
    if (isSending) return

    const payload = readWorkspaceFileDragData(e.dataTransfer)
    if (payload) {
      addAttachment({
        id: payload.id,
        fileName: payload.name,
        filePath: payload.path,
        fileSize: payload.size,
        format: payload.format || 'unknown',
      })
      textareaRef.current?.focus()
      return
    }

    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      void attachDroppedOsFiles(files)
    }
  }

  return (
    <div
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        'border-t bg-maia-surface px-4 py-3 transition-colors',
        isDraggingOver ? 'border-maia-accent-border bg-maia-accent-light/40' : 'border-maia-border',
      )}
    >
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
        <div
          className={cn(
            'flex items-end gap-2 bg-maia-sidebar rounded-xl border p-2 transition-colors',
            isDraggingOver
              ? 'border-maia-accent-border ring-2 ring-maia-accent/25'
              : 'border-maia-border/50',
          )}
        >
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => {
              setInputText(e.target.value)
              handleInput()
            }}
            onKeyDown={handleKeyDown}
            placeholder={isSending ? '工具执行中...' : '输入你的需求... (Enter 发送，Shift+Enter 换行)'}
            rows={1}
            className="flex-1 bg-transparent text-[13px] tracking-wide outline-none focus-visible:outline-none resize-none max-h-[150px] placeholder:text-maia-text-muted"
            disabled={isSending}
          />
          {isSending ? (
            <Button
              size="icon"
              onClick={handleSend}
              className="shrink-0 h-8 w-8 rounded-lg bg-red-500 hover:bg-red-600 text-white"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              size="icon"
              onClick={handleSend}
              disabled={!inputText.trim() && attachedFiles.length === 0}
              className="shrink-0 h-8 w-8 rounded-lg"
            >
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>

        <div
          className={cn(
            'flex items-center gap-3 mt-1.5 text-[10px] tracking-wide transition-colors',
            isDraggingOver ? 'text-maia-accent' : 'text-maia-text-muted',
          )}
        >
          {isUploading ? (
            <span className="flex items-center gap-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              正在上传并附加...
            </span>
          ) : (
            <span>📎 {isDraggingOver ? '松手即可附加' : '从左侧拖拽文件到此处附加'}</span>
          )}
          <span>@ 提及 Agent</span>
          <span>/ 命令</span>
        </div>
      </div>
    </div>
  )
}

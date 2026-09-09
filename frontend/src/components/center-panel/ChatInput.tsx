import { useRef, useEffect, useState, type DragEvent, type KeyboardEvent } from 'react'
import { Send, X, Paperclip, Square, Folder, Loader2 } from 'lucide-react'
import { useChatStore } from '@/stores/chat-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { Button } from '@/components/ui/button'
import type { FileTreeNode } from '@/types/workspace'

/** 递归收集所有文件节点（用于 OS 拖入上传后的差集对比） */
function collectFileNodes(node: FileTreeNode | null, out: FileTreeNode[] = []): FileTreeNode[] {
  if (!node) return out
  if (node.type === 'file') out.push(node)
  node.children?.forEach((child) => collectFileNodes(child, out))
  return out
}

export function ChatInput() {
  const { inputText, setInputText, attachedFiles, removeAttachment, pathRefs, addPathRef, removePathRef, sendMessage, stopMessage, isSending } =
    useChatStore()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [isUploading, setIsUploading] = useState(false)

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

  // ── 拖拽：采用导师版的路径映射方案（@文件名 → 提交时自动填入完整路径）──
  // OS 文件拖入上传为本分支增量：先入库工作区间，再记录路径映射。

  // 从拖拽事件中解析文件/目录节点
  const extractDraggedNode = (e: DragEvent): FileTreeNode | null => {
    const jsonData = e.dataTransfer.getData('application/json')
    if (jsonData) {
      try {
        const node = JSON.parse(jsonData) as FileTreeNode
        if (node?.path) return node
      } catch {
        /* ignore */
      }
    }
    // 兜底：尝试读取纯文本路径
    const textData = e.dataTransfer.getData('text/plain')
    if (textData && /[\\/\\\\]/.test(textData)) {
      const p = textData.trim()
      const name = p.split(/[\\/\\\\]/).filter(Boolean).pop() || p
      return { id: p, name, type: 'file', category: 'unknown', path: p }
    }
    return null
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    if (isSending) return

    const node = extractDraggedNode(e)
    if (node?.path) {
      // 后台记录映射（@文件名 → 完整路径）
      addPathRef({ name: node.name, path: node.path })
      textareaRef.current?.focus()
      return
    }

    // OS 文件拖入（本分支增量）：上传到工作区间后记录路径映射
    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      void attachDroppedOsFiles(files)
    }
  }

  const handleDragOver = (e: DragEvent) => {
    // 关键：dragover 必须 preventDefault，否则 drop 事件不会触发。
    // 注意：dragover 阶段浏览器禁止读取自定义 MIME 类型（如 application/json），
    // 因此这里不能依赖 extractDraggedNode 来判断，而是无条件允许放置，
    // 路径解析统一放到 drop 阶段完成。
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setIsDragOver(true)
  }

  const handleDragLeave = (e: DragEvent) => {
    // 仅在真正离开输入区域时取消高亮（避免子元素间移动导致闪烁）
    const el = textareaRef.current
    if (el && e.relatedTarget && el.contains(e.relatedTarget as Node)) return
    setIsDragOver(false)
  }

  /** 从操作系统拖入的文件：先上传到工作区间，再记录路径映射 */
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
    added.forEach((node) => addPathRef({ name: node.name, path: node.path }))
    if (added.length > 0) textareaRef.current?.focus()
  }

  return (
    <div className="border-t border-maia-border bg-maia-surface px-4 py-3">
      <div className="max-w-3xl mx-auto">
        {/* Attachment bar */}
        {(attachedFiles.length > 0 || pathRefs.length > 0) && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {pathRefs.map((ref, i) => (
              <div
                key={`${ref.name}-${i}`}
                className="flex items-center gap-1 rounded-full bg-blue-500/15 border border-blue-500/40 px-2.5 py-1 text-[11px] tracking-wide text-blue-600 dark:text-blue-400"
                title={`${ref.path}`}
              >
                <Folder className="h-3 w-3" />
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
          className={`flex items-end gap-2 bg-maia-sidebar rounded-xl border p-2 transition-colors ${
            isDragOver ? 'border-maia-accent border-2' : 'border-maia-border/50'
          }`}
        >
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => {
              setInputText(e.target.value)
              handleInput()
            }}
            onKeyDown={handleKeyDown}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
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
              disabled={!inputText.trim() && attachedFiles.length === 0 && pathRefs.length === 0}
              className="shrink-0 h-8 w-8 rounded-lg"
            >
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>

        <div className="flex items-center gap-3 mt-1.5 text-[10px] tracking-wide text-maia-text-muted">
          {isUploading ? (
            <span className="flex items-center gap-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              正在上传...
            </span>
          ) : (
            <span>🖱️ 拖拽文件/目录到输入框，提交时自动填入完整路径</span>
          )}
          <span>@ 提及 Agent</span>
          <span>/ 命令</span>
        </div>
      </div>
    </div>
  )
}

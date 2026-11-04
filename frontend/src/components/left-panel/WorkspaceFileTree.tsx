import { useState, useRef } from 'react'
import {
  ChevronRight,
  ChevronDown,
  File,
  Folder,
  Search,
  Upload,
  FolderInput,
  FolderOpen,
  Trash2,
  Activity,
  Image,
  Table,
  FileText,
  Box,
  Archive,
  FileOutput,
  AlertTriangle,
  Loader2,
} from 'lucide-react'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { useResourceStore } from '@/stores/resource-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { DatasetImportDialog } from './DatasetImportDialog'
import { nodeFileUrl, type FileTreeNode, type FileCategory } from '@/types/workspace'
import { useUIStore } from '@/stores/ui-store'

const FILE_ICONS: Record<FileCategory, typeof File> = {
  eeg: Activity,
  image: Image,
  table: Table,
  text: FileText,
  model: Box,
  archive: Archive,
  result: FileOutput,
  folder: Folder,
  unknown: File,
}

export function WorkspaceFileTree() {
  const {
    root,
    selectedFile,
    selectFile,
    toggleExpand,
    uploadFiles,
    searchQuery,
    setSearchQuery,
    getFilteredTree,
  } = useFileTreeStore()
  const selectResource = useResourceStore((s) => s.selectResource)
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const displayTree = searchQuery.trim() ? getFilteredTree() : root

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.length) return
    setUploading(true)
    await uploadFiles(e.target.files)
    setUploading(false)
    e.target.value = ''
  }

  const handleDragStart = (e: React.DragEvent, node: FileTreeNode) => {
    // 数据集节点不能当文件附件（fileId 是 dataset:<id>，非平台文件）：
    // 显示 X 光标 + 写标记进拖拽数据，让对话侧提示「数据集暂不支持」
    if (node.fileId?.startsWith('dataset:')) {
      e.dataTransfer.effectAllowed = 'none'
      e.dataTransfer.setData('application/x-sotaband-dataset', node.fileId)
      useUIStore.getState().setDragKind('dataset')
      return
    }
    e.dataTransfer.setData('application/json', JSON.stringify(node))
    // 同时设置 text/plain 类型（file_id），提升跨组件拖拽兼容性
    e.dataTransfer.setData('text/plain', node.fileId || '')
    e.dataTransfer.effectAllowed = 'copy'
    // 一拖就提示（不等拖入对话区）
    useUIStore.getState().setDragKind('file')
  }

  const [showImport, setShowImport] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<FileTreeNode | null>(null)
  const [deleting, setDeleting] = useState(false)

  const handleImportDataset = (dsNode: FileTreeNode) => {
    const { root, persist } = useFileTreeStore.getState()
    if (root) {
      root.children = [...(root.children || []), dsNode]
      useFileTreeStore.setState({ root: { ...root } })
      persist()
    }
    setShowImport(false)
  }

  const handleClear = async () => {
    // 走 store 的统一入口：清空文件的同时会把对话里残留的附件引用清掉
    await useFileTreeStore.getState().clearWorkspace()
  }

  const handleDoubleClick = (node: FileTreeNode) => {
    selectFile(node)
    if (node.type !== 'file') return

    // PDF 文件：直接用浏览器打开（按 file_id 访问）
    const ext = (node.format || node.name.split('.').pop() || '').toLowerCase()
    if (ext === 'pdf') {
      const url = nodeFileUrl(node)
      if (url) window.open(url, '_blank')
      return
    }

    // 其他文件（图片/视频/MD/CSV 等）：切换到文件预览视图
    useFileTreeStore.getState().setPreviewFile(node)
    useUIStore.getState().setActiveView('file-preview')
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const res = await fetch(`/api/file/${encodeURIComponent(deleteTarget.fileId || '')}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(detail.detail || `删除失败 (HTTP ${res.status})`)
      }
      // 从文件树中移除该节点
      useFileTreeStore.getState().removeNode(deleteTarget.id)
    } catch (e) {
      alert(`删除失败: ${e}`)
    } finally {
      setDeleting(false)
      setDeleteTarget(null)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Search */}
      <div className="px-3 pt-3 pb-1.5">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-maia-text-muted" />
          <Input
            placeholder="搜索文件..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-7 h-7 text-[13px] text-maia-text bg-maia-bg/70 border-maia-border focus:bg-maia-surface"
          />
        </div>
      </div>

      {/* Upload / Import / Clear */}
      <div className="px-3 pb-2 space-y-1">
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleUpload} />
        <div className="flex gap-1">
          <Button
            variant="outline" size="sm"
            className="flex-1 min-w-0 px-2 text-[13px] h-7 border-maia-border text-maia-text-secondary hover:bg-maia-sidebar-hover"
            onClick={() => fileInputRef.current?.click()} disabled={uploading}
          >
            <Upload className="h-3.5 w-3.5" />
            {uploading ? '上传中...' : '上传'}
          </Button>
          <Button
            variant="outline" size="sm"
            className="flex-1 min-w-0 px-2 text-[13px] h-7 border-maia-border text-maia-text-secondary hover:bg-maia-sidebar-hover"
            onClick={() => setShowImport(true)}
            title="从数据空间导入已注册的数据集"
          >
            <FolderInput className="h-3.5 w-3.5" />
            导入
          </Button>
        </div>
        {root?.children && root.children.length > 0 && (
          <Button
            variant="ghost" size="sm"
            className="w-full text-[13px] h-6 text-maia-text-muted hover:text-maia-danger hover:bg-maia-danger-bg"
            onClick={handleClear}
          >
            <Trash2 className="h-3 w-3" />
            清空我的文件
          </Button>
        )}
      </div>

      {/* File tree */}
      <div className="flex-1 min-h-0 overflow-auto px-2 pb-2">
        {displayTree?.children?.map((node) => (
          <FileTreeItem
            key={node.id}
            node={node}
            depth={0}
            selectedFile={selectedFile}
            onToggle={toggleExpand}
            onSelect={(n) => {
              selectFile(n)
              const ds = useResourceStore.getState().dataResources
              const match = ds.find((r) => r.name === n.name)
              if (match) selectResource(match)
            }}
            onDoubleClick={handleDoubleClick}
            onDragStart={handleDragStart}
            onDelete={setDeleteTarget}
          />
        ))}
        {displayTree?.children?.length === 0 && (
          <div className="flex flex-col items-center text-center px-4 py-10">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-maia-border bg-maia-surface text-maia-text-muted">
              <FolderOpen className="h-4.5 w-4.5" />
            </span>
            <p className="text-[13px] font-medium text-maia-text mt-2.5">我的文件为空</p>
            <p className="text-[12px] leading-[18px] text-maia-text-muted mt-1 max-w-[220px]">
              上传本地文件，或从数据空间导入数据集
            </p>
            <Button variant="outline" size="sm" className="mt-3 text-[12px]" onClick={() => fileInputRef.current?.click()}>
              <Upload className="h-3.5 w-3.5" />
              上传文件
            </Button>
          </div>
        )}
      </div>

      {showImport && (
        <DatasetImportDialog
          onImport={handleImportDataset}
          onClose={() => setShowImport(false)}
        />
      )}

      {/* 删除文件确认弹窗 */}
      {deleteTarget && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40" onClick={() => !deleting && setDeleteTarget(null)}>
          <div className="bg-maia-surface rounded-xl shadow-xl w-[400px] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 px-4 py-3 border-b border-maia-border">
              <Trash2 className="h-4 w-4 text-maia-danger" />
              <span className="text-sm font-semibold text-maia-text-heading">删除文件</span>
            </div>
            <div className="px-4 py-4">
              <p className="text-[13px] text-maia-text leading-relaxed">
                确定要永久删除文件 <span className="font-semibold text-maia-danger">{deleteTarget.name}</span> 吗？
              </p>
              <p className="text-[12px] text-maia-text-muted mt-2 flex items-center gap-1">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                此操作将从文件系统中永久删除该文件，且无法恢复。
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-maia-border bg-maia-bg/50">
              <Button variant="outline" size="sm" onClick={() => setDeleteTarget(null)} disabled={deleting} className="text-[12px] h-7">取消</Button>
              <Button variant="danger" size="sm" onClick={handleDelete} disabled={deleting} className="text-[12px] h-7">
                {deleting ? <><Loader2 className="h-3 w-3 animate-spin" />删除中...</> : '确认删除'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// --- Recursive Tree Item ---

interface FileTreeItemProps {
  node: FileTreeNode
  depth: number
  selectedFile: FileTreeNode | null
  onToggle: (id: string) => void
  onSelect: (node: FileTreeNode) => void
  onDoubleClick: (node: FileTreeNode) => void
  onDragStart: (e: React.DragEvent, node: FileTreeNode) => void
  onDelete: (node: FileTreeNode) => void
}

function FileTreeItem({
  node,
  depth,
  selectedFile,
  onToggle,
  onSelect,
  onDoubleClick,
  onDragStart,
  onDelete,
}: FileTreeItemProps) {
  const isSelected = selectedFile?.id === node.id
  const isDir = node.type === 'directory'
  const Icon = FILE_ICONS[node.category] || File

  return (
    <div className="group/tree-item">
      <div
        draggable
        onDragStart={(e) => onDragStart(e, node)}
        onClick={() => {
          if (isDir) onToggle(node.id)
          onSelect(node)
        }}
        onDoubleClick={() => onDoubleClick(node)}
        className={cn(
          'flex items-center gap-1 h-7 px-1.5 rounded cursor-pointer text-[13px] select-none',
          'hover:bg-maia-sidebar-hover transition-colors',
          isSelected && 'bg-maia-sidebar-active text-maia-text-heading',
        )}
        style={{ paddingLeft: `${depth * 14 + 6}px` }}
      >
        {/* Expand/collapse */}
        {isDir ? (
          node.expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-maia-text-muted" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-maia-text-muted" />
          )
        ) : (
          <span className="w-3 shrink-0" />
        )}

        <Icon
          className={cn(
            'h-3.5 w-3.5 shrink-0',
            isDir ? 'text-amber-500' : 'text-maia-text-muted'
          )}
        />

        <span className="truncate flex-1">{node.name}</span>

        {node.type === 'file' && node.size && (
          <span className="text-[12px] text-maia-text-muted shrink-0">
            {formatSize(node.size)}
          </span>
        )}

        {/* 删除按钮（仅文件） */}
        {node.type === 'file' && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onDelete(node)
            }}
            className="shrink-0 p-0.5 rounded text-maia-text-muted hover:text-maia-danger hover:bg-red-500/10 opacity-0 group-hover/tree-item:opacity-100 transition-opacity"
            title="删除文件"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        )}
      </div>

      {/* Children */}
      {isDir && node.expanded && node.children && (
        <div>
          {node.children.map((child) => (
            <FileTreeItem
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedFile={selectedFile}
              onToggle={onToggle}
              onSelect={onSelect}
              onDoubleClick={onDoubleClick}
              onDragStart={onDragStart}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1048576).toFixed(1)}MB`
}

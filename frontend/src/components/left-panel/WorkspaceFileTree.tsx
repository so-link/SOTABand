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
  MoreVertical,
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
import { useUIStore } from '@/stores/ui-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { DatasetImportDialog } from './DatasetImportDialog'
import { OpenDirectoryDialog } from './OpenDirectoryDialog'
import type { FileTreeNode, FileCategory } from '@/types/workspace'

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
    e.dataTransfer.setData('application/json', JSON.stringify(node))
    // 同时设置 text/plain 类型（完整路径），提升跨组件拖拽兼容性
    e.dataTransfer.setData('text/plain', node.path || '')
    e.dataTransfer.effectAllowed = 'copy'
  }

  const [showImport, setShowImport] = useState(false)
  const [showOpenDir, setShowOpenDir] = useState(false)
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

  const handleOpenDirectory = (dirNode: FileTreeNode) => {
    const { root, persist } = useFileTreeStore.getState()
    if (root) {
      root.children = [...(root.children || []), dirNode]
      useFileTreeStore.setState({ root: { ...root } })
      persist()
    }
    setShowOpenDir(false)
  }

  const handleClear = () => {
    const { root, persist } = useFileTreeStore.getState()
    if (root) {
      root.children = []
      useFileTreeStore.setState({ root: { ...root } })
      persist()
    }
  }

  const handleDoubleClick = (node: FileTreeNode) => {
    selectFile(node)
    if (node.type !== 'file') return

    // PDF 文件：直接用浏览器打开
    const ext = (node.format || node.name.split('.').pop() || '').toLowerCase()
    if (ext === 'pdf') {
      window.open(`/api/file/download?path=${encodeURIComponent(node.path)}`, '_blank')
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
      const res = await fetch('/api/file/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: deleteTarget.path }),
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
            className="pl-7 h-7 text-[12px] text-maia-text tracking-wide bg-maia-bg/70 border-maia-border focus:bg-maia-surface"
          />
        </div>
      </div>

      {/* Upload / Import / Clear */}
      <div className="px-3 pb-2 space-y-1">
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleUpload} />
        <div className="flex gap-1">
          <Button
            variant="outline" size="sm"
            className="flex-1 text-[11px] tracking-wider h-7 border-maia-border text-maia-text-secondary hover:bg-maia-sidebar-hover"
            onClick={() => fileInputRef.current?.click()} disabled={uploading}
          >
            <Upload className="h-3 w-3" />
            {uploading ? '上传中...' : '上传'}
          </Button>
          <Button
            variant="outline" size="sm"
            className="flex-1 text-[11px] tracking-wider h-7 border-maia-border text-maia-text-secondary hover:bg-maia-sidebar-hover"
            onClick={() => setShowImport(true)}
            title="从数据空间导入已注册的数据集"
          >
            <FolderInput className="h-3 w-3" />
            导入
          </Button>
          <Button
            variant="outline" size="sm"
            className="flex-1 text-[11px] tracking-wider h-7 border-maia-border text-maia-text-secondary hover:bg-maia-sidebar-hover"
            onClick={() => setShowOpenDir(true)}
            title="打开本地目录"
          >
            <FolderOpen className="h-3 w-3" />
            打开
          </Button>
        </div>
        {root?.children && root.children.length > 0 && (
          <Button
            variant="ghost" size="sm"
            className="w-full text-[11px] tracking-wider h-6 text-maia-text-muted hover:text-maia-danger hover:bg-red-50"
            onClick={handleClear}
          >
            <Trash2 className="h-3 w-3" />
            清空工作区
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
          <p className="text-[11px] text-maia-text-muted text-center py-10 tracking-wide">
            工作区间为空
            <br />
            <span className="text-[10px]">点击"上传数据"添加文件</span>
          </p>
        )}
      </div>

      {showImport && (
        <DatasetImportDialog
          onImport={handleImportDataset}
          onClose={() => setShowImport(false)}
        />
      )}

      {showOpenDir && (
        <OpenDirectoryDialog
          onOpen={handleOpenDirectory}
          onClose={() => setShowOpenDir(false)}
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
              <p className="text-[11px] text-maia-text-muted mt-1.5 break-all font-mono">{deleteTarget.path}</p>
              <p className="text-[11px] text-maia-text-muted mt-2 flex items-center gap-1">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                此操作将从文件系统中永久删除该文件，且无法恢复。
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-maia-border bg-maia-bg/50">
              <Button variant="outline" size="sm" onClick={() => setDeleteTarget(null)} disabled={deleting} className="text-[11px] h-7">取消</Button>
              <Button variant="danger" size="sm" onClick={handleDelete} disabled={deleting} className="text-[11px] h-7">
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
          'flex items-center gap-1 py-[3px] px-1.5 rounded cursor-pointer text-[12px] tracking-wide select-none',
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
          <span className="text-[10px] text-maia-text-muted shrink-0 tracking-tight">
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

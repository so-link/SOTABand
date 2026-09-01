import { useState, useRef } from 'react'
import {
  ChevronRight,
  ChevronDown,
  File,
  Folder,
  Search,
  Upload,
  FolderInput,
  Trash2,
  MoreVertical,
  Activity,
  Image,
  Table,
  FileText,
  Box,
  Archive,
  FileOutput,
} from 'lucide-react'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { useResourceStore } from '@/stores/resource-store'
import { useChatStore } from '@/stores/chat-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { setWorkspaceFileDragData } from '@/lib/dnd'
import { DatasetImportDialog } from './DatasetImportDialog'
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
  const addAttachment = useChatStore((s) => s.addAttachment)
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
    // 目录不是有效附件，拒绝拖起（否则拖到输入框会静默无反应）
    if (node.type !== 'file') return
    setWorkspaceFileDragData(e.dataTransfer, {
      id: node.id,
      name: node.name,
      path: node.path,
      size: node.size || 0,
      format: node.format || '',
    })
  }

  const [showImport, setShowImport] = useState(false)

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
    if (node.type === 'file') {
      addAttachment({
        id: node.id,
        fileName: node.name,
        filePath: node.path,
        fileSize: node.size || 0,
        format: node.format || 'unknown',
      })
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
}

function FileTreeItem({
  node,
  depth,
  selectedFile,
  onToggle,
  onSelect,
  onDoubleClick,
  onDragStart,
}: FileTreeItemProps) {
  const isSelected = selectedFile?.id === node.id
  const isDir = node.type === 'directory'
  const Icon = FILE_ICONS[node.category] || File

  return (
    <div>
      <div
        draggable={!isDir}
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

        {isSelected && (
          <MoreVertical className="h-3 w-3 shrink-0 text-maia-text-muted" />
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

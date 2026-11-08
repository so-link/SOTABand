import { useMemo, useState } from 'react'
import { X, Folder, File as FileIcon, Search } from 'lucide-react'
import { useFileTreeStore } from '@/stores/file-tree-store'
import type { FileTreeNode } from '@/types/workspace'

interface Props {
  /** 是否只选择文件（默认 true） */
  fileOnly?: boolean
  onSelect: (path: string) => void
  onClose: () => void
}

/** 收集工作区文件树中的所有文件节点（含绝对路径）。 */
function collectFiles(node: FileTreeNode | null, out: FileTreeNode[] = []): FileTreeNode[] {
  if (!node) return out
  if (node.type === 'file') out.push(node)
  for (const c of node.children || []) collectFiles(c, out)
  return out
}

/** 工作区文件选择器：从工作区间文件树中选择一个文件，返回其绝对路径。 */
export function WorkspaceFilePicker({ onSelect, onClose }: Props) {
  const root = useFileTreeStore((s) => s.root)
  const files = useMemo(() => collectFiles(root), [root])
  const [keyword, setKeyword] = useState('')

  const filtered = useMemo(() => {
    if (!keyword.trim()) return files
    const k = keyword.toLowerCase()
    return files.filter((f) => f.name.toLowerCase().includes(k) || f.path.toLowerCase().includes(k))
  }, [files, keyword])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[560px] max-h-[70vh] flex flex-col bg-maia-surface rounded-lg border border-maia-border shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-maia-border">
          <span className="text-[13px] font-semibold text-maia-text-heading">选择工作区文件</span>
          <button onClick={onClose} className="p-1 rounded hover:bg-maia-sidebar-hover">
            <X className="h-4 w-4 text-maia-text-muted" />
          </button>
        </div>

        <div className="px-4 py-2 border-b border-maia-border">
          <div className="flex items-center gap-2 bg-maia-bg rounded border border-maia-border px-2 py-1">
            <Search className="h-3.5 w-3.5 text-maia-text-muted" />
            <input
              autoFocus
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索文件名…"
              className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-maia-text-muted"
            />
          </div>
        </div>

        <div className="flex-1 overflow-auto px-2 py-2">
          {filtered.length === 0 ? (
            <div className="text-center text-[12px] text-maia-text-muted py-8">
              {files.length === 0
                ? '工作区暂无文件，请先在左侧「工作区间」上传 / 导入文件'
                : '没有匹配的文件'}
            </div>
          ) : (
            filtered.map((f) => (
              <button
                key={f.id}
                onClick={() => { onSelect(f.path); onClose() }}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-left hover:bg-maia-sidebar-hover group"
                title={f.path}
              >
                <FileIcon className="h-3.5 w-3.5 text-maia-text-muted shrink-0" />
                <span className="text-[12px] text-maia-text truncate">{f.name}</span>
                <span className="ml-auto text-[10px] text-maia-text-muted truncate max-w-[220px] opacity-0 group-hover:opacity-100">
                  {f.path}
                </span>
              </button>
            ))
          )}
        </div>

        <div className="px-4 py-2 border-t border-maia-border flex items-center gap-2 text-[11px] text-maia-text-muted">
          <Folder className="h-3.5 w-3.5" />
          共 {files.length} 个文件
        </div>
      </div>
    </div>
  )
}

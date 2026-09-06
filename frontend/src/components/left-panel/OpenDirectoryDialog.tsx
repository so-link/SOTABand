import { useState, useEffect } from 'react'
import {
  X, FolderOpen, Loader2, AlertCircle, Folder, FolderUp,
  ChevronRight, HardDrive, File, Home,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { FileTreeNode } from '@/types/workspace'

const BASE_URL = ''

interface Subdir { name: string; path: string }
interface DirFile { name: string; path: string; format: string; size: number }

interface Props {
  onOpen: (node: FileTreeNode) => void
  onClose: () => void
}

export function OpenDirectoryDialog({ onOpen, onClose }: Props) {
  const [currentPath, setCurrentPath] = useState('')
  const [subdirs, setSubdirs] = useState<Subdir[]>([])
  const [files, setFiles] = useState<DirFile[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [opening, setOpening] = useState(false)

  // 加载指定目录
  const loadDir = async (path: string) => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${BASE_URL}/api/file/browse-directory?path=${encodeURIComponent(path)}`)
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setCurrentPath(data.current)
      setSubdirs(data.subdirs || [])
      setFiles(data.files || [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // 初始加载根目录
  useEffect(() => {
    loadDir('')
  }, [])

  // 进入子目录
  const enterDir = (path: string) => loadDir(path)

  // 返回上级
  const goUp = async () => {
    const res = await fetch(`${BASE_URL}/api/file/browse-directory?path=${encodeURIComponent(currentPath)}`)
    if (!res.ok) return
    const data = await res.json()
    if (data.parent) loadDir(data.parent)
  }

  // 打开当前目录（扫描成树结构）
  const handleOpen = async () => {
    if (!currentPath) return
    setOpening(true)
    setError('')
    try {
      const res = await fetch(`${BASE_URL}/api/file/scan-directory?path=${encodeURIComponent(currentPath)}`)
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      const data = await res.json()
      onOpen(data.root as FileTreeNode)
    } catch (e) {
      setError(String(e))
    } finally {
      setOpening(false)
    }
  }

  // 面包屑：根据路径生成可点击的层级
  const crumbs = currentPath ? currentPath.split('/').filter(Boolean) : []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onClose}>
      <div className="bg-maia-surface rounded-xl shadow-xl w-[560px] max-h-[600px] flex flex-col" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-maia-border">
          <div className="flex items-center gap-2">
            <FolderOpen className="h-4 w-4 text-amber-500" />
            <span className="text-sm font-semibold text-maia-text-heading">打开本地目录</span>
          </div>
          <button onClick={onClose} className="text-maia-text-muted hover:text-maia-text"><X className="h-4 w-4" /></button>
        </div>

        {/* 路径导航栏 */}
        <div className="px-4 py-2 border-b border-maia-border bg-maia-bg/50">
          <div className="flex items-center gap-1 text-[12px]">
            <button
              onClick={goUp}
              disabled={!currentPath || crumbs.length === 0}
              className="p-1 rounded hover:bg-maia-sidebar-hover text-maia-text-secondary disabled:opacity-30"
              title="返回上级"
            >
              <FolderUp className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => loadDir('')}
              className="p-1 rounded hover:bg-maia-sidebar-hover text-maia-text-secondary"
              title="回到根目录"
            >
              <Home className="h-3.5 w-3.5" />
            </button>
            <span className="text-maia-text-muted mx-1">/</span>
            {crumbs.map((c, i) => (
              <span key={i} className="flex items-center gap-1">
                <button
                  onClick={() => loadDir('/' + crumbs.slice(0, i + 1).join('/'))}
                  className="hover:text-maia-accent text-maia-text-secondary truncate max-w-[140px]"
                >
                  {c}
                </button>
                {i < crumbs.length - 1 && <span className="text-maia-text-muted">/</span>}
              </span>
            ))}
          </div>
        </div>

        {/* 目录内容 */}
        <div className="flex-1 min-h-0 overflow-auto">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-sm text-maia-text-muted gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />加载中...
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 px-4 py-6 text-xs text-maia-danger">
              <AlertCircle className="h-4 w-4 shrink-0" />{error}
            </div>
          ) : (
            <div className="py-1">
              {subdirs.length === 0 && files.length === 0 && (
                <div className="flex flex-col items-center justify-center py-12 text-sm text-maia-text-muted gap-1">
                  <Folder className="h-6 w-6 opacity-20" />
                  空目录
                </div>
              )}
              {/* 子目录 */}
              {subdirs.map((d) => (
                <button
                  key={d.path}
                  onClick={() => enterDir(d.path)}
                  onDoubleClick={() => enterDir(d.path)}
                  className="w-full flex items-center gap-2.5 px-3 py-2 hover:bg-maia-bg transition-colors text-left"
                >
                  <Folder className="h-4 w-4 text-amber-500 shrink-0" />
                  <span className="flex-1 truncate text-[13px] text-maia-text-heading">{d.name}</span>
                  <ChevronRight className="h-3.5 w-3.5 text-maia-text-muted shrink-0" />
                </button>
              ))}
              {/* 文件 */}
              {files.map((f) => (
                <div
                  key={f.path}
                  className="flex items-center gap-2.5 px-3 py-1.5 text-left opacity-60"
                >
                  <File className="h-4 w-4 text-maia-text-muted shrink-0" />
                  <span className="flex-1 truncate text-[12px] text-maia-text-secondary">{f.name}</span>
                  <span className="text-[10px] text-maia-text-muted shrink-0">{f.format}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-2.5 border-t border-maia-border bg-maia-bg/50">
          <div className="flex items-center gap-1.5 text-[11px] text-maia-text-muted min-w-0">
            <HardDrive className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate font-mono">{currentPath || '/'}</span>
          </div>
          <div className="flex gap-2 shrink-0">
            <Button variant="outline" size="sm" onClick={onClose} className="text-[11px] h-7">取消</Button>
            <Button size="sm" onClick={handleOpen} disabled={!currentPath || opening} className="text-[11px] h-7">
              {opening ? <><Loader2 className="h-3 w-3 animate-spin" />打开中...</> : '打开此目录'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

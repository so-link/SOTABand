import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Database, X, Search, Loader2, ChevronDown, ChevronRight, Folder, File } from 'lucide-react'
import { dataApi } from '@/services/api/data'

export interface DatasetEntry {
  id: string
  name: string
  data_path: string
  formats?: string[]
  file_count?: number
}

export interface DataFile {
  name: string
  path: string
  format: string
  size: number
}

/** 数据空间文件选择器（数据集文件，选中值为 data_path）。
 *
 *  与 FileSelect（「我的文件」，file_id）并存：数据集文件走 data 接口体系
 *  （/api/data/{id}/raw），工具执行时直接拿路径；「我的文件」走 file_id，
 *  由平台解析回路径。 */
export function DataFilePicker({ paramName, onSelect, onClose }: {
  paramName: string
  onSelect: (fullPath: string) => void
  onClose: () => void
}) {
  const [datasets, setDatasets] = useState<DatasetEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedId, setExpandedId] = useState<string>('')
  const [files, setFiles] = useState<Record<string, DataFile[]>>({})
  const [loadingFiles, setLoadingFiles] = useState<string>('')
  const [query, setQuery] = useState('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await dataApi.list()
        if (!cancelled) setDatasets((res.datasets as unknown as DatasetEntry[]) || [])
      } catch (e) {
        if (!cancelled) setError(String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const toggleDataset = async (ds: DatasetEntry) => {
    if (expandedId === ds.id) { setExpandedId(''); return }
    setExpandedId(ds.id)
    if (files[ds.id]) return
    setLoadingFiles(ds.id)
    try {
      const res = await dataApi.listFiles(ds.id)
      setFiles((prev) => ({ ...prev, [ds.id]: res.files }))
    } catch {
      setFiles((prev) => ({ ...prev, [ds.id]: [] }))
    } finally {
      setLoadingFiles('')
    }
  }

  const filtered = query.trim()
    ? datasets.filter((d) => d.name.toLowerCase().includes(query.toLowerCase()) || d.id.toLowerCase().includes(query.toLowerCase()))
    : datasets

  return createPortal(
    <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[520px] max-h-[70vh] flex flex-col rounded-lg border border-maia-border bg-maia-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-maia-border shrink-0">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-blue-500" />
            <span className="text-sm font-semibold text-maia-text-heading">从数据空间选择文件</span>
          </div>
          <button onClick={onClose} className="text-maia-text-muted hover:text-maia-text"><X className="h-4 w-4" /></button>
        </div>

        {/* 参数名提示 + 搜索 */}
        <div className="px-4 pt-2.5 pb-1.5 space-y-2 shrink-0">
          <div className="text-[12px] text-maia-text-secondary">
            为参数 <span className="font-mono text-maia-accent">{paramName}</span> 选择文件
          </div>
          <div className="relative">
            <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-maia-text-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索数据集..."
              className="w-full h-7 pl-7 pr-2 rounded border border-maia-border bg-maia-bg text-[12px] outline-none focus:border-maia-accent"
            />
          </div>
        </div>

        {/* 内容 */}
        <div className="flex-1 min-h-0 overflow-auto px-2 py-2">
          {loading && <div className="flex items-center gap-1.5 px-2 py-4 text-[13px] text-maia-text-muted"><Loader2 className="h-3 w-3 animate-spin" />加载数据集...</div>}
          {error && <div className="px-2 py-4 text-[13px] text-maia-danger">{error}</div>}
          {!loading && !error && filtered.length === 0 && <div className="px-2 py-4 text-[13px] text-maia-text-muted">暂无数据集</div>}

          {!loading && !error && filtered.map((ds) => {
            const isExpanded = expandedId === ds.id
            const dsFiles = files[ds.id]
            const isLoadingFiles = loadingFiles === ds.id
            return (
              <div key={ds.id} className="mb-0.5">
                {/* 数据集行 */}
                <div className="group flex items-center gap-1.5 rounded hover:bg-maia-sidebar-hover">
                  <button
                    onClick={() => toggleDataset(ds)}
                    className="flex-1 min-w-0 flex items-center gap-1.5 px-2 py-1.5 text-left"
                  >
                    {isExpanded ? <ChevronDown className="h-3 w-3 text-maia-text-muted shrink-0" /> : <ChevronRight className="h-3 w-3 text-maia-text-muted shrink-0" />}
                    <Folder className="h-3.5 w-3.5 text-blue-500 shrink-0" />
                    <span className="flex-1 truncate text-[13px] text-maia-text">{ds.name}</span>
                    <span className="text-[12px] text-maia-text-muted shrink-0">{ds.file_count ?? (dsFiles?.length ?? '')} 文件</span>
                  </button>
                  {ds.data_path && (
                    <button
                      onClick={() => onSelect(ds.data_path)}
                      className="shrink-0 mr-1 px-1.5 py-0.5 rounded text-[12px] text-blue-500 bg-blue-500/10 hover:bg-blue-500/20"
                      title={`选择整个目录: ${ds.data_path}`}
                    >选目录</button>
                  )}
                </div>

                {/* 文件列表 */}
                {isExpanded && (
                  <div className="ml-4 pl-2 border-l border-maia-border">
                    {isLoadingFiles && <div className="flex items-center gap-1.5 px-2 py-1.5 text-[12px] text-maia-text-muted"><Loader2 className="h-3 w-3 animate-spin" />加载文件...</div>}
                    {!isLoadingFiles && dsFiles && dsFiles.length === 0 && <div className="px-2 py-1.5 text-[12px] text-maia-text-muted">无文件</div>}
                    {!isLoadingFiles && dsFiles?.map((f) => {
                      const fullPath = ds.data_path ? `${ds.data_path}/${f.path}` : f.path
                      return (
                        <button
                          key={f.path}
                          onClick={() => onSelect(fullPath)}
                          className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded hover:bg-maia-accent/10 text-left group"
                          title={fullPath}
                        >
                          <File className="h-3.5 w-3.5 text-maia-text-muted shrink-0" />
                          <span className="flex-1 truncate text-[12px] font-mono text-maia-text-secondary group-hover:text-maia-accent">{f.name}</span>
                          <span className="text-[12px] text-maia-text-muted shrink-0">{f.format}</span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>,
    document.body,
  )
}

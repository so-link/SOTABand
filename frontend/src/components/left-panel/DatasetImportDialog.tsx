import { useState, useEffect } from 'react'
import { X, Search, Database, Check } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { FileTreeNode } from '@/types/workspace'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

interface DatasetItem { id: string; name: string; data_path: string; file_count: number; formats: string[]; total_size: number }

interface Props {
  onImport: (dsNode: FileTreeNode) => void
  onClose: () => void
}

export function DatasetImportDialog({ onImport, onClose }: Props) {
  const [datasets, setDatasets] = useState<DatasetItem[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    fetch(`${BASE_URL}/api/data/list`)
      .then(r => r.json())
      .then(d => setDatasets(d.datasets || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const filtered = query.trim()
    ? datasets.filter(d =>
        d.name.toLowerCase().includes(query.toLowerCase()) ||
        d.id.toLowerCase().includes(query.toLowerCase())
      )
    : datasets

  const handleImport = async () => {
    if (!selected) return
    setImporting(true)
    try {
      const ds = datasets.find(d => d.id === selected)
      const dsPath = ds?.data_path || ''
      const filesRes = await fetch(`${BASE_URL}/api/data/${selected}/files`)
      const fileData = filesRes.ok ? await filesRes.json() : {files:[]}
      const files = (fileData.files as Array<Record<string, unknown>>) || []

      const dsNode: FileTreeNode = {
        id: ds?.id || selected,
        name: ds?.name || selected,
        type: 'directory', category: 'folder',
        path: dsPath,
        expanded: true,
        children: files.map((f: Record<string, unknown>) => ({
          id: `ds-${selected}-${f.name}`,
          name: f.name as string,
          type: 'file' as const, category: 'unknown' as const,
          path: dsPath ? `${dsPath}/${f.name}` : (f.name as string),
          format: (f.format as string) || '',
          size: (f.size as number) || 0,
        })),
      }
      onImport(dsNode)
    } catch { /* ignore */ }
    setImporting(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-[440px] max-h-[500px] flex flex-col" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-maia-border">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-blue-500" />
            <span className="text-sm font-semibold text-maia-text-heading">导入数据集</span>
            <Badge variant="default" className="text-[10px]">{datasets.length}</Badge>
          </div>
          <button onClick={onClose} className="text-maia-text-muted hover:text-maia-text"><X className="h-4 w-4" /></button>
        </div>

        {/* Search */}
        <div className="px-4 py-2 border-b border-maia-border">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-maia-text-muted" />
            <Input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="搜索数据集..."
              className="pl-7 h-8 text-[12px]"
            />
          </div>
        </div>

        {/* List */}
        <div className="flex-1 min-h-0 overflow-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12 text-sm text-maia-text-muted">加载中...</div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-sm text-maia-text-muted gap-1">
              <Database className="h-6 w-6 opacity-20" />
              {query ? '无匹配数据集' : '暂无已注册数据集'}
            </div>
          ) : (
            <div className="py-1">
              {filtered.map(ds => (
                <button
                  key={ds.id}
                  onClick={() => setSelected(ds.id)}
                  onDoubleClick={handleImport}
                  className={`w-full text-left px-4 py-2.5 flex items-center gap-3 hover:bg-maia-bg transition-colors ${
                    selected === ds.id ? 'bg-blue-50 border-l-2 border-l-blue-500' : 'border-l-2 border-l-transparent'
                  }`}
                >
                  <div className={`h-8 w-8 rounded-lg flex items-center justify-center shrink-0 ${
                    selected === ds.id ? 'bg-blue-100 text-blue-600' : 'bg-maia-bg text-maia-text-muted'
                  }`}>
                    <Database className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium text-maia-text-heading truncate">{ds.name}</div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[10px] text-maia-text-muted font-mono">{ds.id}</span>
                      <span className="text-[10px] text-maia-text-muted">{ds.file_count || 0} 文件</span>
                      {ds.total_size > 0 && (
                        <span className="text-[10px] text-maia-text-muted">
                          {ds.total_size > 1048576 ? `${(ds.total_size / 1048576).toFixed(1)}MB` : `${(ds.total_size / 1024).toFixed(0)}KB`}
                        </span>
                      )}
                    </div>
                    {(ds.formats || []).length > 0 && (
                      <div className="flex gap-1 mt-1">
                        {(ds.formats || []).slice(0, 3).map(f => (
                          <Badge key={f} variant="default" className="text-[9px]">{f.toUpperCase()}</Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  {selected === ds.id && <Check className="h-4 w-4 text-blue-500 shrink-0" />}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-2.5 border-t border-maia-border bg-maia-bg/50">
          <span className="text-[11px] text-maia-text-muted">
            {selected ? `已选择: ${datasets.find(d => d.id === selected)?.name}` : '点击选择数据集'}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose} className="text-[11px] h-7">取消</Button>
            <Button size="sm" onClick={handleImport} disabled={!selected || importing} className="text-[11px] h-7">
              {importing ? '导入中...' : '导入'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

import { useState, useEffect } from 'react'
import { Loader2, ChevronLeft, ChevronRight, Table2, ArrowDownUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardBody } from '@/components/ui/card'

const BASE_URL = ''
const TABLE_PAGE_OPTIONS = [8, 16, 32, 64, 100]
/** 服务端一次读取的最大行数，兼顾分页与倒序查看 */
const ROW_LIMIT = 2000

interface TablePayload {
  headers: string[]
  rows: string[][]
  truncated: boolean
}

interface DataTablePreviewProps {
  datasetId: string
  /** 可表格化预览的文件（csv/tsv/xlsx/xls/parquet） */
  tableFiles: Array<Record<string, unknown>>
  /** 当前预览的文件在 tableFiles 中的下标（受控） */
  activeIndex?: number
  onActiveChange?: (idx: number) => void
}

/**
 * 表格预览：CSV / Excel / Parquet 统一走后端 /table 接口，
 * 保证不同格式的中栏布局完全一致。
 */
export function DataTablePreview({ datasetId, tableFiles, activeIndex, onActiveChange }: DataTablePreviewProps) {
  const [data, setData] = useState<TablePayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(16)
  // 行号倒序：从最后一行往前看，行号仍显示原始行号
  const [desc, setDesc] = useState(false)

  const active = Math.min(activeIndex ?? 0, Math.max(0, tableFiles.length - 1))
  const activeFile = tableFiles[active]
  const activeName = (activeFile?.name as string) || ''

  useEffect(() => {
    if (!activeName) return
    setLoading(true)
    setError('')
    fetch(`${BASE_URL}/api/data/${encodeURIComponent(datasetId)}/table?path=${encodeURIComponent(activeName)}&limit=${ROW_LIMIT}`)
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}))
          throw new Error(body.detail || `HTTP ${r.status}`)
        }
        return r.json()
      })
      .then((d) => {
        const rows = (d.rows as Array<Array<string | null>>) || []
        setData({
          headers: (d.headers as string[]) || [],
          rows: rows.map((row) => row.map((v) => (v === null || v === undefined ? '' : String(v)))),
          truncated: rows.length >= ROW_LIMIT,
        })
        setLoading(false)
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e))
        setData(null)
        setLoading(false)
      })
  }, [datasetId, activeName])

  if (loading) {
    return (
      <Card className="border-maia-border">
        <CardBody>
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-maia-text-muted" />
          </div>
        </CardBody>
      </Card>
    )
  }

  if (error || !data) {
    return (
      <Card className="border-maia-border">
        <CardBody>
          <div className="text-[12px] text-maia-text-muted py-4 text-center">
            无法解析表格文件{error ? `：${error}` : ''}
          </div>
        </CardBody>
      </Card>
    )
  }

  const { headers, rows, truncated } = data
  const ordered = desc ? [...rows].reverse() : rows
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageRows = ordered.slice((safePage - 1) * pageSize, safePage * pageSize)

  return (
    <Card className="border-maia-border">
      <CardBody>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5 min-w-0">
            <Table2 className="h-3.5 w-3.5 text-maia-success shrink-0" />
            <span className="text-[13px] font-medium text-maia-text-secondary">表格预览</span>
            <span className="text-[12px] text-maia-text-muted shrink-0">
              {rows.length} 条记录{truncated ? `（最多预览 ${ROW_LIMIT} 条）` : ''}
            </span>
            <span className="text-[12px] text-maia-text-muted truncate min-w-0" title={activeName}>{activeName}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => { setDesc(v => !v); setPage(1) }}
              className={`inline-flex items-center gap-1 h-6 px-1.5 rounded border text-[12px] transition-colors ${
                desc
                  ? 'border-maia-accent/50 text-maia-accent bg-maia-accent/10'
                  : 'border-maia-border text-maia-text-muted hover:text-maia-text-secondary hover:bg-maia-sidebar-hover'
              }`}
              title="切换行号正序 / 倒序"
            >
              <ArrowDownUp className="h-3 w-3" />
              {desc ? '倒序' : '正序'}
            </button>
            <span className="text-[12px] text-maia-text-muted">每页</span>
            <select value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
              className="h-6 rounded border border-maia-border bg-maia-bg text-[12px] text-maia-text px-1 outline-none focus:border-maia-accent/40">
              {TABLE_PAGE_OPTIONS.map(n => (<option key={n} value={n}>{n} 条</option>))}
            </select>
            <div className="flex items-center gap-1 ml-2">
              <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage <= 1}
                onClick={() => setPage(p => Math.max(1, p - 1))}><ChevronLeft className="h-3 w-3" /></Button>
              <span className="text-[12px] text-maia-text-muted min-w-[40px] text-center">{safePage}/{totalPages}</span>
              <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage >= totalPages}
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}><ChevronRight className="h-3 w-3" /></Button>
            </div>
          </div>
        </div>

        {/* 纵向由中栏内容区统一滚动，这里只保留横向滚动，避免出现两层滚动条 */}
        <div className="overflow-x-auto border border-maia-border rounded">
          <table className="w-full text-[13px] border-collapse">
            <thead className="sticky top-0 z-10">
              <tr className="bg-maia-bg">
                <th className="text-left px-2.5 py-2 text-maia-text-muted font-medium border-b border-maia-border whitespace-nowrap w-10">#</th>
                {headers.map((h, i) => (
                  <th key={i} className="text-left px-2 py-1.5 text-maia-text-muted font-medium border-b border-maia-border whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row, ri) => (
                <tr key={ri} className="hover:bg-maia-bg/50">
                  <td className="px-2.5 py-1.5 text-maia-text-muted border-b border-maia-border/50 tnum">
                    {desc ? rows.length - ((safePage - 1) * pageSize + ri) : (safePage - 1) * pageSize + ri + 1}
                  </td>
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-2.5 py-1.5 text-maia-text border-b border-maia-border/50 max-w-[300px] truncate" title={cell}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  )
}

import { useState, useEffect } from 'react'
import { Loader2, Table2 } from 'lucide-react'
import { TableView } from './TableView'

const BASE_URL = ''
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
 * 数据集表格预览：CSV / Excel / Parquet 统一走后端 /table 接口，
 * 保证不同格式的中栏布局完全一致；表格展示复用 TableView。
 */
export function DataTablePreview({ datasetId, tableFiles, activeIndex }: DataTablePreviewProps) {
  const [data, setData] = useState<TablePayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const active = Math.min(activeIndex ?? 0, Math.max(0, tableFiles.length - 1))
  const activeFile = tableFiles[active]
  const activeName = (activeFile?.name as string) || ''
  const activePath = (activeFile?.path as string) || activeName
  // 规范文档为该文件登记的说明（例如 ETF 的标的名称）
  const activeDesc = (activeFile?.description as string) || ''

  useEffect(() => {
    if (!activeName) return
    setLoading(true)
    setError('')
    fetch(`${BASE_URL}/api/data/${encodeURIComponent(datasetId)}/table?path=${encodeURIComponent(activePath)}&limit=${ROW_LIMIT}`)
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
  }, [datasetId, activePath])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-5 w-5 animate-spin text-maia-text-muted" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="text-[13px] text-maia-text-muted py-8 text-center">
        无法解析表格文件{error ? `：${error}` : ''}
      </div>
    )
  }

  const { headers, rows, truncated } = data

  // 与「我的文件」CSV 预览同款无框样式：直接渲染 TableView，不套 Card
  return (
    <TableView
      headers={headers}
      rows={rows}
      heading={
        <>
          <Table2 className="h-3.5 w-3.5 text-maia-success shrink-0" />
          <span className="text-[13px] font-medium text-maia-text-secondary shrink-0">表格预览</span>
          <span className="text-[12px] text-maia-text-muted shrink-0">
            {rows.length} 条记录{truncated ? `（最多预览 ${ROW_LIMIT} 条）` : ''}
          </span>
          <span className="text-[12px] text-maia-text-muted truncate min-w-0" title={activeDesc || activeName}>
            {activeDesc ? `${activeName} · ${activeDesc}` : activeName}
          </span>
        </>
      }
    />
  )
}

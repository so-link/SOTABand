import { useState, useEffect } from 'react'
import { Loader2, ChevronLeft, ChevronRight, Table2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardBody } from '@/components/ui/card'

const BASE_URL = ''
const TABLE_PAGE_OPTIONS = [8, 16, 32, 64, 100]

/** 原生 CSV 行解析 */
function parseCSVLine(line: string): string[] {
  const result: string[] = []
  let current = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < line.length && line[i + 1] === '"') { current += '"'; i++ }
        else { inQuotes = false }
      } else { current += ch }
    } else {
      if (ch === '"') { inQuotes = true }
      else if (ch === ',') { result.push(current.trim()); current = '' }
      else { current += ch }
    }
  }
  result.push(current.trim())
  return result
}

interface CsvTablePreviewProps {
  datasetPath: string
  csvFiles: Array<Record<string, unknown>>
}

export function CsvTablePreview({ datasetPath, csvFiles }: CsvTablePreviewProps) {
  const [csvData, setCsvData] = useState<{ headers: string[]; rows: string[][] } | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(16)

  useEffect(() => {
    if (csvFiles.length === 0) { setCsvData(null); return }
    const csvFile = csvFiles[0]
    const filePath = datasetPath ? `${datasetPath}/${csvFile.name}` : ''
    if (!filePath) return
    setLoading(true)
    fetch(`${BASE_URL}/api/file/download?path=${encodeURIComponent(filePath)}`)
      .then(r => r.text())
      .then(text => {
        const lines = text.split('\n').filter(l => l.trim())
        if (lines.length > 0) {
          const headers = parseCSVLine(lines[0])
          const rows = lines.slice(1).map(parseCSVLine)
          setCsvData({ headers, rows })
        }
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

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

  if (!csvData) {
    return (
      <Card className="border-maia-border">
        <CardBody>
          <div className="text-[11px] text-maia-text-muted py-4 text-center">无法解析 CSV 文件</div>
        </CardBody>
      </Card>
    )
  }

  const { headers, rows } = csvData
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageRows = rows.slice((safePage - 1) * pageSize, safePage * pageSize)

  return (
    <Card className="border-maia-border">
      <CardBody>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <Table2 className="h-3.5 w-3.5 text-green-400" />
            <span className="text-xs font-medium text-maia-text-secondary tracking-wide">
              CSV 表格预览 — {csvFiles[0].name as string}（{rows.length} 条记录）
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-maia-text-muted">每页</span>
            <select value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
              className="h-6 rounded border border-maia-border bg-maia-bg text-[11px] text-maia-text px-1 outline-none focus:border-maia-accent/40">
              {TABLE_PAGE_OPTIONS.map(n => (<option key={n} value={n}>{n} 条</option>))}
            </select>
            <div className="flex items-center gap-1 ml-2">
              <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage <= 1}
                onClick={() => setPage(p => Math.max(1, p - 1))}><ChevronLeft className="h-3 w-3" /></Button>
              <span className="text-[11px] text-maia-text-muted min-w-[40px] text-center">{safePage}/{totalPages}</span>
              <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage >= totalPages}
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}><ChevronRight className="h-3 w-3" /></Button>
            </div>
          </div>
        </div>

        <div className="overflow-auto max-h-[500px] border border-maia-border rounded">
          <table className="w-full text-[11px] border-collapse">
            <thead className="sticky top-0 z-10">
              <tr className="bg-maia-bg">
                <th className="text-left px-2 py-1.5 text-maia-text-muted font-medium border-b border-maia-border whitespace-nowrap w-10 text-[10px]">#</th>
                {headers.map((h, i) => (
                  <th key={i} className="text-left px-2 py-1.5 text-maia-text-muted font-medium border-b border-maia-border whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row, ri) => (
                <tr key={ri} className="hover:bg-maia-bg/50">
                  <td className="px-2 py-1 text-maia-text-muted border-b border-maia-border/50 text-[10px]">{(safePage - 1) * pageSize + ri + 1}</td>
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-2 py-1 text-maia-text border-b border-maia-border/50 max-w-[300px] truncate" title={cell}>{cell}</td>
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

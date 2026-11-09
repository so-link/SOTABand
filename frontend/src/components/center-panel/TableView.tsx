import { useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, ArrowDownUp } from 'lucide-react'
import { Button } from '@/components/ui/button'

const TABLE_PAGE_OPTIONS = [8, 16, 32, 64, 100]

/**
 * 表格预览的共用视图：正序/倒序切换 + 每页条数 + 翻页 + 行号 + 表格样式。
 *
 * 数据集预览（DataTablePreview，后端 /table 解析 csv/tsv/xlsx/parquet）与
 * 「我的文件」CSV 预览（FilePreviewView，前端解析原始文本）取数方式不同，
 * 但展示需求完全一致，过去各自抄了一份分页与表格代码，倒序只有前者有。
 * 这里只收拢**展示与交互**，取数仍由调用方负责。
 */
export function TableView({
  headers,
  rows,
  heading,
}: {
  headers: string[]
  rows: string[][]
  /** 标题行左侧内容（图标 + 名称等），由调用方决定 */
  heading: ReactNode
}) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(16)
  // 行号倒序：从最后一行往前看，行号仍显示原始行号
  const [desc, setDesc] = useState(false)

  const ordered = desc ? [...rows].reverse() : rows
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageRows = ordered.slice((safePage - 1) * pageSize, safePage * pageSize)

  return (
    <>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-1.5 min-w-0">{heading}</div>
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

      {/* 纵向由外层内容区统一滚动，这里只保留横向滚动，避免出现两层滚动条 */}
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
    </>
  )
}

import { useState } from 'react'
import { Pencil, Check, X, Loader2, Table2, Zap } from 'lucide-react'
import type { SpecTable } from '@/types/spec-outline'

interface Props {
  toolId: string
  table: SpecTable
  md: string
  onApplied: (updatedMd: string) => void
}

/**
 * 表格表单化编辑
 *
 * 「把并发数默认值从 8 改成 4」这类改动是**确定性的**，不需要 LLM。
 * 走 LLM 有两个问题：慢（推理模型 ~11 秒）、不精确（会顺带改同义词语序）。
 *
 * 这里直接程序化改表格单元格：毫秒级完成，且只改目标单元格。
 * 只有「补充说明」「改措辞」这类语义修改才需要走 LLM 精化。
 */
export function TableEditView({ toolId, table, md, onApplied }: Props) {
  const [editing, setEditing] = useState<{ row: number; col: number } | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // 本地乐观更新：先改本地值再落盘，保证 0 延迟的视觉反馈
  const [localRows, setLocalRows] = useState<string[][]>(table.rows)

  if (!table.has_table || !table.header?.length) return null

  const startEdit = (row: number, col: number) => {
    setEditing({ row, col })
    setDraft(localRows[row]?.[col] ?? '')
    setError('')
  }

  const cancel = () => {
    setEditing(null)
    setDraft('')
    setError('')
  }

  const save = async () => {
    if (!editing) return
    const { row, col } = editing
    const column = table.header[col]
    const oldVal = localRows[row]?.[col] ?? ''
    if (draft === oldVal) { cancel(); return }

    setBusy(true); setError('')
    // 乐观更新：立即反映到界面，不等请求
    const next = localRows.map((r, i) =>
      i === row ? r.map((c, j) => (j === col ? draft : c)) : r)
    setLocalRows(next)

    try {
      const { toolApi } = await import('@/services/api/tool')
      const res = await toolApi.updateTableCell({
        toolId,
        nodeId: table.node_id!,
        rowIndex: row,
        column,
        value: draft,
        specMd: md,
        save: false,
      })
      onApplied(res.updated_md)
      cancel()
    } catch (e) {
      // 失败回滚
      setLocalRows(table.rows)
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-3 rounded-lg border border-maia-border overflow-hidden">
      {/* 头部：说明这是零延迟的确定性编辑 */}
      <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-maia-bg/40 border-b border-maia-border">
        <Table2 className="h-3 w-3 text-maia-accent" />
        <span className="text-[10px] text-maia-text-secondary">
          {table.node_title || '参数表'}
        </span>
        <span className="flex items-center gap-0.5 text-[9px] text-maia-success">
          <Zap className="h-2.5 w-2.5" />
          点单元格直接改，即时生效
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="bg-maia-bg/30">
              {table.header.map((h, i) => (
                <th key={i} className="px-2 py-1 text-left font-medium text-maia-text-secondary whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {localRows.map((row, ri) => (
              <tr key={ri} className="border-t border-maia-border/50">
                {row.map((cell, ci) => {
                  const isEditing = editing?.row === ri && editing?.col === ci
                  return (
                    <td key={ci} className="px-2 py-1 align-top">
                      {isEditing ? (
                        <div className="flex items-center gap-1">
                          <input
                            autoFocus
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') { e.preventDefault(); void save() }
                              if (e.key === 'Escape') { e.preventDefault(); cancel() }
                            }}
                            className="w-full min-w-[60px] rounded border border-maia-accent/40 bg-maia-bg px-1 py-0.5 text-[10px] outline-none"
                          />
                          <button
                            onClick={() => void save()}
                            disabled={busy}
                            title="保存 (Enter)"
                            className="text-maia-success shrink-0"
                          >
                            {busy
                              ? <Loader2 className="h-3 w-3 animate-spin" />
                              : <Check className="h-3 w-3" />}
                          </button>
                          <button onClick={cancel} title="取消 (Esc)" className="text-maia-text-muted shrink-0">
                            <X className="h-3 w-3" />
                          </button>
                        </div>
                      ) : (
                        <div
                          onClick={() => startEdit(ri, ci)}
                          title="点击编辑"
                          className="group flex items-start gap-1 cursor-pointer hover:bg-maia-bg/40 rounded px-1 -mx-1"
                        >
                          <span className="text-maia-text break-words max-w-[260px]">
                            {cell || <span className="text-maia-text-muted">（空）</span>}
                          </span>
                          <Pencil className="h-2.5 w-2.5 text-maia-text-muted opacity-0 group-hover:opacity-100 shrink-0 mt-0.5" />
                        </div>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && (
        <div className="px-2.5 py-1 text-[10px] text-maia-danger border-t border-maia-border">
          {error}
        </div>
      )}
    </div>
  )
}

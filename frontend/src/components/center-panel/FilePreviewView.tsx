import { useState, useEffect } from 'react'
import {
  File, FileText, Image, Film, Loader2, ExternalLink, Download,
  FolderOpen, Table2, ChevronLeft, ChevronRight,
} from 'lucide-react'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

const BASE_URL = ''

const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico'])
const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv', 'm4v'])
const MD_EXTENSIONS = new Set(['md', 'markdown'])
const CSV_EXTENSIONS = new Set(['csv'])
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

function getExtension(name: string): string {
  return (name.split('.').pop() || '').toLowerCase()
}

/** 轻量 Markdown → HTML 渲染器（零依赖） */
function renderMarkdown(md: string): string {
  let html = md
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre class="bg-maia-bg border border-maia-border rounded p-3 my-2 overflow-auto text-[11px] leading-relaxed"><code>${code.trim()}</code></pre>`
  )
  html = html.replace(/`([^`]+)`/g, '<code class="bg-maia-bg text-green-400 px-1 py-0.5 rounded text-[11px]">$1</code>')
  html = html.replace(/^#### (.+)$/gm, '<h4 class="text-sm font-semibold text-maia-text-heading mt-4 mb-1">$1</h4>')
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold text-maia-text-heading mt-4 mb-1">$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-lg font-semibold text-maia-text-heading mt-5 mb-2 pb-1 border-b border-maia-border">$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold text-maia-text-heading mt-5 mb-3 pb-1 border-b border-maia-border">$1</h1>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-maia-text">$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  html = html.replace(/^---$/gm, '<hr class="border-maia-border my-3" />')
  html = html.replace(/^[\s]*[-*] (.+)$/gm, '<li class="text-maia-text ml-4 list-disc">$1</li>')
  html = html.replace(/^[\s]*\d+\. (.+)$/gm, '<li class="text-maia-text ml-4 list-decimal">$1</li>')
  html = html.replace(/\|(.+)\|\n\|[-| :]+\|\n((?:\|.+\|\n?)*)/g, (_, header, rows) => {
    const headers = header.split('|').map((h: string) => h.trim()).filter(Boolean)
    const ths = headers.map((h: string) => `<th class="border border-maia-border bg-maia-bg px-2 py-1 text-left text-[11px] font-medium text-maia-text-secondary">${h}</th>`).join('')
    const bodyRows = rows.trim().split('\n').map((row: string) => {
      const cells = row.split('|').map((c: string) => c.trim()).filter(Boolean)
      return `<tr>${cells.map((c: string) => `<td class="border border-maia-border px-2 py-1 text-[11px] text-maia-text">${c}</td>`).join('')}</tr>`
    }).join('')
    return `<table class="border-collapse my-2 w-full text-[11px]"><thead><tr>${ths}</tr></thead><tbody>${bodyRows}</tbody></table>`
  })
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="text-maia-accent hover:underline">$1</a>')
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="max-w-full rounded my-2" />')
  html = html.replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-maia-accent/50 pl-3 my-2 text-maia-text-muted text-[12px]">$1</blockquote>')
  const lines = html.split('\n')
  const result: string[] = []
  let paragraph: string[] = []
  const isBlock = (l: string) => /^<(h[1-4]|pre|table|hr|ul|ol|li|blockquote|img|div)/.test(l.trim())
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      if (paragraph.length > 0) { result.push(`<p class="text-maia-text text-[12px] leading-relaxed my-1">${paragraph.join(' ')}</p>`); paragraph = [] }
      continue
    }
    if (isBlock(trimmed)) {
      if (paragraph.length > 0) { result.push(`<p class="text-maia-text text-[12px] leading-relaxed my-1">${paragraph.join(' ')}</p>`); paragraph = [] }
      result.push(trimmed)
    } else { paragraph.push(trimmed) }
  }
  if (paragraph.length > 0) result.push(`<p class="text-maia-text text-[12px] leading-relaxed my-1">${paragraph.join(' ')}</p>`)
  return result.join('\n')
}

export function FilePreviewView() {
  const previewFile = useFileTreeStore((s) => s.previewFile)
  const [mdHtml, setMdHtml] = useState<string | null>(null)
  const [csvData, setCsvData] = useState<{ headers: string[]; rows: string[][] } | null>(null)
  const [loading, setLoading] = useState(false)
  const [csvPage, setCsvPage] = useState(1)
  const [csvPageSize, setCsvPageSize] = useState(16)

  const ext = previewFile ? getExtension(previewFile.name) : ''
  const isImage = IMAGE_EXTENSIONS.has(ext)
  const isVideo = VIDEO_EXTENSIONS.has(ext)
  const isMarkdown = MD_EXTENSIONS.has(ext)
  const isCsv = CSV_EXTENSIONS.has(ext)

  useEffect(() => {
    setCsvPage(1)
    if (previewFile && isMarkdown) {
      setLoading(true)
      setMdHtml(null)
      fetch(`${BASE_URL}/api/file/download?path=${encodeURIComponent(previewFile.path)}`)
        .then(r => r.text())
        .then(text => { setMdHtml(renderMarkdown(text)); setLoading(false) })
        .catch(() => setLoading(false))
    } else if (previewFile && isCsv) {
      setLoading(true)
      setCsvData(null)
      fetch(`${BASE_URL}/api/file/download?path=${encodeURIComponent(previewFile.path)}`)
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
    } else {
      setMdHtml(null)
      setCsvData(null)
    }
  }, [previewFile?.path, isMarkdown, isCsv])

  if (!previewFile) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-maia-text-muted gap-2">
        <FolderOpen className="h-10 w-10 opacity-20" />
        <p className="text-sm">双击工作区中的文件进行预览</p>
      </div>
    )
  }

  const downloadUrl = `${BASE_URL}/api/file/download?path=${encodeURIComponent(previewFile.path)}`
  const imageUrl = `${BASE_URL}/api/file/image?path=${encodeURIComponent(previewFile.path)}`

  return (
    <div className="flex flex-col h-full bg-maia-surface overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          {isImage ? <Image className="h-4 w-4 text-amber-400 shrink-0" />
            : isVideo ? <Film className="h-4 w-4 text-blue-400 shrink-0" />
            : isMarkdown ? <FileText className="h-4 w-4 text-purple-400 shrink-0" />
            : isCsv ? <Table2 className="h-4 w-4 text-green-400 shrink-0" />
            : <File className="h-4 w-4 text-maia-text-muted shrink-0" />}
          <span className="text-sm font-semibold text-maia-text-heading truncate">{previewFile.name}</span>
          <Badge variant="default" className="text-[9px] shrink-0">{ext.toUpperCase() || 'FILE'}</Badge>
          {previewFile.size != null && previewFile.size > 0 && (
            <span className="text-[11px] text-maia-text-muted shrink-0">
              {previewFile.size > 1048576 ? `${(previewFile.size / 1048576).toFixed(1)} MB` : `${(previewFile.size / 1024).toFixed(1)} KB`}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="outline" size="sm" className="text-[11px] h-7" onClick={() => window.open(downloadUrl, '_blank')}>
            <ExternalLink className="h-3 w-3" />浏览器打开
          </Button>
          <a href={downloadUrl} download={previewFile.name}>
            <Button variant="outline" size="sm" className="text-[11px] h-7">
              <Download className="h-3 w-3" />下载
            </Button>
          </a>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0">
        {isImage && (
          <div className="h-full flex items-center justify-center p-6 bg-maia-bg/30">
            <img src={imageUrl} alt={previewFile.name} className="max-w-full max-h-full object-contain rounded shadow-lg" />
          </div>
        )}

        {isVideo && (
          <div className="h-full flex items-center justify-center p-6 bg-maia-bg/30">
            <video src={downloadUrl} controls className="max-w-full max-h-full rounded shadow-lg" />
          </div>
        )}

        {isMarkdown && (
          <div className="max-w-4xl mx-auto p-6">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-5 w-5 animate-spin text-maia-text-muted" />
              </div>
            ) : mdHtml ? (
              <div className="markdown-body" dangerouslySetInnerHTML={{ __html: mdHtml }} />
            ) : (
              <div className="text-[12px] text-maia-text-muted py-8 text-center">无法加载 Markdown 内容</div>
            )}
          </div>
        )}

        {isCsv && (
          <div className="max-w-5xl mx-auto p-6">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-5 w-5 animate-spin text-maia-text-muted" />
              </div>
            ) : !csvData ? (
              <div className="text-[12px] text-maia-text-muted py-8 text-center">无法解析 CSV 文件</div>
            ) : (
              (() => {
                const { headers, rows } = csvData
                const totalPages = Math.max(1, Math.ceil(rows.length / csvPageSize))
                const safePage = Math.min(csvPage, totalPages)
                const pageRows = rows.slice((safePage - 1) * csvPageSize, safePage * csvPageSize)
                return (
                  <>
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-1.5">
                        <Table2 className="h-3.5 w-3.5 text-green-400" />
                        <span className="text-xs font-medium text-maia-text-secondary tracking-wide">
                          CSV 表格预览（{rows.length} 条记录，{headers.length} 列）
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-maia-text-muted">每页</span>
                        <select value={csvPageSize} onChange={e => { setCsvPageSize(Number(e.target.value)); setCsvPage(1) }}
                          className="h-6 rounded border border-maia-border bg-maia-bg text-[11px] text-maia-text px-1 outline-none focus:border-maia-accent/40">
                          {TABLE_PAGE_OPTIONS.map(n => (<option key={n} value={n}>{n} 条</option>))}
                        </select>
                        <div className="flex items-center gap-1 ml-2">
                          <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage <= 1}
                            onClick={() => setCsvPage(p => Math.max(1, p - 1))}><ChevronLeft className="h-3 w-3" /></Button>
                          <span className="text-[11px] text-maia-text-muted min-w-[40px] text-center">{safePage}/{totalPages}</span>
                          <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage >= totalPages}
                            onClick={() => setCsvPage(p => Math.min(totalPages, p + 1))}><ChevronRight className="h-3 w-3" /></Button>
                        </div>
                      </div>
                    </div>

                    <div className="overflow-auto max-h-[600px] border border-maia-border rounded">
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
                              <td className="px-2 py-1 text-maia-text-muted border-b border-maia-border/50 text-[10px]">{(safePage - 1) * csvPageSize + ri + 1}</td>
                              {row.map((cell, ci) => (
                                <td key={ci} className="px-2 py-1 text-maia-text border-b border-maia-border/50 max-w-[300px] truncate" title={cell}>{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )
              })()
            )}
          </div>
        )}

        {!isImage && !isVideo && !isMarkdown && !isCsv && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-maia-text-muted p-6">
            <File className="h-12 w-12 opacity-20" />
            <p className="text-sm">该文件类型暂不支持内嵌预览</p>
            <p className="text-[11px] font-mono text-maia-text-muted break-all max-w-lg text-center">{previewFile.path}</p>
            <div className="flex gap-2 mt-2">
              <Button variant="outline" size="sm" className="text-[11px]" onClick={() => window.open(downloadUrl, '_blank')}>
                <ExternalLink className="h-3 w-3" />浏览器打开
              </Button>
              <a href={downloadUrl} download={previewFile.name}>
                <Button variant="outline" size="sm" className="text-[11px]">
                  <Download className="h-3 w-3" />下载
                </Button>
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

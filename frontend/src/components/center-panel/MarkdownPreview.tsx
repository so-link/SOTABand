import { useState, useEffect } from 'react'
import { Loader2, FileText } from 'lucide-react'
import { Card, CardBody } from '@/components/ui/card'

const BASE_URL = ''

/** 轻量 Markdown → HTML 渲染器（零依赖） */
function renderMarkdown(md: string): string {
  // 转义 HTML
  let html = md
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  // 代码块 ```...```
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre class="bg-maia-bg border border-maia-border rounded p-3 my-2 overflow-auto text-[11px] leading-relaxed"><code>${code.trim()}</code></pre>`
  )

  // 行内代码 `...`
  html = html.replace(/`([^`]+)`/g, '<code class="bg-maia-bg text-green-400 px-1 py-0.5 rounded text-[11px]">$1</code>')

  // 标题
  html = html.replace(/^#### (.+)$/gm, '<h4 class="text-sm font-semibold text-maia-text-heading mt-4 mb-1">$1</h4>')
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold text-maia-text-heading mt-4 mb-1">$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-lg font-semibold text-maia-text-heading mt-5 mb-2 pb-1 border-b border-maia-border">$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold text-maia-text-heading mt-5 mb-3 pb-1 border-b border-maia-border">$1</h1>')

  // 粗体 **...** 和斜体 *...*
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-maia-text">$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // 水平线
  html = html.replace(/^---$/gm, '<hr class="border-maia-border my-3" />')

  // 无序列表 - item
  html = html.replace(/^[\s]*[-*] (.+)$/gm, '<li class="text-maia-text ml-4 list-disc">$1</li>')
  // 有序列表 1. item
  html = html.replace(/^[\s]*\d+\. (.+)$/gm, '<li class="text-maia-text ml-4 list-decimal">$1</li>')

  // 表格
  html = html.replace(/\|(.+)\|\n\|[-| :]+\|\n((?:\|.+\|\n?)*)/g, (_, header, rows) => {
    const headers = header.split('|').map((h: string) => h.trim()).filter(Boolean)
    const ths = headers.map((h: string) => `<th class="border border-maia-border bg-maia-bg px-2 py-1 text-left text-[11px] font-medium text-maia-text-secondary">${h}</th>`).join('')
    const bodyRows = rows.trim().split('\n').map((row: string) => {
      const cells = row.split('|').map((c: string) => c.trim()).filter(Boolean)
      return `<tr>${cells.map((c: string) => `<td class="border border-maia-border px-2 py-1 text-[11px] text-maia-text">${c}</td>`).join('')}</tr>`
    }).join('')
    return `<table class="border-collapse my-2 w-full text-[11px]"><thead><tr>${ths}</tr></thead><tbody>${bodyRows}</tbody></table>`
  })

  // 链接 [text](url)
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="text-maia-accent hover:underline">$1</a>')

  // 图片 ![...](url)
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="max-w-full rounded my-2" />')

  // 引用 >
  html = html.replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-maia-accent/50 pl-3 my-2 text-maia-text-muted text-[12px]">$1</blockquote>')

  // 段落（连续的非空行合并为 <p>）
  const lines = html.split('\n')
  const result: string[] = []
  let paragraph: string[] = []
  const isBlock = (l: string) => /^<(h[1-4]|pre|table|hr|ul|ol|li|blockquote|img|div)/.test(l.trim())

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      if (paragraph.length > 0) {
        result.push(`<p class="text-maia-text text-[12px] leading-relaxed my-1">${paragraph.join(' ')}</p>`)
        paragraph = []
      }
      continue
    }
    if (isBlock(trimmed)) {
      if (paragraph.length > 0) {
        result.push(`<p class="text-maia-text text-[12px] leading-relaxed my-1">${paragraph.join(' ')}</p>`)
        paragraph = []
      }
      result.push(trimmed)
    } else {
      paragraph.push(trimmed)
    }
  }
  if (paragraph.length > 0) {
    result.push(`<p class="text-maia-text text-[12px] leading-relaxed my-1">${paragraph.join(' ')}</p>`)
  }

  return result.join('\n')
}

interface MarkdownPreviewProps {
  datasetPath: string
  mdFiles: Array<Record<string, unknown>>
}

export function MarkdownPreview({ datasetPath, mdFiles }: MarkdownPreviewProps) {
  const [html, setHtml] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (mdFiles.length === 0) { setHtml(null); return }
    const mdFile = mdFiles[0]
    const filePath = datasetPath ? `${datasetPath}/${mdFile.name}` : ''
    if (!filePath) return
    setLoading(true)
    fetch(`${BASE_URL}/api/file/download?path=${encodeURIComponent(filePath)}`)
      .then(r => r.text())
      .then(text => { setHtml(renderMarkdown(text)); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (mdFiles.length === 0) return null

  return (
    <Card className="border-maia-border">
      <CardBody>
        <div className="flex items-center gap-1.5 mb-3">
          <FileText className="h-3.5 w-3.5 text-purple-400" />
          <span className="text-xs font-medium text-maia-text-secondary tracking-wide">{mdFiles[0].name as string}</span>
        </div>
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-maia-text-muted" />
          </div>
        ) : html ? (
          <div className="markdown-body" dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <div className="text-[11px] text-maia-text-muted py-4 text-center">无法加载文件</div>
        )}
      </CardBody>
    </Card>
  )
}

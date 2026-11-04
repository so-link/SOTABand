import { useState, useEffect } from 'react'
import { Loader2, FileText, ChevronLeft, ChevronRight } from 'lucide-react'
import { Card, CardBody } from '@/components/ui/card'
import { datasetFileUrl } from '@/types/workspace'

/** JSON / TXT 等纯文本预览（竖线样式与 MD 文档卡片一致） */
const TEXT_LIMIT = 200_000

interface TextPreviewProps {
  datasetId: string
  textFiles: Array<Record<string, unknown>>
  /** 初始展示的文件下标（配合 key 重挂载切换文件） */
  initialIndex?: number
}

export function TextPreview({ datasetId, textFiles, initialIndex = 0 }: TextPreviewProps) {
  const [text, setText] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [currentIndex, setCurrentIndex] = useState(0)

  const currentFile = textFiles[currentIndex]
  const hasPrev = currentIndex > 0
  const hasNext = currentIndex < textFiles.length - 1

  const loadFile = (index: number) => {
    setCurrentIndex(index)
    setLoading(true)
    setText(null)
    const file = textFiles[index]
    if (!file) return
    const relPath = (file.path as string) || (file.name as string)
    if (!relPath) { setLoading(false); return }
    fetch(datasetFileUrl(datasetId, relPath))
      .then(r => r.text())
      .then(t => {
        // JSON 尝试美化缩进，失败按原样展示
        let out = t
        if (((file.name as string) || '').toLowerCase().endsWith('.json')) {
          try { out = JSON.stringify(JSON.parse(t), null, 2) } catch { /* 原样 */ }
        }
        setText(out.length > TEXT_LIMIT ? out.slice(0, TEXT_LIMIT) + '\n…（内容过长，已截断）' : out)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    if (textFiles.length > 0) loadFile(Math.min(initialIndex, textFiles.length - 1))
  }, [])

  if (textFiles.length === 0) return null

  return (
    <Card className="border-maia-border">
      <CardBody>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5 min-w-0">
            <FileText className="h-3.5 w-3.5 text-maia-accent shrink-0" />
            <span className="text-[13px] font-medium text-maia-text-secondary truncate">
              {currentFile?.name as string}
            </span>
            <span className="text-[12px] text-maia-text-muted shrink-0">({currentIndex + 1}/{textFiles.length})</span>
          </div>
          {textFiles.length > 1 && (
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                onClick={() => hasPrev && loadFile(currentIndex - 1)}
                disabled={!hasPrev}
                className="flex items-center gap-1 px-2 h-6 rounded border border-maia-border hover:bg-maia-bg disabled:opacity-30 disabled:cursor-not-allowed text-[12px] text-maia-text-secondary"
                title="上一篇"
              >
                <ChevronLeft className="h-3 w-3" />上一篇
              </button>
              <button
                onClick={() => hasNext && loadFile(currentIndex + 1)}
                disabled={!hasNext}
                className="flex items-center gap-1 px-2 h-6 rounded border border-maia-border hover:bg-maia-bg disabled:opacity-30 disabled:cursor-not-allowed text-[12px] text-maia-text-secondary"
                title="下一篇"
              >
                下一篇<ChevronRight className="h-3 w-3" />
              </button>
            </div>
          )}
        </div>
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-maia-text-muted" />
          </div>
        ) : text !== null ? (
          <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap break-all bg-maia-bg rounded p-3 border border-maia-border">
            {text}
          </pre>
        ) : (
          <div className="text-[12px] text-maia-text-muted py-4 text-center">无法加载文件</div>
        )}
      </CardBody>
    </Card>
  )
}

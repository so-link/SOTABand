import { useState } from 'react'
import { Download, File } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { useResourceStore } from '@/stores/resource-store'
import { datasetFileUrl } from '@/types/workspace'
import { authedUrl } from '@/services/api/auth'
import { MediaPreviewModal } from '@/components/ui/media-preview-modal'

/**
 * 数据集预览的文件列表（右栏）。
 * 文件清单由中栏 DataPreviewView 拉取后写入 store，这里只负责展示与点选：
 * 点 CSV / Markdown / 文本切换中栏对应预览；图片/视频/PDF 等其他类型用页内模态框
 * 放大查看（不再 window.open 新开浏览器标签）。
 */
export function DatasetFilesPanel({ hideHeader = false }: { hideHeader?: boolean }) {
  const previewDatasetId = useResourceStore((s) => s.previewDatasetId)
  const dataResources = useResourceStore((s) => s.dataResources)
  const cachedDataset = useResourceStore((s) => s.cachedDatasetForDetail)
  const previewFiles = useResourceStore((s) => s.previewFiles)
  const activeCsvIndex = useResourceStore((s) => s.activeCsvIndex)
  const activeMdIndex = useResourceStore((s) => s.activeMdIndex)
  const activeTextIndex = useResourceStore((s) => s.activeTextIndex)
  const setActiveCsvIndex = useResourceStore((s) => s.setActiveCsvIndex)
  const setActiveMdIndex = useResourceStore((s) => s.setActiveMdIndex)
  const setActiveTextIndex = useResourceStore((s) => s.setActiveTextIndex)

  const isFmt = (f: Record<string, unknown>, exts: string[]) => exts.includes(((f.format as string) || '').toLowerCase())
  const tableFiles = previewFiles.filter((f) => isFmt(f, ['csv', 'tsv', 'xlsx', 'xls', 'parquet', 'pq']))
  const mdFiles = previewFiles.filter((f) => isFmt(f, ['md']))
  const textFiles = previewFiles.filter((f) => isFmt(f, ['json', 'jsonl', 'txt']))
  const inlineFmt = (f: Record<string, unknown>) =>
    tableFiles.includes(f) || mdFiles.includes(f) || textFiles.includes(f)
  // 中栏不做内嵌预览的文件（图片/视频/PDF/其他）统一交给模态框，保持可连续翻页
  const modalFiles = previewFiles.filter((f) => !inlineFmt(f))
  // 正在模态框查看的文件在 modalFiles 中的下标，-1 表示未打开
  const [modalIndex, setModalIndex] = useState(-1)
  // 数据集名以中栏实际预览的数据集为准（多标签下随激活标签变化）
  const dataset = (previewDatasetId && dataResources.find((r) => r.id === previewDatasetId))
    ?? (cachedDataset?.id === previewDatasetId ? cachedDataset : null)

  const fileUrl = (f: Record<string, unknown>) => {
    if (!previewDatasetId) return ''
    const relPath = (f.path as string) || (f.name as string)
    // 模态框内 <img>/<video> 与下载都是原生请求，带不了 Authorization 头：追加 ?auth_token=
    return relPath ? authedUrl(datasetFileUrl(previewDatasetId, relPath)) : ''
  }

  const download = (url: string, name: string) => {
    // 用隐藏 <a download> 触发下载：window.open 会新开浏览器窗口/标签
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
  }

  return (
    <div className="flex flex-col h-full">
      {!hideHeader && (
        <div className="flex items-center justify-between h-9 px-3 shrink-0 border-b border-maia-border">
          <span className="text-[13px] text-maia-text-muted truncate">数据集文件</span>
          {previewFiles.length > 0 && (
            <span className="text-[12px] text-maia-text-muted shrink-0">{previewFiles.length} 个</span>
          )}
        </div>
      )}

      {dataset && (
        <div className="px-3 py-2 border-b border-maia-border shrink-0">
          <div className="text-[13px] font-medium text-maia-text-heading truncate" title={dataset.name}>
            {dataset.name}
          </div>
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto p-1.5">
        {previewFiles.length === 0 ? (
          <div className="flex h-full items-center justify-center px-3 text-center text-[12px] text-maia-text-muted">
            正在读取文件列表…
          </div>
        ) : (
          previewFiles.map((f, i) => {
            const name = (f.name as string) || ''
            const fmt = ((f.format as string) || '').toUpperCase()
            const size = f.size as number
            const csvIdx = tableFiles.indexOf(f)
            const mdIdx = mdFiles.indexOf(f)
            const textIdx = textFiles.indexOf(f)
            const isActive = (csvIdx >= 0 && csvIdx === activeCsvIndex) || (mdIdx >= 0 && mdIdx === activeMdIndex) || (textIdx >= 0 && textIdx === activeTextIndex)
            const url = fileUrl(f)
            const inModal = !inlineFmt(f)
            // 规范文档为该文件登记的说明（例如 ETF 的标的名称）
            const desc = (f.description as string) || ''

            const onClick = () => {
              if (csvIdx >= 0) setActiveCsvIndex(csvIdx)
              else if (mdIdx >= 0) setActiveMdIndex(mdIdx)
              else if (textIdx >= 0) setActiveTextIndex(textIdx)
              else if (inModal) setModalIndex(modalFiles.indexOf(f))
            }

            return (
              <div
                key={`${name}-${i}`}
                onClick={onClick}
                title={csvIdx >= 0 || mdIdx >= 0 || textIdx >= 0 ? '点击在中栏预览' : '点击放大预览'}
                className={`group rounded px-2 py-1.5 cursor-pointer transition-colors ${
                  isActive ? 'bg-maia-accent-light text-maia-accent' : 'text-maia-text hover:bg-maia-bg'
                }`}
              >
                <div className="flex items-center gap-2">
                <File className="h-3 w-3 shrink-0 text-maia-text-muted" />
                <span className="min-w-0 flex-1 truncate text-[12px]">{name}</span>
                <Badge variant="default" className="text-[10px] shrink-0">{fmt}</Badge>
                {size > 0 && (
                  <span className="text-[11px] text-maia-text-muted shrink-0">
                    {size > 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${(size / 1024).toFixed(0)} KB`}
                  </span>
                )}
                {url && (
                  <button
                    className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:text-maia-accent transition-all shrink-0"
                    title="下载文件"
                    onClick={(e) => { e.stopPropagation(); download(url, name) }}
                  >
                    <Download className="h-3 w-3" />
                  </button>
                )}
                </div>
                {/* 规范文档为该文件登记的说明，窄栏下单独占一行避免挤压文件名 */}
                {desc && (
                  <div className="mt-0.5 truncate pl-5 text-[11px] text-maia-text-muted" title={desc}>
                    {desc}
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>

      {/* 图片/视频/PDF 等的页内放大预览 */}
      {modalIndex >= 0 && modalFiles.length > 0 && (
        <MediaPreviewModal
          items={modalFiles.map((f) => ({ url: fileUrl(f), name: (f.name as string) || '' }))}
          index={Math.min(modalIndex, modalFiles.length - 1)}
          onIndexChange={setModalIndex}
          onClose={() => setModalIndex(-1)}
        />
      )}
    </div>
  )
}

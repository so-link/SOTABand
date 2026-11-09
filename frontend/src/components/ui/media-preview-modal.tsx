/**
 * 媒体预览模态框 —— 图片 / 视频 / PDF 统一在页内对话框里查看。
 *
 * 此前这些文件点击后走 window.open(_blank) 新开浏览器标签：脱离应用外壳、
 * 丢失深浅色主题、关掉标签就回不到原位置，列表里连翻几张图都要开一堆窗口。
 * 这里改为模态框，支持左右翻页、图片缩放、下载，键盘 Esc / ←→ 操作。
 *
 * 必须用 createPortal 挂到 document.body：TopBar/StatusBar 等父容器带
 * backdrop-filter，会把 fixed 定位与全屏遮罩困在父容器的定位上下文里。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X, ChevronLeft, ChevronRight, Download, ZoomIn, ZoomOut, Maximize, File as FileIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'

export interface MediaItem {
  /** 已完成鉴权处理的资源 URL（调用方负责 authedUrl） */
  url: string
  name: string
}

const VIDEO_EXTS = new Set(['mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv', 'm4v'])
const PDF_EXTS = new Set(['pdf'])
const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'tiff', 'tif', 'ico'])
const ZOOM_STEPS = [0.5, 0.75, 1, 1.5, 2, 3]

const extOf = (name: string) => (name.split('.').pop() || '').toLowerCase()

export function MediaPreviewModal({
  items,
  index,
  onIndexChange,
  onClose,
}: {
  items: MediaItem[]
  index: number
  onIndexChange: (i: number) => void
  onClose: () => void
}) {
  const [zoomIdx, setZoomIdx] = useState(2)
  const zoom = ZOOM_STEPS[zoomIdx]
  const open = index >= 0 && index < items.length
  const item = open ? items[index] : null
  const ext = item ? extOf(item.name) : ''
  const isVideo = VIDEO_EXTS.has(ext)
  const isPdf = PDF_EXTS.has(ext)
  const isImage = IMAGE_EXTS.has(ext)
  const canZoom = isImage
  const total = items.length
  const move = (delta: number) => {
    const next = index + delta
    if (next >= 0 && next < total) {
      setZoomIdx(2)
      onIndexChange(next)
    }
  }

  useEffect(() => { if (!open) setZoomIdx(2) }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') move(-1)
      else if (e.key === 'ArrowRight') move(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!open || !item) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] flex flex-col bg-black/80 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={item.name}
        className="flex h-full w-full flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏：文件名 + 翻页 + 缩放 + 下载 + 关闭 */}
        <div className="flex items-center gap-2 px-4 py-2 shrink-0 border-b border-white/10 bg-black/40">
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-white/90" title={item.name}>
            {item.name}
          </span>
          {total > 1 && (
            <div className="flex items-center gap-1 shrink-0">
              <Button variant="outline" size="sm" className="h-7 w-7 p-0" disabled={index <= 0}
                onClick={() => move(-1)} title="上一个（←）">
                <ChevronLeft className="h-3.5 w-3.5" />
              </Button>
              <span className="min-w-[52px] text-center text-[12px] text-white/70">{index + 1} / {total}</span>
              <Button variant="outline" size="sm" className="h-7 w-7 p-0" disabled={index >= total - 1}
                onClick={() => move(1)} title="下一个（→）">
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
          {canZoom && (
            <div className="flex items-center gap-1 shrink-0">
              <Button variant="outline" size="sm" className="h-7 w-7 p-0" disabled={zoomIdx <= 0}
                onClick={() => setZoomIdx(i => Math.max(0, i - 1))} title="缩小">
                <ZoomOut className="h-3.5 w-3.5" />
              </Button>
              <span className="min-w-[42px] text-center text-[12px] text-white/70">{Math.round(zoom * 100)}%</span>
              <Button variant="outline" size="sm" className="h-7 w-7 p-0" disabled={zoomIdx >= ZOOM_STEPS.length - 1}
                onClick={() => setZoomIdx(i => Math.min(ZOOM_STEPS.length - 1, i + 1))} title="放大">
                <ZoomIn className="h-3.5 w-3.5" />
              </Button>
              <Button variant="outline" size="sm" className="h-7 w-7 p-0" onClick={() => setZoomIdx(2)} title="实际大小">
                <Maximize className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
          <a href={item.url} download={item.name} className="shrink-0" title="下载">
            <Button variant="outline" size="sm" className="h-7 px-2 text-[12px]">
              <Download className="h-3.5 w-3.5" />
            </Button>
          </a>
          <Button variant="outline" size="sm" className="h-7 w-7 p-0 shrink-0" onClick={onClose} title="关闭（Esc）">
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>

        {/* 内容区 */}
        <div className="flex-1 min-h-0 overflow-auto flex items-center justify-center p-4">
          {isVideo ? (
            <video src={item.url} controls autoPlay className="max-w-full max-h-full rounded shadow-2xl" />
          ) : isPdf ? (
            <iframe title={item.name} src={item.url} className="h-full w-full max-w-5xl rounded bg-white shadow-2xl" />
          ) : isImage ? (
            <img
              src={item.url}
              alt={item.name}
              style={{ transform: `scale(${zoom})` }}
              className="max-w-full max-h-full object-contain shadow-2xl transition-transform origin-center"
            />
          ) : (
            // 浏览器无法内嵌渲染的类型（docx/xlsx/压缩包等）：给出下载入口，
            // 不再像 window.open 那样新开窗口
            <div className="flex flex-col items-center gap-3 text-white/70">
              <FileIcon className="h-14 w-14 opacity-40" />
              <p className="text-[14px]">该文件类型不支持内嵌预览</p>
              <p className="text-[12px] text-white/50">{item.name}</p>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

import { useState, useEffect } from 'react'
import { Database, File, Image, Loader2, ChevronLeft, ChevronRight, Table2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import { CsvTablePreview } from './CsvTablePreview'
import { MarkdownPreview } from './MarkdownPreview'
import type { DataResource } from '@/types/resources'

const BASE_URL = ''
const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'tiff', 'tif', 'ico'])
const PAGE_SIZE_OPTIONS = [8, 16, 24, 32, 48]
const TABLE_PAGE_OPTIONS = [8, 16, 32, 64, 100]

function isImageFile(file: Record<string, unknown>): boolean {
  const fmt = ((file.format as string) || '').toLowerCase()
  const name = ((file.name as string) || '').toLowerCase()
  return IMAGE_EXTENSIONS.has(fmt) || IMAGE_EXTENSIONS.has(name.split('.').pop() || '')
}

export function DataPreviewView() {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const cachedDataset = useResourceStore((s) => s.cachedDatasetForDetail)
  const dataset = selectedResource?.type === 'data' ? (selectedResource as DataResource) : cachedDataset
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(16)

  useEffect(() => {
    if (!dataset) return
    setPage(1)
    setLoading(true)
    fetch(`${BASE_URL}/api/data/${dataset.id}/preview`)
      .then(r => r.json())
      .then(d => { setDetail(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [dataset])

  if (!dataset) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-maia-text-muted gap-2">
        <Database className="h-10 w-10 opacity-20" />
        <p className="text-sm">请在左侧选择数据集查看预览</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin text-maia-text-muted" />
      </div>
    )
  }

  const files = (detail?.files as Array<Record<string, unknown>>) || []
  const specMd = (detail?.spec_md as string) || ''
  const datasetPath = ((detail?.dataset as Record<string, unknown>)?.data_path as string) || ''

  const imageFiles = files.filter(isImageFile)
  const csvFiles = files.filter(f => ((f.format as string) || '').toLowerCase() === 'csv')
  const mdFiles = files.filter(f => ((f.format as string) || '').toLowerCase() === 'md')
  const nonImageFiles = files.filter(f => !isImageFile(f))

  // 构建文件 URL
  const getFileUrl = (f: Record<string, unknown>) => {
    const filePath = datasetPath ? `${datasetPath}/${f.name}` : ''
    if (!filePath) return ''
    const isImg = isImageFile(f)
    return isImg
      ? `${BASE_URL}/api/file/image?path=${encodeURIComponent(filePath)}`
      : `${BASE_URL}/api/file/download?path=${encodeURIComponent(filePath)}`
  }

  // 分页
  const totalPages = Math.max(1, Math.ceil(imageFiles.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageImages = imageFiles.slice((safePage - 1) * pageSize, safePage * pageSize)

  return (
    <div className="flex flex-col h-full bg-maia-surface overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-blue-500" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">{dataset.name}</span>
          <Badge variant="success">v{dataset.version}</Badge>
          <Badge variant="accent">{dataset.format?.toUpperCase()}</Badge>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-maia-text-muted">
          {files.length > 0 && <span>{files.length} 个文件</span>}
          {imageFiles.length > 0 && <span>· {imageFiles.length} 张图片</span>}
          {dataset.fileSize > 0 && (
            <span>
              {dataset.fileSize > 1048576 ? `${(dataset.fileSize / 1048576).toFixed(1)} MB` : `${(dataset.fileSize / 1024).toFixed(1)} KB`}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 p-4 max-w-5xl mx-auto space-y-4 w-full">
        {/* Image gallery */}
        {imageFiles.length > 0 && (
          <Card className="border-maia-border">
            <CardBody>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-1.5">
                  <Image className="h-3.5 w-3.5 text-amber-400" />
                  <span className="text-xs font-medium text-maia-text-secondary tracking-wide">
                    图片预览（共 {imageFiles.length} 张）
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-maia-text-muted">每页</span>
                  <select
                    value={pageSize}
                    onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
                    className="h-6 rounded border border-maia-border bg-maia-bg text-[11px] text-maia-text px-1 outline-none focus:border-maia-accent/40"
                  >
                    {PAGE_SIZE_OPTIONS.map(n => (
                      <option key={n} value={n}>{n} 张</option>
                    ))}
                  </select>
                  <div className="flex items-center gap-1 ml-2">
                    <Button
                      size="sm" variant="outline"
                      className="h-6 w-6 p-0"
                      disabled={safePage <= 1}
                      onClick={() => setPage(p => Math.max(1, p - 1))}
                    >
                      <ChevronLeft className="h-3 w-3" />
                    </Button>
                    <span className="text-[11px] text-maia-text-muted min-w-[40px] text-center">
                      {safePage}/{totalPages}
                    </span>
                    <Button
                      size="sm" variant="outline"
                      className="h-6 w-6 p-0"
                      disabled={safePage >= totalPages}
                      onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                    >
                      <ChevronRight className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-4 gap-2">
                {pageImages.map((img, i) => {
                  const imgPath = datasetPath ? `${datasetPath}/${img.name}` : ''
                  const imgUrl = imgPath ? `${BASE_URL}/api/file/image?path=${encodeURIComponent(imgPath)}` : ''
                  return (
                    <div
                      key={i}
                      className="aspect-square rounded border border-maia-border bg-maia-bg overflow-hidden group cursor-pointer hover:border-maia-accent/40 transition-colors"
                      onClick={() => {
                        if (imgUrl) window.open(imgUrl, '_blank')
                      }}
                    >
                      {imgUrl ? (
                        <div className="w-full h-full flex items-center justify-center">
                          <img
                            src={imgUrl}
                            alt={img.name as string}
                            className="max-w-full max-h-full object-contain"
                            loading="lazy"
                            onError={(e) => {
                              const el = e.currentTarget
                              el.style.display = 'none'
                              const parent = el.parentElement
                              if (parent) {
                                parent.innerHTML = `<div class="flex flex-col items-center justify-center gap-1 text-maia-text-muted"><svg class="h-6 w-6 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg><span class="text-[10px]">加载失败</span></div>`
                              }
                            }}
                          />
                        </div>
                      ) : (
                        <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-maia-text-muted">
                          <Image className="h-6 w-6 opacity-30" />
                          <span className="text-[10px]">{img.name as string}</span>
                        </div>
                      )}
                      <div className="absolute bottom-0 left-0 right-0 bg-black/60 px-1.5 py-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                        <span className="text-[9px] text-white truncate block">{img.name as string}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </CardBody>
          </Card>
        )}

        {/* CSV table preview */}
        {csvFiles.length > 0 && (
          <CsvTablePreview datasetPath={datasetPath} csvFiles={csvFiles} />
        )}

        {/* Markdown preview */}
        {mdFiles.length > 0 && (
          <MarkdownPreview datasetPath={datasetPath} mdFiles={mdFiles} />
        )}

        {/* File list */}
        {(nonImageFiles.length > 0 || imageFiles.length > 0) && (
          <Card className="border-maia-border">
            <CardBody>
              <div className="flex items-center gap-1.5 mb-2">
                <File className="h-3.5 w-3.5 text-maia-text-muted" />
                <span className="text-xs font-medium text-maia-text-secondary tracking-wide">文件列表（点击预览）</span>
              </div>
              <div className="space-y-1">
                {[...imageFiles, ...nonImageFiles].map((f, i) => {
                  const url = getFileUrl(f)
                  return (
                  <div
                    key={i}
                    className="flex items-center justify-between py-1 px-2 rounded hover:bg-maia-bg text-[11px] cursor-pointer"
                    onClick={() => { if (url) window.open(url, '_blank') }}
                    title="点击预览"
                  >
                    <div className="flex items-center gap-1.5">
                      <File className="h-3 w-3 text-maia-text-muted" />
                      <span className="text-maia-text hover:text-maia-accent transition-colors">{f.name as string}</span>
                    </div>
                    <div className="flex items-center gap-3 text-maia-text-muted">
                      <Badge variant="default" className="text-[9px]">{((f.format as string) || '').toUpperCase()}</Badge>
                      <span>{(f.size as number) > 1024 ? `${((f.size as number) / 1024).toFixed(0)} KB` : `${f.size} B`}</span>
                    </div>
                  </div>
                  )
                })}
              </div>
            </CardBody>
          </Card>
        )}

        {/* MD spec summary */}
        {specMd && (
          <Card className="border-maia-border">
            <CardBody>
              <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto">
                {specMd.slice(0, 3000)}
              </pre>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}

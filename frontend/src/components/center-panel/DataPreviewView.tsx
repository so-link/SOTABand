import { useState, useEffect } from 'react'
import { Database, Image, Loader2, ChevronLeft, ChevronRight, Plus, Package, FileText, Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { DataTablePreview } from './DataTablePreview'
import { MarkdownPreview } from './MarkdownPreview'
import { TextPreview } from './TextPreview'
import type { DataResource } from '@/types/resources'

const BASE_URL = ''
const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'tiff', 'tif', 'ico'])
const PAGE_SIZE_OPTIONS = [8, 16, 24, 32, 48]

function isImageFile(file: Record<string, unknown>): boolean {
  const fmt = ((file.format as string) || '').toLowerCase()
  const name = ((file.name as string) || '').toLowerCase()
  return IMAGE_EXTENSIONS.has(fmt) || IMAGE_EXTENSIONS.has(name.split('.').pop() || '')
}

export function DataPreviewView({ datasetId }: { datasetId?: string }) {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const cachedDataset = useResourceStore((s) => s.cachedDatasetForDetail)
  const dataResources = useResourceStore((s) => s.dataResources)
  const selectResource = useResourceStore((s) => s.selectResource)
  const { setActiveView } = useUIStore()
  // 优先按视图 ID 定位数据集：每个数据集独立标签，刷新后仍可按 ID 恢复；
  // 列表尚未加载时退回缓存的同一数据集；无 ID 的旧入口退回到当前选中资源。
  const dataset = datasetId
    ? (dataResources.find((d) => d.id === datasetId) as DataResource | undefined)
      ?? (cachedDataset?.id === datasetId ? cachedDataset : null)
    : (selectedResource?.type === 'data' ? (selectedResource as DataResource) : cachedDataset)
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(16)
  // 当前预览的 CSV / Markdown 下标存在共享 store 中：
  // 右栏「数据集文件」列表点选文件即可切换中栏预览。
  const activeCsv = useResourceStore((s) => s.activeCsvIndex)
  const activeMd = useResourceStore((s) => s.activeMdIndex)
  const activeText = useResourceStore((s) => s.activeTextIndex)
  const setActiveCsv = useResourceStore((s) => s.setActiveCsvIndex)
  const setActiveMd = useResourceStore((s) => s.setActiveMdIndex)
  const setActiveText = useResourceStore((s) => s.setActiveTextIndex)
  const setPreviewFiles = useResourceStore((s) => s.setPreviewFiles)
  // MD 文档（说明文档 / spec）默认收起，只显示数据预览，与工具详情一致
  const [showDoc, setShowDoc] = useState(false)

  useEffect(() => {
    if (!dataset) return
    setPage(1)
    // 先清空上一数据集的文件清单，避免右栏短暂显示旧数据集的文件
    setPreviewFiles(dataset.id, '', [])
    setLoading(true)
    fetch(`${BASE_URL}/api/data/${dataset.id}/preview`)
      .then(r => r.json())
      .then(d => {
        setDetail(d)
        // 文件清单发布到 store，右栏据此渲染文件列表
        setPreviewFiles(
          dataset.id,
          (d?.dataset as Record<string, unknown>)?.data_path as string || '',
          (d?.files as Array<Record<string, unknown>>) || [],
        )
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [dataset, setPreviewFiles])

  if (!dataset) {
    // 空态：充分利用中栏空间。有数据集则列出供选择；没有则引导创建/管理。
    return (
      <div className="flex flex-col items-center justify-center h-full text-maia-text-muted gap-4 p-6">
        <Database className="h-12 w-12 opacity-20" />
        {dataResources.length > 0 ? (
          <>
            <p className="text-sm">选择一个数据集查看预览</p>
            <div className="w-full max-w-md space-y-1">
              {dataResources.map((d) => (
                <button
                  key={d.id}
                  onClick={() => selectResource(d)}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded border border-maia-border bg-maia-bg hover:border-maia-accent/40 hover:bg-maia-sidebar-hover text-left transition-colors"
                >
                  <Database className="h-3.5 w-3.5 text-blue-500 shrink-0" />
                  <span className="text-[13px] text-maia-text truncate flex-1">{d.name}</span>
                  {d.format && (
                    <Badge variant="default" className="text-[11px] shrink-0">{d.format.toUpperCase()}</Badge>
                  )}
                </button>
              ))}
            </div>
          </>
        ) : (
          <>
            <div className="text-center">
              <p className="text-sm text-maia-text-heading font-medium">暂无数据集</p>
              <p className="text-[12px] mt-1">创建数据集或从数据集仓库导入后，在此查看预览</p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => setActiveView('dataset-editor')}
              >
                <Plus className="h-3.5 w-3.5" />
                新建数据集
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => setActiveView('dataset-repository')}
              >
                <Package className="h-3.5 w-3.5" />
                数据集仓库
              </Button>
            </div>
          </>
        )}
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
  // 可表格化的格式统一走后端 /table 接口，中栏表格布局对各类格式保持一致
  const tableFiles = files.filter(f => ['csv', 'tsv', 'xlsx', 'xls', 'parquet', 'pq'].includes(((f.format as string) || '').toLowerCase()))
  const mdFiles = files.filter(f => ((f.format as string) || '').toLowerCase() === 'md')
  const textFiles = files.filter(f => ['json', 'jsonl', 'txt'].includes(((f.format as string) || '').toLowerCase()))

  // 分页
  const totalPages = Math.max(1, Math.ceil(imageFiles.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageImages = imageFiles.slice((safePage - 1) * pageSize, safePage * pageSize)

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header：第一行身份与统计，第二行文档工具条（与工具详情页对齐） */}
      <div className="shrink-0 border-b border-maia-border bg-maia-bg/40">
        <div className="flex items-center gap-2 px-4 h-11 min-w-0">
          <Database className="h-4 w-4 text-blue-500 shrink-0" />
          <span className="text-[14px] font-semibold text-maia-text-heading truncate min-w-0">{dataset.name}</span>
          <Badge variant="success" className="shrink-0">v{dataset.version}</Badge>
          <Badge variant="accent" className="shrink-0">{dataset.format?.toUpperCase()}</Badge>
          <div className="ml-auto flex items-center gap-2 shrink-0">
            <div className="flex items-center gap-2 text-[12px] text-maia-text-muted">
              {files.length > 0 && <span>{files.length} 个文件</span>}
              {imageFiles.length > 0 && <span>· {imageFiles.length} 张图片</span>}
              {dataset.fileSize > 0 && (
                <span>
                  {dataset.fileSize > 1048576 ? `${(dataset.fileSize / 1048576).toFixed(1)} MB` : `${(dataset.fileSize / 1024).toFixed(1)} KB`}
                </span>
              )}
            </div>
            <a
              href={`${BASE_URL}/api/data/${encodeURIComponent(dataset.id)}/archive`}
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-transparent text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text transition-colors shrink-0"
              title="打包下载数据集全部文件（zip）"
            >
              <Download className="h-3.5 w-3.5" />
              打包下载
            </a>
          </div>
        </div>

        {(specMd || mdFiles.length > 0) && (
          <div className="flex items-center gap-1 px-3 h-9 border-t border-maia-border/60 overflow-x-auto">
            <button
              onClick={() => setShowDoc(v => !v)}
              className={cn(
                'inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border transition-colors shrink-0',
                showDoc
                  ? 'bg-maia-accent-light text-maia-accent border-maia-accent-border'
                  : 'text-maia-text-muted border-transparent hover:bg-maia-sidebar-hover hover:text-maia-text',
              )}
            >
              <FileText className="h-3.5 w-3.5" />
              {showDoc ? '收起 MD 文档' : 'MD 文档'}
            </button>
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-auto">
        {/* 单栏宽度与工具详情（max-w-2xl）对齐 */}
        <div className="p-4 max-w-2xl mx-auto space-y-4 w-full">
        {/* Image gallery */}
        {imageFiles.length > 0 && (
          <Card className="border-maia-border">
            <CardBody>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-1.5">
                  <Image className="h-3.5 w-3.5 text-amber-400" />
                  <span className="text-[13px] font-medium text-maia-text-secondary">
                    图片预览（共 {imageFiles.length} 张）
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-maia-text-muted">每页</span>
                  <select
                    value={pageSize}
                    onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
                    className="h-6 rounded border border-maia-border bg-maia-bg text-[12px] text-maia-text px-1 outline-none focus:border-maia-accent/40"
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
                    <span className="text-[12px] text-maia-text-muted min-w-[40px] text-center">
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
                                parent.innerHTML = `<div class="flex flex-col items-center justify-center gap-1 text-maia-text-muted"><svg class="h-6 w-6 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg><span class="text-[12px]">加载失败</span></div>`
                              }
                            }}
                          />
                        </div>
                      ) : (
                        <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-maia-text-muted">
                          <Image className="h-6 w-6 opacity-30" />
                          <span className="text-[12px]">{img.name as string}</span>
                        </div>
                      )}
                      <div className="absolute bottom-0 left-0 right-0 bg-black/60 px-1.5 py-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                        <span className="text-[11px] text-white truncate block">{img.name as string}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </CardBody>
          </Card>
        )}

        {/* Markdown 文档：展开时位于预览表格上方，与工具详情（文档在上、代码在下）对齐 */}
        {showDoc && mdFiles.length > 0 && (
          <MarkdownPreview key={activeMd} datasetPath={datasetPath} mdFiles={mdFiles} initialIndex={activeMd} />
        )}

        {showDoc && specMd && (
          <Card className="border-maia-border">
            <CardBody>
              <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap">
                {specMd.slice(0, 3000)}
              </pre>
            </CardBody>
          </Card>
        )}

        {/* 表格预览：CSV / Excel / Parquet 统一表格组件，布局一致 */}
        {tableFiles.length > 0 && (
          <DataTablePreview datasetId={dataset.id} tableFiles={tableFiles} activeIndex={activeCsv} onActiveChange={setActiveCsv} />
        )}

        {/* JSON / TXT 纯文本预览 */}
        {textFiles.length > 0 && (
          <TextPreview key={`text:${activeText}`} datasetPath={datasetPath} textFiles={textFiles} initialIndex={activeText} />
        )}
      </div>
      </div>
    </div>
  )
}

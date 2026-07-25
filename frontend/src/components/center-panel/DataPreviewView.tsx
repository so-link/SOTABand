import { useState, useEffect } from 'react'
import { Database, File, Loader2, Eye } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import type { DataResource } from '@/types/resources'

const BASE_URL = ''

export function DataPreviewView() {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const dataset = selectedResource?.type === 'data' ? (selectedResource as DataResource) : null
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    if (!dataset) return
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
  const previewTool = detail?.preview_tool as string | null
  const hasPreviewTool = (detail?.has_preview_tool as boolean) || false

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
          {dataset.fileSize > 0 && (
            <span>
              {dataset.fileSize > 1048576 ? `${(dataset.fileSize / 1048576).toFixed(1)} MB` : `${(dataset.fileSize / 1024).toFixed(1)} KB`}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 p-4 max-w-3xl mx-auto space-y-4 w-full">
        {/* Preview tool match */}
        {hasPreviewTool && previewTool && (
          <Card className="border-blue-200 bg-blue-50/50">
            <CardBody>
              <div className="flex items-center gap-2 mb-1">
                <Eye className="h-4 w-4 text-blue-500" />
                <span className="text-xs font-medium text-blue-700">推荐预览工具</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-mono text-blue-800">{previewTool}</span>
                <Button size="sm" variant="outline" className="text-[11px] h-6">
                  执行预览
                </Button>
              </div>
            </CardBody>
          </Card>
        )}

        {/* No preview tool */}
        {!hasPreviewTool && (
          <Card className="border-amber-200 bg-amber-50/50">
            <CardBody>
              <div className="flex items-center gap-1.5 text-xs text-amber-600">
                <Eye className="h-3.5 w-3.5" />
                暂无专用的数据预览工具，以下展示数据集的描述信息
              </div>
            </CardBody>
          </Card>
        )}

        {/* File list */}
        {files.length > 0 && (
          <Card className="border-maia-border">
            <CardBody>
              <div className="flex items-center gap-1.5 mb-2">
                <File className="h-3.5 w-3.5 text-maia-text-muted" />
                <span className="text-xs font-medium text-maia-text-secondary tracking-wide">文件列表</span>
              </div>
              <div className="space-y-1">
                {files.map((f, i) => (
                  <div key={i} className="flex items-center justify-between py-1 px-2 rounded hover:bg-maia-bg text-[11px]">
                    <div className="flex items-center gap-1.5">
                      <File className="h-3 w-3 text-maia-text-muted" />
                      <span className="text-maia-text">{f.name as string}</span>
                    </div>
                    <div className="flex items-center gap-3 text-maia-text-muted">
                      <Badge variant="default" className="text-[9px]">{(f.format as string)?.toUpperCase()}</Badge>
                      <span>{(f.size as number) > 1024 ? `${((f.size as number) / 1024).toFixed(0)} KB` : `${f.size} B`}</span>
                    </div>
                  </div>
                ))}
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

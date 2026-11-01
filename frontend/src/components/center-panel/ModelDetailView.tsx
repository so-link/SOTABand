import { useState, useEffect } from 'react'
import { BrainCircuit, FileText, Cpu, Link2, Calendar, FolderOpen } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { cn } from '@/lib/utils'

const BASE_URL = ''

interface ModelDetail {
  id: string
  name: string
  version: string
  status?: string
  framework?: string
  model_path?: string
  input_format?: string
  output_format?: string
  tags?: string[]
  associated_tool_id?: string
  created_at?: string
  spec_md?: string
}

export function ModelDetailView({ modelId }: { modelId?: string }) {
  const [model, setModel] = useState<ModelDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [showSpec, setShowSpec] = useState(true)

  useEffect(() => {
    if (!modelId) return
    setLoading(true)
    setNotFound(false)
    fetch(`${BASE_URL}/api/model/${encodeURIComponent(modelId)}`)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(data => {
        setModel(data.model || null)
        setLoading(false)
      })
      .catch(() => {
        setNotFound(true)
        setLoading(false)
      })
  }, [modelId])

  if (!modelId || notFound) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-maia-text-muted gap-2">
        <BrainCircuit className="h-10 w-10 opacity-20" />
        <p className="text-sm">模型不存在或未选择</p>
      </div>
    )
  }

  if (loading || !model) {
    return (
      <div className="flex items-center justify-center h-full text-maia-text-muted text-sm">
        加载模型信息…
      </div>
    )
  }

  const infoRows: Array<{ label: string; value?: string; icon?: React.ReactNode }> = [
    { label: '框架', value: model.framework, icon: <Cpu className="h-3.5 w-3.5" /> },
    { label: '输入格式', value: model.input_format },
    { label: '输出格式', value: model.output_format },
    { label: '关联工具', value: model.associated_tool_id, icon: <Link2 className="h-3.5 w-3.5" /> },
    { label: '模型文件', value: model.model_path, icon: <FolderOpen className="h-3.5 w-3.5" /> },
    { label: '创建时间', value: model.created_at ? new Date(model.created_at).toLocaleString() : undefined, icon: <Calendar className="h-3.5 w-3.5" /> },
  ]

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header：与 Agent / 工具详情页对齐的双行结构 */}
      <div className="shrink-0 border-b border-maia-border bg-maia-bg/40">
        <div className="flex items-center gap-2 px-4 h-11 min-w-0">
          <BrainCircuit className="h-4 w-4 text-emerald-500 shrink-0" />
          <span className="text-[14px] font-semibold text-maia-text-heading truncate min-w-0">
            {model.name}
          </span>
          <Badge variant="success" className="shrink-0">v{model.version}</Badge>
          {model.status && (
            <Badge variant="default" className="shrink-0">{model.status}</Badge>
          )}
        </div>

        <div className="flex items-center gap-1 px-3 h-9 border-t border-maia-border/60 overflow-x-auto">
          <button
            onClick={() => setShowSpec(!showSpec)}
            className={cn(
              'inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border transition-colors shrink-0',
              showSpec
                ? 'bg-maia-accent-light text-maia-accent border-maia-accent-border'
                : 'text-maia-text-muted border-transparent hover:bg-maia-sidebar-hover hover:text-maia-text',
            )}
          >
            <FileText className="h-3.5 w-3.5" />
            {showSpec ? '收起 MD 文档' : 'MD 文档'}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-auto p-4">
        <div className="max-w-2xl mx-auto space-y-4">
          {/* 标签行 */}
          {(model.tags?.length ?? 0) > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[12px] text-maia-text-muted shrink-0">标签</span>
              {model.tags!.map((t, i) => (
                <Badge key={i} variant="default" className="text-[11px]">{t}</Badge>
              ))}
            </div>
          )}

          {/* 模型信息 */}
          <Card className="border-maia-border">
            <CardBody>
              <div className="text-[13px] font-medium text-maia-text-secondary mb-3">模型信息</div>
              <div className="space-y-2">
                {infoRows.filter(r => r.value).map((r, i) => (
                  <div key={i} className="flex items-start gap-2 text-[13px]">
                    <span className="flex items-center gap-1.5 w-20 shrink-0 text-maia-text-muted">
                      {r.icon}
                      {r.label}
                    </span>
                    <span className="text-maia-text break-all flex-1">{r.value}</span>
                  </div>
                ))}
              </div>
            </CardBody>
          </Card>

          {/* MD 规范文档 */}
          {showSpec && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="text-[13px] font-medium text-maia-text-secondary mb-2">MD 规范文档</div>
                <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">
                  {model.spec_md || '（无规范文档）'}
                </pre>
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

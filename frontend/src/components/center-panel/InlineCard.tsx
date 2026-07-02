import { useState } from 'react'
import type { InlineCard as InlineCardType } from '@/types/chat'
import { Card, CardHeader, CardBody } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUIStore, type ActiveView } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import {
  Database,
  Wrench,
  CheckCircle2,
  ArrowRight,
  FileCode,
  GitBranch,
  Activity,
} from 'lucide-react'

interface InlineCardProps {
  card: InlineCardType
}

export function InlineCard({ card }: InlineCardProps) {
  const { type, title, summary, data } = card
  const setActiveView = useUIStore((s) => s.setActiveView)

  switch (type) {
    case 'data-preview':
      return <DataPreviewCard title={title} summary={summary} data={data} />
    case 'tool-match':
      return <ToolMatchCard title={title} summary={summary} data={data} />
    case 'code-review':
      return <CodeReviewCard title={title} summary={summary} data={data} setActiveView={setActiveView} />
    case 'execution-progress':
      return <ExecutionProgressCard title={title} summary={summary} data={data} />
    case 'result-summary':
      return <ResultSummaryCard title={title} summary={summary} data={data} />
    case 'orchestration-preview':
      return <OrchestrationPreviewCard title={title} summary={summary} data={data} setActiveView={setActiveView} />
    case 'create-tool':
      return <CreateToolCard title={title} summary={summary} data={data} />
    default:
      return null
  }
}

// ---- Data Preview Card ----

function DataPreviewCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  return (
    <Card className="border-blue-200 bg-blue-50/50">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-blue-500" />
          <span className="text-xs font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-gray-500 mb-2">{summary}</p>
        <div className="grid grid-cols-3 gap-2">
          {Object.entries(data).map(([key, value]) => (
            <div key={key} className="text-center">
              <div className="text-lg font-semibold text-gray-800">{String(value)}</div>
              <div className="text-[10px] text-gray-400">{key}</div>
            </div>
          ))}
        </div>
        <Button variant="ghost" size="sm" className="mt-2 text-xs">
          展开预览 <ArrowRight className="h-3 w-3" />
        </Button>
      </CardBody>
    </Card>
  )
}

// ---- Tool Match Card ----

function ToolMatchCard({
  title, summary, data,
}: { title: string; summary: string; data: Record<string, unknown> }) {
  const tools = (data.tools as Array<{ name: string; version: string; match: number; isLocal: boolean; status: string }>) || []

  return (
    <Card className="border-emerald-200 bg-emerald-50/50">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-emerald-500" />
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="success">{summary}</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <div className="space-y-2">
          {tools.map((tool, i) => (
            <div key={i} className="flex items-center gap-2 p-2 rounded bg-white border border-gray-100">
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-medium truncate">{tool.name}</span>
                  <span className="text-[10px] text-gray-400">v{tool.version}</span>
                  {tool.isLocal && <Badge variant="accent" className="text-[9px]">⭐本地工具</Badge>}
                </div>
              </div>
              <Badge variant="default">{tool.match}% 匹配</Badge>
            </div>
          ))}
        </div>
        {data.suggestion ? (
          <p className="text-xs text-gray-500 mt-2 italic">💡 {String(data.suggestion)}</p>
        ) : null}
        <div className="flex gap-2 mt-3">
          <Button size="sm" className="text-xs">直接执行</Button>
          <Button variant="outline" size="sm" className="text-xs">查看详情</Button>
        </div>
      </CardBody>
    </Card>
  )
}

// ---- Orchestration Preview Card ----

function OrchestrationPreviewCard({
  title, summary, data,
  setActiveView,
}: { title: string; summary: string; data: Record<string, unknown>; setActiveView: (v: ActiveView) => void }) {
  return (
    <Card className="border-purple-200 bg-purple-50/50">
      <CardHeader>
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-purple-500" />
          <span className="text-xs font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <div className="flex items-center gap-4 mb-3">
          <div className="text-center">
            <div className="text-lg font-semibold text-purple-700">{String(data.agentCount)}</div>
            <div className="text-[10px] text-gray-400">Agent</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-purple-700">{String(data.parallelBranches)}</div>
            <div className="text-[10px] text-gray-400">并行分支</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-semibold text-purple-700">{String(data.estimatedTime)}</div>
            <div className="text-[10px] text-gray-400">预计耗时</div>
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-2">{summary}</p>
        <Button
          size="sm"
          className="text-xs"
          onClick={() => setActiveView('orchestration')}
        >
          在编排编辑器中打开 <ArrowRight className="h-3 w-3" />
        </Button>
      </CardBody>
    </Card>
  )
}

// ---- Code Review Card (simplified) ----

function CodeReviewCard({
  title, summary,
  setActiveView,
}: { title: string; summary: string; data?: Record<string, unknown>; setActiveView: (v: ActiveView) => void }) {
  return (
    <Card className="border-amber-200 bg-amber-50/50">
      <CardHeader>
        <div className="flex items-center gap-2">
          <FileCode className="h-4 w-4 text-amber-500" />
          <span className="text-xs font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-gray-500 mb-2">{summary}</p>
        <div className="flex gap-2">
          <Button
            size="sm"
            className="text-xs"
            onClick={() => setActiveView('code-review')}
          >
            在代码核验视图中打开
          </Button>
          <Button variant="outline" size="sm" className="text-xs text-emerald-600">
            批准并注册
          </Button>
        </div>
      </CardBody>
    </Card>
  )
}

// ---- Execution Progress Card ----

function ExecutionProgressCard({ title, summary }: { title: string; summary: string; data?: Record<string, unknown> }) {
  return (
    <Card className="border-gray-200">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-blue-500 animate-pulse" />
          <span className="text-xs font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-gray-500">{summary}</p>
        <div className="mt-2 h-1.5 bg-gray-200 rounded-full overflow-hidden">
          <div className="h-full bg-blue-500 rounded-full animate-pulse" style={{ width: '60%' }} />
        </div>
      </CardBody>
    </Card>
  )
}

// ---- Result Summary Card ----

function ResultSummaryCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const result = (data.result as Record<string, unknown>) || {}
  const outputFormat = result.output_format as string || ''
  const outputData = (result.data as Record<string, unknown>) || {}

  return (
    <Card className="border-emerald-200 bg-emerald-50/50">
      <CardHeader>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="success">完成</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-gray-500 mb-2">{summary}</p>

        {/* Visual: Image */}
        {outputFormat === 'image' && (
          <ImageOutput data={outputData} />
        )}

        {/* Visual: Table */}
        {outputFormat === 'table' && (
          <TableOutput data={outputData} />
        )}
      </CardBody>
    </Card>
  )
}

// ── Visual Output Components ──

function ImageOutput({ data }: { data: Record<string, unknown> }) {
  const imagePath = (data.image_path || data.path) as string | undefined
  const [error, setError] = useState(false)
  if (!imagePath) return <p className="text-xs text-gray-400">图片路径不可用</p>

  const src = `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'}/api/file/image?path=${encodeURIComponent(imagePath)}`

  return (
    <div className="mt-2 rounded-lg overflow-hidden border border-gray-200">
      {error ? (
        <div className="p-3 text-xs text-red-500 bg-red-50">
          图片加载失败: {imagePath}
        </div>
      ) : (
        <img
          src={src}
          alt="工具输出图片"
          className="w-full max-h-[400px] object-contain bg-gray-50"
          onError={() => setError(true)}
        />
      )}
    </div>
  )
}

function TableOutput({ data }: { data: Record<string, unknown> }) {
  const columns = (data.columns as string[]) || []
  const rows = (data.rows as unknown[][]) || []

  if (columns.length === 0) return <p className="text-xs text-gray-400">表格数据不可用</p>

  return (
    <div className="mt-2 rounded-lg border border-gray-200 overflow-auto max-h-[300px]">
      <table className="w-full text-xs">
        <thead className="bg-gray-50">
          <tr>
            {columns.map((col, i) => (
              <th key={i} className="px-3 py-2 text-left font-medium text-gray-600 border-b border-gray-200 whitespace-nowrap">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="hover:bg-gray-50">
              {(row as unknown[]).map((cell, j) => (
                <td key={j} className="px-3 py-1.5 text-gray-700 border-b border-gray-100 whitespace-nowrap">
                  {String(cell ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Create Tool Card ──

function CreateToolCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const setActiveView = useUIStore((s) => s.setActiveView)
  const desc = (data.description as string) || summary

  const handleCreate = () => {
    useToolEditorStore.getState().prefill(desc)
    setActiveView('tool-editor')
  }

  return (
    <Card className="border-purple-200 bg-purple-50/50">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-purple-500" />
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="accent">新工具</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-gray-500 mb-3">{summary}</p>
        <Button size="sm" className="text-xs" onClick={handleCreate}>
          创建新工具 <ArrowRight className="h-3 w-3" />
        </Button>
      </CardBody>
    </Card>
  )
}

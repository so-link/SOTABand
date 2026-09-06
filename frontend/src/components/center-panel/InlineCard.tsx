import { useState, useEffect } from 'react'
import type { InlineCard as InlineCardType } from '@/types/chat'
import { Card, CardHeader, CardBody } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUIStore, type ActiveView } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useChatStore } from '@/stores/chat-store'
import {
  Database,
  Wrench,
  CheckCircle2,
  ArrowRight,
  FileCode,
  GitBranch,
  Activity,
  Volume2,
  Download,
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
    case 'tool-confirm':
      return <ToolConfirmCard title={title} summary={summary} data={data} />
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
    <Card className="border-maia-card-blue bg-maia-card-blue">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-blue-500" />
          <span className="text-xs font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
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
    <Card className="border-maia-card-green bg-maia-card-green">
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
            <div key={i} className="flex items-center gap-2 p-2 rounded bg-maia-surface border border-maia-border-light">
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
          <p className="text-xs text-maia-text-secondary mt-2 italic">💡 {String(data.suggestion)}</p>
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
    <Card className="border-maia-card-purple bg-maia-card-purple">
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
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
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
    <Card className="border-maia-card-amber bg-maia-card-amber">
      <CardHeader>
        <div className="flex items-center gap-2">
          <FileCode className="h-4 w-4 text-amber-500" />
          <span className="text-xs font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
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
    <Card className="border-maia-border">
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
    <Card className="border-maia-card-green bg-maia-card-green">
      <CardHeader>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="success">完成</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>

        {/* Full result data (non-image fields) */}
        <FullResult result={result} />

        {/* Image */}
        {outputFormat === 'image' ? (
          <ImageOutput data={outputData} />
        ) : null}

        {/* Table — 有 columns+rows 就渲染 */}
        {((outputData.columns as unknown[])?.length > 0 || (outputData.rows as unknown[])?.length > 0) && (
          <TableOutput data={outputData} />
        )}

        {/* Plain table format */}
        {outputFormat === 'table' && !(outputData.columns as unknown[])?.length && (
          <TableOutput data={outputData} />
        )}

        {/* File — 音频播放器 / 下载链接 */}
        {outputFormat === 'file' && (
          <FileOutput data={outputData} />
        )}
      </CardBody>
    </Card>
  )
}

// ── Full Result Display ──

function FullResult({ result }: { result: Record<string, unknown> }) {
  return (
    <details className="mt-2">
      <summary className="text-[11px] text-maia-text-muted cursor-pointer hover:text-maia-text">完整返回数据</summary>
      <pre className="mt-1 p-2 rounded bg-maia-surface border border-maia-border text-[10px] font-mono leading-relaxed max-h-[200px] overflow-auto whitespace-pre-wrap">
        {JSON.stringify(result, null, 2)}
      </pre>
    </details>
  )
}

// ── Visual Output Components ──

function ImageOutput({ data }: { data: Record<string, unknown> }) {
  const imagePath = (data.image_path || data.path) as string | undefined
  const [error, setError] = useState(false)
  if (!imagePath) return <p className="text-xs text-gray-400">图片路径不可用</p>

  const src = `${''}/api/file/image?path=${encodeURIComponent(imagePath)}`

  return (
    <div className="mt-2 rounded-lg overflow-hidden border border-maia-border">
      {error ? (
        <div className="p-3 text-xs text-maia-danger bg-red-50">
          图片加载失败: {imagePath}
        </div>
      ) : (
        <img
          src={src}
          alt="工具输出图片"
          className="w-full max-h-[400px] object-contain bg-maia-bg"
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
    <div className="mt-2 rounded-lg border border-maia-border overflow-auto max-h-[300px]">
      <table className="w-full text-xs">
        <thead className="bg-maia-bg">
          <tr>
            {columns.map((col, i) => (
              <th key={i} className="px-3 py-2 text-left font-medium text-maia-text-secondary border-b border-maia-border whitespace-nowrap">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="hover:bg-maia-bg">
              {(row as unknown[]).map((cell, j) => (
                <td key={j} className="px-3 py-1.5 text-maia-text border-b border-maia-border-light whitespace-nowrap">
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

// ── File Output (audio player / download link) ──

const AUDIO_EXTENSIONS = ['.wav', '.mp3', '.ogg', '.flac', '.aac', '.m4a', '.wma', '.webm', '.aiff', '.aifc', '.aif']

function FileOutput({ data }: { data: Record<string, unknown> }) {
  const filePath = (data.file_path || data.path || data.output_path) as string | undefined
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const fileName = filePath ? (filePath.split('/').pop() || filePath) : ''
  const ext = fileName.slice(fileName.lastIndexOf('.')).toLowerCase()
  const isAudio = AUDIO_EXTENSIONS.includes(ext)

  if (!filePath) return <p className="text-xs text-gray-400">文件路径不可用</p>

  const downloadUrl = `/api/file/download?path=${encodeURIComponent(filePath)}`

  // 音频文件通过 fetch → Blob → ObjectURL 加载，避免跨域和 Range 请求问题
  useEffect(() => {
    if (!isAudio) return
    let cancelled = false
    setLoading(true)
    fetch(`/api/file/download?path=${encodeURIComponent(filePath)}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.blob()
      })
      .then(blob => {
        if (!cancelled) {
          setAudioUrl(URL.createObjectURL(blob))
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [filePath])

  return (
    <div className="mt-2 rounded-lg border border-maia-border overflow-hidden">
      {isAudio ? (
        <div className="p-3 bg-maia-bg">
          <div className="flex items-center gap-2 mb-2">
            <Volume2 className="h-4 w-4 text-emerald-500" />
            <span className="text-xs font-medium text-maia-text">{fileName}</span>
          </div>
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-maia-text-muted py-2">
              <div className="h-3 w-3 rounded-full border-2 border-maia-border border-t-maia-accent animate-spin" />
              加载音频...
            </div>
          ) : audioUrl ? (
            <audio controls className="w-full" preload="auto" src={audioUrl}>
              <p className="text-xs text-maia-text-muted">
                您的浏览器不支持直接播放此音频格式，
                <a href={downloadUrl} target="_blank" className="text-maia-accent underline ml-1">点击下载</a>
              </p>
            </audio>
          ) : (
            <div className="text-xs text-maia-text-muted">
              音频加载失败，
              <a href={downloadUrl} target="_blank" className="text-maia-accent underline ml-1">点击下载文件</a>
            </div>
          )}
        </div>
      ) : (
        <a
          href={downloadUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 p-3 text-xs text-maia-accent hover:bg-maia-bg transition-colors"
        >
          <Download className="h-4 w-4" />
          <span className="truncate flex-1">{fileName}</span>
          <span className="text-maia-text-muted shrink-0">下载</span>
        </a>
      )}
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
    <Card className="border-maia-card-purple bg-maia-card-purple">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-purple-500" />
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="accent">新工具</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-3">{summary}</p>
        <Button size="sm" className="text-xs" onClick={handleCreate}>
          创建新工具 <ArrowRight className="h-3 w-3" />
        </Button>
      </CardBody>
    </Card>
  )
}

// ── Tool Confirm Card ──

function ToolConfirmCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const candidates = (data.candidates as Array<{ id: string; name: string; index: number }>) || []

  const handleSelect = (candidate: { id: string; name: string; index: number }) => {
    // 发送选择消息，让后端识别并继续引导参数
    sendDirectMessage(`我选择第${candidate.index}个工具：${candidate.name}`)
  }

  return (
    <Card className="border-maia-card-amber bg-maia-card-amber">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-amber-500" />
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="default">{candidates.length} 个候选</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
        <div className="space-y-1.5">
          {candidates.map((c) => (
            <button
              key={c.id}
              onClick={() => handleSelect(c)}
              className="w-full flex items-center gap-2 px-3 py-2 rounded border border-maia-border bg-maia-surface hover:bg-maia-accent/10 hover:border-maia-accent/40 transition-colors text-left group"
            >
              <span className="flex items-center justify-center h-5 w-5 rounded-full bg-maia-bg text-[11px] font-medium text-maia-text-secondary shrink-0 group-hover:bg-maia-accent group-hover:text-white">
                {c.index}
              </span>
              <span className="flex-1 truncate text-xs text-maia-text">{c.name}</span>
              <ArrowRight className="h-3 w-3 text-maia-text-muted shrink-0 group-hover:text-maia-accent" />
            </button>
          ))}
        </div>
      </CardBody>
    </Card>
  )
}

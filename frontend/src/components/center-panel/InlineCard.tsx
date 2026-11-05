import { useState, useEffect } from 'react'
import { Lightbulb } from 'lucide-react'
import type { InlineCard as InlineCardType } from '@/types/chat'
import { Card, CardHeader, CardBody } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUIStore, type ActiveView } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useChatStore } from '@/stores/chat-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'
import { useAuthStore } from '@/stores/auth-store'
import { fileUrl } from '@/types/workspace'
import { authedUrl } from '@/services/api/auth'
import {
  Database,
  Wrench,
  CheckCircle2,
  XCircle,
  ArrowRight,
  FileCode,
  GitBranch,
  Activity,
  Volume2,
  Download,
  Layers,
  ShieldAlert,
  Timer,
} from 'lucide-react'

interface InlineCardProps {
  card: InlineCardType
  /** 平台确认卡回写状态需要定位所属消息；非对话场景（Agent 详情页）可不传 */
  messageId?: string
}

export function InlineCard({ card, messageId }: InlineCardProps) {
  const { type, title, summary, data } = card
  const setActiveView = useUIStore((s) => s.setActiveView)

  switch (type) {
    case 'data-preview':
      return <DataPreviewCard title={title} summary={summary} data={data} />
    case 'tool-match':
      return <ToolMatchCard title={title} summary={summary} data={data} />
    case 'tool-confirm':
      return <ToolConfirmCard title={title} summary={summary} data={data} />
    case 'mode-selection':
      return <ModeSelectionCard title={title} summary={summary} data={data} />
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
    case 'add-workspace-tool':
      return <AddWorkspaceToolCard title={title} summary={summary} data={data} />
    case 'platform-confirm':
      return <PlatformConfirmCard cardId={card.id} title={title} summary={summary} data={data} messageId={messageId} />
    case 'platform-result':
      return <PlatformResultCard title={title} summary={summary} data={data} />
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
          <span className="text-[13px] font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>
        <div className="grid grid-cols-3 gap-2">
          {Object.entries(data).map(([key, value]) => (
            <div key={key} className="text-center">
              <div className="text-[13px] font-semibold text-maia-text">{String(value)}</div>
              <div className="text-[12px] text-maia-text-muted">{key}</div>
            </div>
          ))}
        </div>
        <Button variant="ghost" size="sm" className="mt-2 text-[13px]">
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
          <span className="text-[13px] font-medium">{title}</span>
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
                  <span className="text-[13px] font-medium truncate">{tool.name}</span>
                  <span className="text-[12px] text-maia-text-muted">v{tool.version}</span>
                  {tool.isLocal && <Badge variant="accent" className="text-[11px]">⭐本地工具</Badge>}
                </div>
              </div>
              <Badge variant="default">{tool.match}% 匹配</Badge>
            </div>
          ))}
        </div>
        {data.suggestion ? (
          <p className="flex items-start gap-1.5 text-[13px] text-maia-text-secondary mt-2">
            <Lightbulb className="h-3.5 w-3.5 shrink-0 mt-0.5 text-maia-accent" />
            <span>{String(data.suggestion)}</span>
          </p>
        ) : null}
        <div className="flex gap-2 mt-3">
          <Button size="sm" className="text-[13px]">直接执行</Button>
          <Button variant="outline" size="sm" className="text-[13px]">查看详情</Button>
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
          <GitBranch className="h-4 w-4 text-maia-accent" />
          <span className="text-[13px] font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <div className="flex items-center gap-4 mb-3">
          <div className="text-center">
            <div className="text-[13px] font-semibold text-maia-accent">{String(data.agentCount)}</div>
            <div className="text-[12px] text-maia-text-muted">Agent</div>
          </div>
          <div className="text-center">
            <div className="text-[13px] font-semibold text-maia-accent">{String(data.parallelBranches)}</div>
            <div className="text-[12px] text-maia-text-muted">并行分支</div>
          </div>
          <div className="text-center">
            <div className="text-[13px] font-semibold text-maia-accent">{String(data.estimatedTime)}</div>
            <div className="text-[12px] text-maia-text-muted">预计耗时</div>
          </div>
        </div>
        <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>
        <Button
          size="sm"
          className="text-[13px]"
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
          <span className="text-[13px] font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>
        <div className="flex gap-2">
          <Button
            size="sm"
            className="text-[13px]"
            onClick={() => setActiveView('code-review')}
          >
            在代码核验视图中打开
          </Button>
          <Button variant="outline" size="sm" className="text-[13px] text-emerald-600">
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
          <span className="text-[13px] font-medium">{title}</span>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary">{summary}</p>
        <div className="mt-2 h-1.5 bg-maia-border rounded-full overflow-hidden">
          <div className="h-full bg-maia-accent rounded-full animate-pulse" style={{ width: '60%' }} />
        </div>
      </CardBody>
    </Card>
  )
}

// ---- Result Summary Card ----

function ResultSummaryCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const result = (data.result as Record<string, unknown>) || {}
  // 失败判定：data 显式带 error，或工具返回了**明确的非 success** status。
  // status 缺失（旧卡片、空 result {}）视为中性/成功——不能把无法判定的
  // 历史数据一律标红（后端工具失败走独立 error 事件，不落此卡）。
  const hasError = data.error !== undefined && data.error !== null && data.error !== ''
  const resultStatus = typeof result.status === 'string' ? result.status : ''
  const failed = hasError || (!!resultStatus && resultStatus !== 'success')
  const outputFormat = result.output_format as string || ''
  const outputData = (result.data as Record<string, unknown>) || {}

  return (
    <Card className={failed ? 'border-maia-danger bg-maia-danger-bg' : 'border-maia-card-green bg-maia-card-green'}>
      <CardHeader>
        <div className="flex items-center gap-2">
          {failed
            ? <XCircle className="h-4 w-4 text-maia-danger" />
            : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          <span className="text-[13px] font-medium">{title}</span>
          <Badge variant={failed ? 'danger' : 'success'}>{failed ? '失败' : '完成'}</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className={`text-[13px] mb-2 ${failed ? 'text-maia-danger' : 'text-maia-text-secondary'}`}>{summary}</p>

        {/* Full result data (non-image fields) — 失败且无 result 时不展示空对象 */}
        {!failed || Object.keys(result).length > 0 ? <FullResult result={result} /> : null}

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
      <summary className="text-[12px] text-maia-text-muted cursor-pointer hover:text-maia-text">完整返回数据</summary>
      <pre className="mt-1 p-2 rounded bg-maia-surface border border-maia-border text-[13px] font-mono leading-[22px] max-h-[200px] overflow-auto whitespace-pre-wrap">
        {JSON.stringify(result, null, 2)}
      </pre>
    </details>
  )
}

// ── Visual Output Components ──

/** 从卡片 data 中取后端注入的 file_id（后端 register_result_paths 注入
 *  `<key>_id` 与驼峰 `<camel>Id` 两种别名），全都没有则返回 null。 */
function pickFileId(data: Record<string, unknown>, pathKeys: string[]): string | null {
  for (const key of pathKeys) {
    const snake = data[`${key}_id`]
    if (typeof snake === 'string' && snake) return snake
    const head = key.replace(/_path$/, '')
    const camel = head.split('_')[0] + head.split('_').slice(1).map(w => w[0].toUpperCase() + w.slice(1)).join('') + 'Id'
    const camelVal = data[camel]
    if (typeof camelVal === 'string' && camelVal) return camelVal
  }
  return null
}

const PATH_KEYS = ['image_path', 'file_path', 'output_path', 'audio_path', 'plot_path']

function ImageOutput({ data }: { data: Record<string, unknown> }) {
  const imagePath = (data.image_path || data.path) as string | undefined
  const fileId = pickFileId(data, PATH_KEYS)
  const [error, setError] = useState(false)
  if (!fileId) return <p className="text-[13px] text-maia-text-muted">图片已不可用（旧卡片，无文件凭证）</p>

  // <img> 原生请求带不了 Authorization 头：多用户模式追加 ?auth_token=
  const src = authedUrl(fileUrl(fileId))

  return (
    <div className="mt-2 rounded-lg overflow-hidden border border-maia-border">
      {error ? (
        <div className="p-3 text-[13px] text-maia-danger bg-maia-danger-bg">
          图片加载失败: {imagePath || fileId}
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

  if (columns.length === 0) return <p className="text-[13px] text-maia-text-muted">表格数据不可用</p>

  return (
    <div className="mt-2 rounded-lg border border-maia-border overflow-auto max-h-[300px]">
      <table className="w-full text-[13px]">
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
  const fileId = pickFileId(data, PATH_KEYS)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // 文件名仍从路径字段取 basename（仅展示用，无访问能力）
  const fileName = filePath ? (filePath.split('/').pop() || filePath) : ''
  const ext = fileName.slice(fileName.lastIndexOf('.')).toLowerCase()
  const isAudio = AUDIO_EXTENSIONS.includes(ext)

  if (!fileId) return <p className="text-[13px] text-maia-text-muted">文件已不可用（旧卡片，无文件凭证）</p>

  // 供 <a href> 直链/点击下载使用：追加 auth_token（fetch 走头注入，参数无害）
  const downloadUrl = authedUrl(fileUrl(fileId))

  // 音频文件通过 fetch → Blob → ObjectURL 加载，避免跨域和 Range 请求问题
  useEffect(() => {
    if (!isAudio) return
    let cancelled = false
    setLoading(true)
    fetch(downloadUrl)
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
  }, [downloadUrl, isAudio])

  return (
    <div className="mt-2 rounded-lg border border-maia-border overflow-hidden">
      {isAudio ? (
        <div className="p-3 bg-maia-bg">
          <div className="flex items-center gap-2 mb-2">
            <Volume2 className="h-4 w-4 text-emerald-500" />
            <span className="text-[13px] font-medium text-maia-text">{fileName}</span>
          </div>
          {loading ? (
            <div className="flex items-center gap-2 text-[13px] text-maia-text-muted py-2">
              <div className="h-3 w-3 rounded-full border-2 border-maia-border border-t-maia-accent animate-spin" />
              加载音频...
            </div>
          ) : audioUrl ? (
            <audio controls className="w-full" preload="auto" src={audioUrl}>
              <p className="text-[13px] text-maia-text-muted">
                您的浏览器不支持直接播放此音频格式，
                <a href={downloadUrl} target="_blank" className="text-maia-accent underline ml-1">点击下载</a>
              </p>
            </audio>
          ) : (
            <div className="text-[13px] text-maia-text-muted">
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
          className="flex items-center gap-2 p-3 text-[13px] text-maia-accent hover:bg-maia-bg transition-colors"
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
    if (!useAuthStore.getState().requireLogin()) return
    useToolEditorStore.getState().prefill(desc)
    setActiveView('tool-editor')
  }

  return (
    <Card className="border-maia-card-purple bg-maia-card-purple">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-maia-accent" />
          <span className="text-[13px] font-medium">{title}</span>
          <Badge variant="accent">新工具</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary mb-3">{summary}</p>
        <Button size="sm" className="text-[13px]" onClick={handleCreate}>
          创建新工具 <ArrowRight className="h-3 w-3" />
        </Button>
      </CardBody>
    </Card>
  )
}

// ── Add Workspace Tool Card（工具仓库有货但不在工具空间） ──

function AddWorkspaceToolCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const addTool = useWorkspaceToolStore((s) => s.addTool)
  const request = String(data.request || '')
  const candidates = (data.candidates as Array<{ id: string; name: string; tags?: string[]; index: number }>) || []
  // 记录已加入的工具 id，按钮变灰避免重复点击
  const [added, setAdded] = useState<string[]>([])

  const handleAdd = (t: { id: string; name: string; tags?: string[] }) => {
    addTool({ id: t.id, name: t.name, tags: t.tags || [], loadedAt: new Date().toISOString() })
    setAdded((prev) => [...prev, t.id])
    // 加入后直接重发原始需求，让助手用新工具继续处理
    if (request) sendDirectMessage(request)
  }

  return (
    <Card className="border-maia-card-blue bg-maia-card-blue">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-blue-500" />
          <span className="text-[13px] font-medium">{title}</span>
          <Badge variant="default">{candidates.length} 个候选</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>
        <div className="space-y-1.5">
          {candidates.map((t) => {
            const done = added.includes(t.id)
            return (
              <button
                key={t.id}
                onClick={() => !done && handleAdd(t)}
                disabled={done}
                className="w-full flex items-center gap-2 px-3 py-2 rounded border border-maia-border bg-maia-surface hover:bg-maia-accent/10 hover:border-maia-accent/40 transition-colors text-left group disabled:opacity-60 disabled:cursor-default"
              >
                <span className="flex-1 truncate text-[13px] text-maia-text">{t.name}</span>
                {done
                  ? <span className="flex items-center gap-1 text-[12px] text-emerald-600 shrink-0"><CheckCircle2 className="h-3.5 w-3.5" />已加入</span>
                  : <span className="flex items-center gap-1 text-[12px] text-maia-accent shrink-0">加入工具空间 <ArrowRight className="h-3 w-3" /></span>}
              </button>
            )
          })}
        </div>
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
          <span className="text-[13px] font-medium">{title}</span>
          <Badge variant="default">{candidates.length} 个候选</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>
        <div className="space-y-1.5">
          {candidates.map((c) => (
            <button
              key={c.id}
              onClick={() => handleSelect(c)}
              className="w-full flex items-center gap-2 px-3 py-2 rounded border border-maia-border bg-maia-surface hover:bg-maia-accent/10 hover:border-maia-accent/40 transition-colors text-left group"
            >
              <span className="flex items-center justify-center h-5 w-5 rounded-full bg-maia-bg text-[12px] font-medium text-maia-text-secondary shrink-0 group-hover:bg-maia-accent group-hover:text-white">
                {c.index}
              </span>
              <span className="flex-1 truncate text-[13px] text-maia-text">{c.name}</span>
              <ArrowRight className="h-3 w-3 text-maia-text-muted shrink-0 group-hover:text-maia-accent" />
            </button>
          ))}
        </div>
      </CardBody>
    </Card>
  )
}

// ── Mode Selection Card ──

function ModeSelectionCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const modes = (data.modes as Array<{ id: string; name: string; desc?: string; index: number }>) || []

  const handleSelect = (mode: { name: string; index: number }) => {
    // 发送选择消息（回复模式名称），由后端解析并继续
    sendDirectMessage(mode.name)
  }

  return (
    <Card className="border-maia-card-blue bg-maia-card-blue">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-blue-500" />
          <span className="text-[13px] font-medium">{title}</span>
          <Badge variant="default">{modes.length} 个模式</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>
        <div className="space-y-1.5">
          {modes.map((m) => (
            <button
              key={m.id || m.name}
              onClick={() => handleSelect(m)}
              className="w-full flex items-start gap-2 px-3 py-2 rounded border border-maia-border bg-maia-surface hover:bg-maia-accent/10 hover:border-maia-accent/40 transition-colors text-left group"
            >
              <span className="flex items-center justify-center h-5 w-5 rounded-full bg-maia-bg text-[12px] font-medium text-maia-text-secondary shrink-0 group-hover:bg-maia-accent group-hover:text-white">
                {m.index}
              </span>
              <span className="flex-1 min-w-0">
                <span className="block text-[13px] text-maia-text font-medium">{m.name}</span>
                {m.desc && (
                  <span className="block text-[12px] text-maia-text-muted mt-0.5 leading-snug">{m.desc}</span>
                )}
              </span>
              <ArrowRight className="h-3 w-3 text-maia-text-muted shrink-0 mt-0.5 group-hover:text-maia-accent" />
            </button>
          ))}
        </div>
      </CardBody>
    </Card>
  )
}

// ── Platform Confirm Card（对话内平台写操作的二次确认卡） ──

const CONFIRM_STATUS_LABEL: Record<string, { text: string; variant: 'success' | 'default' | 'danger' }> = {
  confirmed: { text: '已确认', variant: 'success' },
  cancelled: { text: '已取消', variant: 'default' },
  failed: { text: '未执行成功', variant: 'danger' },
  superseded: { text: '已被新操作顶替', variant: 'default' },
}

function PlatformConfirmCard({
  cardId, title, summary, data, messageId,
}: { cardId: string; title: string; summary: string; data: Record<string, unknown>; messageId?: string }) {
  const resolvePlatformOp = useChatStore((s) => s.resolvePlatformOp)
  const risk = String(data.risk || 'write')
  const isDanger = risk === 'danger'
  const status = String(data.status || 'pending')
  const submitting = data.submitting as 'confirm' | 'cancel' | undefined
  const error = data.error as string | undefined
  const tool = String(data.tool || '')
  const argsView = (data.args_view as Record<string, unknown>) || {}
  const expiresAt = Number(data.expires_at || 0)

  const [ack, setAck] = useState(false) // 高危操作须勾选后才能确认
  const [now, setNow] = useState(() => Date.now())

  const resolved = status !== 'pending'

  // 倒计时以 expires_at（秒级时间戳）为准，刷新/恢复后依然正确
  useEffect(() => {
    if (resolved || !expiresAt) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [resolved, expiresAt])

  const remain = expiresAt ? Math.max(0, Math.floor(expiresAt - now / 1000)) : 0
  const expired = !resolved && expiresAt > 0 && remain <= 0
  const mm = String(Math.floor(remain / 60)).padStart(2, '0')
  const ss = String(remain % 60).padStart(2, '0')

  const canConfirm = !resolved && !expired && !submitting && !!messageId && (!isDanger || ack)
  const canCancel = !resolved && !expired && !submitting && !!messageId

  const badge = resolved
    ? CONFIRM_STATUS_LABEL[status] || { text: status, variant: 'default' as const }
    : expired
      ? { text: '已过期', variant: 'default' as const }
      : isDanger
        ? { text: '高危操作', variant: 'danger' as const }
        : { text: '需确认后执行', variant: 'warning' as const }

  return (
    <Card className={isDanger ? 'border-maia-danger bg-maia-danger-bg' : 'border-maia-card-amber bg-maia-card-amber'}>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldAlert className={`h-4 w-4 ${isDanger ? 'text-maia-danger' : 'text-amber-500'}`} />
          <span className="text-[13px] font-medium">{title}</span>
          <Badge variant={badge.variant}>{badge.text}</Badge>
          {!resolved && !expired && expiresAt > 0 && (
            <span className="ml-auto flex items-center gap-1 text-[12px] text-maia-text-muted">
              <Timer className="h-3 w-3" />
              {mm}:{ss}
            </span>
          )}
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>
        <div className="flex items-center gap-1.5 mb-2">
          <span className="text-[12px] text-maia-text-muted">工具</span>
          <code className="text-[12px] font-mono text-maia-accent">{tool}</code>
        </div>
        {Object.keys(argsView).length > 0 && (
          <div className="rounded border border-maia-border bg-maia-surface divide-y divide-maia-border-light mb-2">
            {Object.entries(argsView).map(([k, v]) => (
              <div key={k} className="flex items-start gap-2 px-2.5 py-1.5">
                <span className="text-[12px] text-maia-text-muted shrink-0 w-24 truncate" title={k}>{k}</span>
                <span className="text-[12px] font-mono text-maia-text break-all">{typeof v === 'string' ? v : JSON.stringify(v)}</span>
              </div>
            ))}
          </div>
        )}
        {isDanger && !resolved && !expired && (
          <label className="flex items-center gap-2 mb-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
              className="accent-maia-danger"
            />
            <span className="text-[12px] text-maia-danger">我已了解此操作不可恢复，确认执行</span>
          </label>
        )}
        {error && <p className="text-[12px] text-maia-danger mb-2">{error}</p>}
        {!resolved && !expired && (
          <div className="flex gap-2">
            <Button
              size="sm"
              variant={isDanger ? 'danger' : 'default'}
              className="text-[13px]"
              disabled={!canConfirm}
              onClick={() => messageId && resolvePlatformOp(messageId, cardId, 'confirm')}
            >
              {submitting === 'confirm' ? '执行中…' : '确认执行'}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="text-[13px]"
              disabled={!canCancel}
              onClick={() => messageId && resolvePlatformOp(messageId, cardId, 'cancel')}
            >
              {submitting === 'cancel' ? '取消中…' : '取消'}
            </Button>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

// ── Platform Result Card（网关回执：平台操作的唯一可信结果） ──

/** 结果卡跳转链接：按工具名映射到对应空间视图 */
const RESULT_GOTO: Record<string, { label: string; view?: ActiveView }> = {
  register_dataset: { label: '在数据空间中查看' },
  register_model: { label: '在模型空间中查看' },
  create_tool: { label: '在工具空间中查看' },
  modify_tool: { label: '在工具空间中查看' },
  create_agent: { label: '在 Agent 空间中查看' },
  create_cron_job: { label: '在定时任务中查看', view: 'cron-jobs' },
  update_cron_job: { label: '在定时任务中查看', view: 'cron-jobs' },
}

function PlatformResultCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const setActiveView = useUIStore((s) => s.setActiveView)
  const setLeftPanelOpen = useUIStore((s) => s.setLeftPanelOpen)
  const setLeftPanelTab = useUIStore((s) => s.setLeftPanelTab)
  const ok = data.status === 'ok'
  const tool = String(data.tool || '')
  const error = data.error as string | undefined
  const result = data.result
  const goto = ok ? RESULT_GOTO[tool] : undefined

  const handleGoto = () => {
    if (!goto) return
    if (goto.view) setActiveView(goto.view)
    else {
      // 数据集/模型/工具/Agent 都在左侧资源面板的分区里
      setLeftPanelOpen(true)
      setLeftPanelTab('resources')
    }
  }

  return (
    <Card className={ok ? 'border-maia-card-green bg-maia-card-green' : 'border-maia-danger bg-maia-danger-bg'}>
      <CardHeader>
        <div className="flex items-center gap-2">
          {ok
            ? <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            : <XCircle className="h-4 w-4 text-maia-danger" />}
          <span className="text-[13px] font-medium">{title}</span>
          <Badge variant={ok ? 'success' : 'danger'}>{ok ? '成功' : '失败'}</Badge>
        </div>
      </CardHeader>
      <CardBody>
        {summary && <p className="text-[13px] text-maia-text-secondary mb-2">{summary}</p>}
        {tool && (
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-[12px] text-maia-text-muted">工具</span>
            <code className="text-[12px] font-mono text-maia-accent">{tool}</code>
          </div>
        )}
        {error && <p className="text-[13px] text-maia-danger mb-2">{error}</p>}
        {ok && result != null && (
          <details className="mb-2">
            <summary className="text-[12px] text-maia-text-muted cursor-pointer hover:text-maia-text">执行回执</summary>
            <pre className="mt-1 p-2 rounded bg-maia-surface border border-maia-border text-[12px] font-mono leading-[20px] max-h-[160px] overflow-auto whitespace-pre-wrap">
              {JSON.stringify(result, null, 2)}
            </pre>
          </details>
        )}
        {goto && (
          <Button size="sm" variant="outline" className="text-[13px]" onClick={handleGoto}>
            {goto.label} <ArrowRight className="h-3 w-3" />
          </Button>
        )}
      </CardBody>
    </Card>
  )
}

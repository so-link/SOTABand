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
  Clock3,
  CircleStop,
  AlertCircle,
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
    case 'model-confirm':
      return <ModelConfirmCard title={title} summary={summary} data={data} />
    case 'model-parameters':
      return <ModelParametersCard title={title} summary={summary} data={data} />
    case 'training-parameters':
      return <TrainingParametersCard title={title} summary={summary} data={data} />
    case 'raw-processing-parameters':
      return <RawProcessingParametersCard title={title} summary={summary} data={data} />
    case 'workflow-action':
      return <WorkflowActionCard title={title} summary={summary} data={data} />
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
    case 'dataset-summary':
    case 'dataset-register':
    case 'model-resource':
      return <ResourceWorkflowCard type={type} title={title} summary={summary} data={data} />
    case 'model-catalog':
      return <ModelCatalogCard title={title} summary={summary} data={data} />
    case 'task-progress':
      return <TaskProgressCard title={title} summary={summary} data={data} />
    case 'evaluation-result':
      return <EvaluationResultCard title={title} summary={summary} data={data} />
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

type TaskState = {
  id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message: string
  created_at?: string
  started_at?: string
  finished_at?: string
  log_tail?: string
  metadata?: Record<string, unknown>
}

function TaskProgressCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const initialTask = (data.task as TaskState | undefined)
  const taskId = String(data.task_id || initialTask?.id || '')
  const [task, setTask] = useState<TaskState | null>(initialTask || null)
  const [pollError, setPollError] = useState('')

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    let timer: number | undefined
    const poll = async () => {
      try {
        const response = await fetch(`/api/task/${encodeURIComponent(taskId)}`)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const payload = await response.json()
        if (cancelled) return
        const next = payload.task as TaskState
        setTask(next)
        setPollError('')
        if (next.status === 'queued' || next.status === 'running') {
          timer = window.setTimeout(poll, 1500)
        }
      } catch (error) {
        if (cancelled) return
        setPollError(error instanceof Error ? error.message : String(error))
        timer = window.setTimeout(poll, 3000)
      }
    }
    poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [taskId])

  const stopTask = async () => {
    if (!taskId) return
    const response = await fetch(`/api/task/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' })
    if (response.ok) {
      const payload = await response.json()
      setTask(payload.task as TaskState)
    }
  }

  const status = task?.status || 'queued'
  const progress = Math.max(0, Math.min(100, Number(task?.progress || 0)))
  const active = status === 'queued' || status === 'running'
  const modelResourceId = String(task?.metadata?.model_resource_id || '')
  const artifactPath = String(task?.metadata?.artifact_path || '')
  // The task endpoint is authoritative after polling. The initial card data
  // can contain a stale generic action from before the worker was submitted.
  const nextAction = String(task?.metadata?.next_action || data.next_action || '继续')
  const nextActionLabel = String(
    task?.metadata?.next_action_label ||
    data.next_action_label ||
    (nextAction === '开始评测' ? '开始评测' : '扫描处理结果'),
  )
  const statusLabel: Record<TaskState['status'], string> = {
    queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消',
  }

  return (
    <Card className="border-maia-border bg-maia-surface">
      <CardHeader>
        <div className="flex items-center gap-2">
          {active ? <Activity className="h-4 w-4 text-blue-500 animate-pulse" /> :
            status === 'completed' ? <CheckCircle2 className="h-4 w-4 text-maia-success" /> :
              <AlertCircle className="h-4 w-4 text-maia-danger" />}
          <span className="text-xs font-medium flex-1">{title}</span>
          <Badge variant={status === 'completed' ? 'success' : 'default'}>{statusLabel[status]}</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary">{task?.message || summary}</p>
        <div className="mt-2 h-1.5 bg-maia-border rounded-full overflow-hidden">
          <div className="h-full bg-blue-500 transition-[width] duration-500" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-2 flex items-center gap-2 text-[10px] text-maia-text-muted">
          <Clock3 className="h-3 w-3" />
          <span>{progress}%</span>
          <span className="font-mono truncate">{taskId}</span>
          {active && (
            <Button variant="ghost" size="sm" className="ml-auto h-6 text-[10px] text-maia-danger" onClick={stopTask}>
              <CircleStop className="h-3 w-3" /> 停止
            </Button>
          )}
        </div>
        {pollError && <p className="mt-2 text-[10px] text-maia-danger">进度刷新失败: {pollError}</p>}
        {status === 'completed' && (
          modelResourceId && (
            <p className="mt-2 text-[10px] text-maia-text-muted">
              模型资源：{modelResourceId}
              {artifactPath ? `；产物目录：${artifactPath}` : ''}
            </p>
          )
        )}
        {status === 'completed' && (
          <Button
            variant="outline"
            size="sm"
            className="mt-3 h-7 text-[11px]"
            onClick={() => sendDirectMessage(nextAction)}
          >
            <ArrowRight className="h-3 w-3" />{nextActionLabel}
          </Button>
        )}
        {task?.log_tail && (
          <details className="mt-2">
            <summary className="text-[10px] text-maia-text-muted cursor-pointer">查看最近日志</summary>
            <pre className="mt-1 max-h-36 overflow-auto whitespace-pre-wrap text-[10px] bg-maia-bg p-2 border border-maia-border">{task.log_tail}</pre>
          </details>
        )}
      </CardBody>
    </Card>
  )
}

function ResourceWorkflowCard({
  type, title, summary, data,
}: { type: string; title: string; summary: string; data: Record<string, unknown> }) {
  const fields = type === 'model-resource'
    ? ['model_id', 'model_name', 'model_type', 'threshold', 'artifact_path']
    : ['dataset_id', 'dataset_name', 'source_path', 'generated_at', 'stats_status', 'sequence_length', 'window_seconds', 'sampling_hz', 'prediction_interval_seconds', 'window_count', 'hypoxemia_window_count', 'non_hypoxemia_window_count', 'hypoxemia_ratio', 'split_mode', 'group_key']
  const labels: Record<string, string> = {
    dataset_id: '数据集 ID', dataset_name: '数据集名称', source_path: '数据路径', generated_at: '生成时间',
    sequence_length: '序列长度', window_seconds: '窗口长度（秒）', sampling_hz: '采样率（Hz）', stats_status: '窗口统计',
    prediction_interval_seconds: '预测间隔（秒）', window_count: '窗口总数',
    hypoxemia_window_count: '低氧窗口数', non_hypoxemia_window_count: '非低氧窗口数',
    hypoxemia_ratio: '低氧窗口比例', split_mode: '划分方式', group_key: '分组键',
  }
  return (
    <Card className="border-maia-card-blue bg-maia-card-blue">
      <CardHeader>
        <div className="flex items-center gap-2">
          {type === 'model-resource' ? <Activity className="h-4 w-4 text-blue-500" /> : <Database className="h-4 w-4 text-blue-500" />}
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="success">就绪</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          {fields.filter((key) => data[key] !== undefined && data[key] !== '' && data[key] !== null).map((key) => (
            <div key={key} className="min-w-0">
              <div className="text-[10px] text-maia-text-muted">{labels[key] || key}</div>
              <div className="text-xs text-maia-text truncate" title={String(data[key])}>{String(data[key])}</div>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  )
}

function ModelCatalogCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const models = (data.models as Array<{
    model_name: string
    label: string
    available: boolean
    verified_baseline: boolean
  }>) || []
  const available = models.filter((model) => model.available)

  return (
    <Card className="border-maia-card-blue bg-maia-card-blue">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-blue-500" />
          <span className="text-xs font-medium flex-1">{title}</span>
          <Badge variant="success">{available.length} 可用</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
        <div className="max-h-64 overflow-auto border border-maia-border bg-maia-surface">
          {available.map((model) => (
            <button
              key={model.model_name}
              className="w-full flex items-center gap-2 px-3 py-2 text-left border-b border-maia-border-light last:border-b-0 hover:bg-maia-accent/10"
              onClick={() => sendDirectMessage(`创建五通道 ${model.model_name} 模型资源`)}
              title={`创建 ${model.model_name} 模型资源`}
            >
              <span className="min-w-0 flex-1 text-xs text-maia-text truncate">{model.model_name}</span>
              {model.verified_baseline && <Badge variant="accent" className="text-[9px]">基线</Badge>}
              <ArrowRight className="h-3 w-3 shrink-0 text-maia-text-muted" />
            </button>
          ))}
        </div>
      </CardBody>
    </Card>
  )
}

function EvaluationResultCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const test = (data.test as Record<string, unknown>) || {}
  const currentMetrics = (data.selected_metrics as Record<string, unknown>) || {}
  const displayedMetrics = Object.keys(test).length ? test : currentMetrics
  const currentCount = data.dataset_window_count
  const metrics = ['auc', 'f1', 'accuracy', 'precision', 'recall']
  return (
    <Card className="border-maia-card-green bg-maia-card-green">
      <CardHeader>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-maia-success" />
          <span className="text-xs font-medium flex-1">{title}</span>
          <Badge variant="success">评测完成</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-3">{summary}</p>
        {currentCount !== undefined && (
          <div className="mb-3 text-[11px] text-maia-text-secondary">
            <p>本次评测数据：{String(currentCount)} 个验证集窗口（低氧 {String(data.dataset_positive_count ?? '-')}，非低氧 {String(data.dataset_negative_count ?? '-')}）</p>
            <p className="mt-1 text-[10px] text-maia-text-muted">{String(data.evaluation_scope_label || '训练集用于训练，测试集保留作独立最终测评')}</p>
            {data.source_dataset_window_count !== undefined && (
              <p className="mt-1 text-[10px] text-maia-text-muted">输入数据集总窗口：{String(data.source_dataset_window_count)}</p>
            )}
          </div>
        )}
        <div className="grid grid-cols-3 gap-2">
          <div className="border-r border-maia-border">
            <div className="text-lg font-semibold text-maia-text">{formatMetric(data.threshold)}</div>
            <div className="text-[10px] text-maia-text-muted">验证集阈值</div>
          </div>
          {metrics.filter((key) => displayedMetrics[key] !== undefined).map((key) => (
            <div key={key}>
              <div className="text-lg font-semibold text-maia-text">{formatMetric(displayedMetrics[key])}</div>
              <div className="text-[10px] text-maia-text-muted">{Object.keys(test).length ? `test ${key}` : `本次数据 ${key}`}</div>
            </div>
          ))}
        </div>
        {typeof data.file_path === 'string' && (
          <a
            href={`/api/file/download?path=${encodeURIComponent(data.file_path)}`}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-flex items-center gap-1 text-xs text-maia-accent underline"
          >
            <Download className="h-3 w-3" />下载评测 Excel
          </a>
        )}
        <p className="mt-3 text-[10px] text-maia-text-muted">研究用途，不用于临床诊断</p>
      </CardBody>
    </Card>
  )
}

function formatMetric(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(4) : String(value ?? '-')
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

function ModelConfirmCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const candidates = (data.candidates as Array<{ id: string; name: string; index: number }>) || []

  const handleSelect = (candidate: { id: string; name: string; index: number }) => {
    sendDirectMessage(`我选择：${candidate.name}`)
  }

  return (
    <Card className="border-maia-card-blue bg-maia-card-blue">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-blue-500" />
          <span className="text-xs font-medium">{title}</span>
          <Badge variant="default">{candidates.length} 个候选</Badge>
        </div>
      </CardHeader>
      <CardBody>
        <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
        <div className="space-y-1.5 max-h-80 overflow-y-auto">
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

function ModelParametersCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const architecture = (data.architecture as Record<string, unknown>) || {}
  const architectureGroups = (data.architecture_groups as Record<string, string[]>) || {}
  const valueText = (value: unknown) => Array.isArray(value) ? JSON.stringify(value) : String(value ?? '-')
  const renderGroups = (groups: Record<string, string[]>, source: Record<string, unknown>) =>
    Object.entries(groups).map(([group, keys]) => (
      <div key={group} className="rounded border border-maia-border bg-maia-surface p-2">
        <div className="text-xs font-medium text-maia-text mb-1">{group}</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1">
          {keys.filter((key) => key in source).map((key) => <div key={key} className="text-[11px] text-maia-text-secondary break-all">
            <span className="font-mono">{key}</span>: {valueText(source[key])}
          </div>)}
        </div>
      </div>
    ))

  return <Card className="border-maia-card-blue bg-maia-card-blue">
    <CardHeader><span className="text-xs font-medium">{title}</span></CardHeader>
    <CardBody>
      <p className="text-xs text-maia-text-secondary mb-2">{summary} 架构修改请用自然语言描述，系统会按当前模型的专属参数进行校验。</p>
      <div className="max-h-96 overflow-y-auto space-y-2">
        <div className="text-xs font-semibold">架构参数</div>
        {renderGroups(architectureGroups, architecture)}
      </div>
    </CardBody>
  </Card>
}

function TrainingParametersCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const training = (data.training as Record<string, unknown>) || {}
  const groups = (data.training_groups as Record<string, string[]>) || {}
  const specs = (data.parameter_specs as Record<string, { min?: number; max?: number; step?: number }>) || {}
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(
    Object.entries(training).map(([key, value]) => [key, Array.isArray(value) ? JSON.stringify(value) : String(value ?? '')]),
  ))
  const submit = () => {
    const output: Record<string, unknown> = {}
    for (const [key, original] of Object.entries(training)) {
      const raw = (values[key] || '').trim()
      if (!raw) continue
      if (typeof original === 'boolean') output[key] = raw === 'true'
      else if (typeof original === 'number') output[key] = Number(raw)
      else output[key] = raw
    }
    sendDirectMessage(`训练参数：${JSON.stringify(output)}`)
  }
  return <Card className="border-maia-card-blue bg-maia-card-blue">
    <CardHeader><span className="text-xs font-medium">{title}</span></CardHeader>
    <CardBody>
      <p className="text-xs text-maia-text-secondary mb-2">{summary} 清空字段将恢复默认值。</p>
      <div className="max-h-96 overflow-y-auto space-y-2">
        {Object.entries(groups).map(([group, keys]) => <div key={group} className="rounded border border-maia-border bg-maia-surface p-2">
          <div className="text-xs font-medium text-maia-text mb-1">{group}</div>
          <div className="grid grid-cols-1 gap-2">
            {keys.filter((key) => key in training).map((key) => {
              const original = training[key]
              const spec = specs[key] || {}
              const boolean = typeof original === 'boolean'
              return <label key={key} className="grid grid-cols-[minmax(130px,1fr)_minmax(120px,1fr)] gap-2 items-center text-[11px]">
                <span className="text-maia-text"><span className="font-medium">{key}</span><span className="block text-[10px] text-maia-text-muted">当前值：{String(original)}</span></span>
                {boolean ? <input type="checkbox" checked={values[key] === 'true'} onChange={(event) => setValues((current) => ({ ...current, [key]: String(event.target.checked) }))} /> :
                  <input value={values[key] || ''} type={typeof original === 'number' ? 'number' : 'text'} min={spec.min} max={spec.max} step={spec.step} onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} className="h-7 rounded border border-maia-border bg-maia-bg px-2 text-[11px] text-maia-text" />}
              </label>
            })}
          </div>
        </div>)}
      </div>
      <Button variant="outline" size="sm" className="mt-3 h-7 text-[11px]" onClick={submit}>
        <ArrowRight className="h-3 w-3" />提交训练参数
      </Button>
    </CardBody>
  </Card>
}

function RawProcessingParametersCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const parameters = (data.parameters as Array<{ key: string; label: string; value: unknown; help?: string; min?: number; max?: number; step?: number }>) || []
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(parameters.map((item) => [item.key, Array.isArray(item.value) ? JSON.stringify(item.value) : String(item.value ?? '')]))
  )
  const submit = () => {
    const payload: Record<string, string> = {}
    for (const item of parameters) {
      const value = (values[item.key] || '').trim()
      if (value) payload[item.key] = value
    }
    sendDirectMessage(`原始处理参数：${JSON.stringify(payload)}`)
  }
  return <Card className="border-maia-card-blue bg-maia-card-blue">
    <CardHeader><span className="text-xs font-medium">{title}</span></CardHeader>
    <CardBody>
      <p className="text-xs text-maia-text-secondary mb-2">{summary}</p>
      <div className="max-h-96 overflow-y-auto space-y-2">
        {parameters.map((item) => (
          <label key={item.key} className="grid grid-cols-[minmax(150px,1fr)_minmax(120px,1fr)] gap-2 items-center text-[11px]">
            <span className="text-maia-text"><span className="font-medium">{item.label}</span><span className="block font-mono text-[10px] text-maia-text-muted">{item.key}</span></span>
            <input
              value={values[item.key] || ''}
              type={item.min !== undefined || item.max !== undefined ? 'number' : 'text'}
              min={item.min}
              max={item.max}
              step={item.step}
              onChange={(event) => setValues((current) => ({ ...current, [item.key]: event.target.value }))}
              title={item.help}
              className="h-7 rounded border border-maia-border bg-maia-bg px-2 text-[11px] text-maia-text"
            />
          </label>
        ))}
      </div>
      <Button variant="outline" size="sm" className="mt-3 h-7 text-[11px]" onClick={submit}>
        <ArrowRight className="h-3 w-3" />提交处理参数
      </Button>
    </CardBody>
  </Card>
}

function WorkflowActionCard({ title, summary, data }: { title: string; summary: string; data: Record<string, unknown> }) {
  const sendDirectMessage = useChatStore((s) => s.sendDirectMessage)
  const action = String(data.action || '')
  const label = String(data.label || action)
  return <Card className="border-maia-card-blue bg-maia-card-blue">
    <CardHeader><span className="text-xs font-medium">{title}</span></CardHeader>
    <CardBody>
      <p className="text-xs text-maia-text-secondary">{summary}</p>
      <Button variant="outline" size="sm" className="mt-3 h-7 text-[11px]" onClick={() => sendDirectMessage(action)}>
        <ArrowRight className="h-3 w-3" />{label}
      </Button>
    </CardBody>
  </Card>
}

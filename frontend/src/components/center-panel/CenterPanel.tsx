import { useCallback, useEffect, useRef, useState } from 'react'
import { useUIStore, type ActiveView, parseAgentDetailView, parseToolDetailView, parseModelDetailView } from '@/stores/ui-store'
import { useResourceStore } from '@/stores/resource-store'
import { ChatView } from './ChatView'
import { DataPreviewView } from './DataPreviewView'
import { FilePreviewView } from './FilePreviewView'
import { CodeReviewView } from './CodeReviewView'
import { OrchestrationView } from './OrchestrationView'
import { TaskMonitorView } from './TaskMonitorView'
import { AgentEditorView } from './AgentEditorView'
import { AgentDetailView } from './AgentDetailView'
import { ToolEditorView } from './ToolEditorView'
import { ToolDetailView } from './ToolDetailView'
import { ModelDetailView } from './ModelDetailView'
import { DatasetEditorView } from './DatasetEditorView'
import { RepositoryView } from './RepositoryView'
import { DatasetRepositoryView } from './DatasetRepositoryView'
import { SettingsView } from './SettingsView'
import { MessageSquare, BarChart3, Search, GitBranch, Radio, Bot, X, Package, Settings, PanelLeft, PanelRight, ChevronLeft, ChevronRight, BrainCircuit } from 'lucide-react'

const TAB_CONFIG: Record<ActiveView, { label: string; icon: typeof MessageSquare }> = {
  chat:            { label: '对话',    icon: MessageSquare },
  'data-preview':  { label: '数据',    icon: BarChart3 },
  'file-preview':  { label: '预览',    icon: BarChart3 },
  'code-review':   { label: '核验',    icon: Search },
  orchestration:   { label: '编排',    icon: GitBranch },
  'task-monitor':  { label: '监控',    icon: Radio },
  'agent-editor':  { label: 'Agent',   icon: Bot },
  'agent-detail':  { label: 'Agent',   icon: Bot },
  'tool-editor':   { label: '工具',    icon: Bot },
  'tool-detail':   { label: '工具',    icon: Bot },
  'dataset-editor':{ label: '数据集',  icon: BarChart3 },
  'dataset-repository': { label: '数据集仓库', icon: Package },
  'repository':    { label: '工具仓库',    icon: Package },
  settings:        { label: '设置',    icon: Settings },
}

export function CenterPanel() {
  const { activeView } = useUIStore()
  return (
    <div className="flex flex-col h-full bg-maia-bg">
      <ViewTabBar />
      <div className="flex-1 min-h-0"><ViewRouter view={activeView} /></div>
    </div>
  )
}

function ViewTabBar() {
  const { activeView, openViews, setActiveView, closeView,
          leftPanelOpen, rightPanelOpen, toggleLeftPanel, toggleRightPanel } = useUIStore()
  const agents = useResourceStore((s) => s.agentResources)
  const tools = useResourceStore((s) => s.toolResources)
  const modelResources = useResourceStore((s) => s.modelResources)

  // 标签过多时用左右箭头滚动，不出原生水平滚动条
  const scrollRef = useRef<HTMLDivElement>(null)
  const [canScrollLeft, setCanScrollLeft] = useState(false)
  const [canScrollRight, setCanScrollRight] = useState(false)

  const updateArrows = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    setCanScrollLeft(el.scrollLeft > 1)
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1)
  }, [])

  useEffect(() => {
    updateArrows()
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(updateArrows)
    ro.observe(el)
    window.addEventListener('resize', updateArrows)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', updateArrows)
    }
  }, [updateArrows, openViews.length])

  const scrollTabs = (dir: number) =>
    scrollRef.current?.scrollBy({ left: dir * 160, behavior: 'smooth' })

  // 激活标签变化（打开新资源/点左侧资源）时，把对应标签滚动进可视区。
  // 标签超出容器宽度时新标签会落在视口外，不滚过去用户看不到刚打开的标签。
  useEffect(() => {
    const container = scrollRef.current
    if (!container) return
    const el = container.querySelector(`[data-view="${CSS.escape(activeView)}"]`)
    el?.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' })
  }, [activeView, openViews.length])

  return (
    <div className="flex items-center h-9 border-b border-maia-border bg-maia-surface px-1 gap-1 shrink-0 select-none">
      <button
        onClick={toggleLeftPanel}
        title={leftPanelOpen ? '隐藏侧边栏' : '显示侧边栏'}
        className="shrink-0 flex items-center justify-center h-6 w-6 rounded text-maia-text-muted hover:text-maia-accent hover:bg-maia-sidebar-hover transition-colors"
      >
        <PanelLeft className="h-3.5 w-3.5" />
      </button>
      {canScrollLeft && (
        <button
          onClick={() => scrollTabs(-1)}
          title="向左滚动标签"
          className="shrink-0 flex items-center justify-center h-6 w-5 rounded text-maia-text-muted hover:text-maia-text hover:bg-maia-sidebar-hover transition-colors"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
      )}
      <div
        ref={scrollRef}
        onScroll={updateArrows}
        className="flex-1 min-w-0 flex items-center gap-1 overflow-x-hidden"
      >
        {openViews.map((viewId) => {
          // 参数化视图（单个 Agent/工具/模型）动态解析标题与图标
          const agentId = parseAgentDetailView(viewId)
          const toolId = parseToolDetailView(viewId)
          const modelId = parseModelDetailView(viewId)
          let cfg = TAB_CONFIG[viewId as keyof typeof TAB_CONFIG] as
            | { label: string; icon: typeof MessageSquare }
            | undefined
          if (!cfg) {
            if (agentId) {
              const a = agents.find((x: { id: string }) => x.id === agentId)
              cfg = { label: a?.name || agentId, icon: Bot }
            } else if (toolId) {
              const t = tools.find((x: { id: string }) => x.id === toolId)
              cfg = { label: t?.name || toolId, icon: Bot }
            } else if (modelId) {
              const m = modelResources.find((x: { id: string }) => x.id === modelId)
              cfg = { label: m?.name || modelId, icon: BrainCircuit }
            }
          }
          if (!cfg) return null
          const Icon = cfg.icon
          const isActive = activeView === viewId
          const isChat = viewId === 'chat'
          return (
            <button
              key={viewId}
              data-view={viewId}
              onClick={() => setActiveView(viewId)}
              className={`flex items-center gap-1.5 h-6 px-2.5 rounded text-[13px] transition-colors shrink-0 group ${isChat ? 'min-w-[72px]' : 'min-w-[72px]'} ${
                isActive
                  ? 'bg-maia-bg text-maia-text border border-maia-border'
                  : 'text-maia-text-muted hover:text-maia-text-secondary hover:bg-maia-sidebar-hover'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span className="max-w-[140px] truncate">{cfg.label}</span>
              {!isChat && (
                <span
                  onClick={(e) => { e.stopPropagation(); closeView(viewId) }}
                  className="ml-auto p-0.5 rounded-sm opacity-0 group-hover:opacity-100 hover:bg-maia-sidebar-hover hover:text-maia-danger transition-all"
                  title="关闭"
                ><X className="h-3 w-3" /></span>
              )}
            </button>
          )
        })}
      </div>
      {canScrollRight && (
        <button
          onClick={() => scrollTabs(1)}
          title="向右滚动标签"
          className="shrink-0 flex items-center justify-center h-6 w-5 rounded text-maia-text-muted hover:text-maia-text hover:bg-maia-sidebar-hover transition-colors"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      )}
      <button
        onClick={toggleRightPanel}
        title={rightPanelOpen ? '隐藏属性面板' : '显示属性面板'}
        className="shrink-0 flex items-center justify-center h-6 w-6 rounded text-maia-text-muted hover:text-maia-accent hover:bg-maia-sidebar-hover transition-colors"
      >
        <PanelRight className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

function ViewRouter({ view }: { view: ActiveView }) {
  // 单个 Agent / 工具的独立视图
  const agentId = parseAgentDetailView(view)
  // key 强制按 agentId 重新挂载：否则切换 Agent 时 React 复用同一实例，
  // messages/input/running/specMd 等状态会残留上一个 Agent 的内容，
  // 表现为切到 ETF Agent 仍显示城市查询 Agent 的对话。
  if (agentId) return <AgentDetailView key={`agent:${agentId}`} agentId={agentId} />
  const toolId = parseToolDetailView(view)
  if (toolId) return <ToolDetailView key={`tool:${toolId}`} toolId={toolId} />
  const modelId = parseModelDetailView(view)
  if (modelId) return <ModelDetailView key={`model:${modelId}`} modelId={modelId} />

  switch (view) {
    case 'chat': return <ChatView />
    case 'data-preview': return <DataPreviewView />
    case 'file-preview': return <FilePreviewView />
    case 'code-review': return <CodeReviewView />
    case 'orchestration': return <OrchestrationView />
    case 'task-monitor': return <TaskMonitorView />
    case 'agent-editor': return <AgentEditorView />
    case 'agent-detail': return <AgentDetailView />
    case 'tool-editor': return <ToolEditorView />
    case 'tool-detail': return <ToolDetailView />
    case 'dataset-editor': return <DatasetEditorView />
    case 'dataset-repository': return <DatasetRepositoryView />
    case 'repository': return <RepositoryView />
    case 'settings': return <SettingsView />
  }
}

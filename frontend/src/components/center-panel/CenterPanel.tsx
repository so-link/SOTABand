import { useCallback, useEffect, useRef, useState } from 'react'
import { useUIStore, type ActiveView, parseAgentDetailView, parseToolDetailView, parseModelDetailView, parseDatasetPreviewView } from '@/stores/ui-store'
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
import { AccountView } from './AccountView'
import { UsersView } from './UsersView'
import { CronJobsView } from './CronJobsView'
import { MessageSquare, BarChart3, Search, GitBranch, Radio, Bot, X, Package, Settings, PanelLeft, PanelRight, ChevronLeft, ChevronRight, BrainCircuit, CalendarClock, MoreHorizontal, User, Users } from 'lucide-react'
import { cn } from '@/lib/utils'

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
  'cron-jobs':     { label: '定时任务',  icon: CalendarClock },
  settings:        { label: '设置',    icon: Settings },
  account:         { label: '账号',    icon: User },
  users:           { label: '用户管理', icon: Users },
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
  const { activeView, openViews, setActiveView, closeView, closeOtherViews, closeAllViews,
          leftPanelOpen, rightPanelOpen, toggleLeftPanel, toggleRightPanel } = useUIStore()
  const agents = useResourceStore((s) => s.agentResources)
  const tools = useResourceStore((s) => s.toolResources)
  const modelResources = useResourceStore((s) => s.modelResources)
  const datasets = useResourceStore((s) => s.dataResources)

  // 「批量关闭标签」下拉菜单
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  // 除 chat（固定标签）外的可关闭标签数
  const closableCount = openViews.filter((v) => v !== 'chat').length

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

  // 「对话」是固定标签，永远渲染在滚动区之外（最左侧）；
  // 其余标签在滚动容器内，多时用 < / > 滚动。
  const chatView = openViews.includes('chat') ? 'chat' : null
  const scrollableViews = openViews.filter((v) => v !== 'chat')

  // 激活标签变化（打开新资源/点左侧资源）时，把对应标签滚动进可视区。
  // 标签超出容器宽度时新标签会落在视口外，不滚过去用户看不到刚打开的标签。
  useEffect(() => {
    if (activeView === 'chat') return
    const container = scrollRef.current
    if (!container) return
    const el = container.querySelector(`[data-view="${CSS.escape(activeView)}"]`)
    el?.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' })
  }, [activeView, openViews.length])

  // 按 viewId 渲染单个标签（对话标签与滚动标签共用）
  const renderTab = (viewId: ActiveView) => {
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
      } else {
        const dsId = parseDatasetPreviewView(viewId)
        if (dsId) {
          const d = datasets.find((x: { id: string }) => x.id === dsId)
          cfg = { label: d?.name || dsId, icon: BarChart3 }
        }
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
        className={`flex items-center gap-1.5 h-6 px-2.5 rounded text-[13px] transition-colors shrink-0 group min-w-[72px] ${
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
  }

  return (
    <div className="flex items-center h-9 border-b border-maia-border bg-maia-surface px-1 gap-1 shrink-0 select-none">
      <button
        onClick={toggleLeftPanel}
        title={leftPanelOpen ? '隐藏侧边栏' : '显示侧边栏'}
        className="shrink-0 flex items-center justify-center h-6 w-6 rounded text-maia-text-muted hover:text-maia-accent hover:bg-maia-sidebar-hover transition-colors"
      >
        <PanelLeft className="h-3.5 w-3.5" />
      </button>
      {/* 固定标签：永远在滚动区外，不随右侧标签滚动 */}
      {chatView && renderTab(chatView)}
      {/* 左滚动箭头放在固定标签之后：对话标签始终在 < 的左边 */}
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
        {scrollableViews.map((viewId) => renderTab(viewId))}
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
      <div ref={menuRef} className="relative shrink-0">
        <button
          onClick={() => setMenuOpen((v) => !v)}
          title="标签操作"
          className={cn(
            'flex items-center justify-center h-6 w-6 rounded transition-colors',
            menuOpen
              ? 'text-maia-accent bg-maia-sidebar-hover'
              : 'text-maia-text-muted hover:text-maia-accent hover:bg-maia-sidebar-hover',
          )}
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-7 z-50 w-40 rounded-md border border-maia-border bg-maia-surface shadow-lg py-1">
            <button
              onClick={() => { closeOtherViews(); setMenuOpen(false) }}
              disabled={closableCount <= (activeView !== 'chat' ? 1 : 0)}
              className="w-full text-left px-3 py-1.5 text-[13px] text-maia-text hover:bg-maia-sidebar-hover disabled:opacity-40 disabled:pointer-events-none"
            >
              关闭其他标签
            </button>
            <button
              onClick={() => { closeAllViews(); setMenuOpen(false) }}
              disabled={closableCount === 0}
              className="w-full text-left px-3 py-1.5 text-[13px] text-maia-text hover:bg-maia-sidebar-hover disabled:opacity-40 disabled:pointer-events-none"
            >
              关闭所有标签
            </button>
          </div>
        )}
      </div>
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
  const datasetId = parseDatasetPreviewView(view)
  if (datasetId) return <DataPreviewView key={`dataset:${datasetId}`} datasetId={datasetId} />

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
    case 'cron-jobs': return <CronJobsView />
    case 'settings': return <SettingsView />
    case 'account': return <AccountView />
    case 'users': return <UsersView />
  }
}

import { useUIStore, type ActiveView, parseAgentDetailView, parseToolDetailView } from '@/stores/ui-store'
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
import { DatasetEditorView } from './DatasetEditorView'
import { RepositoryView } from './RepositoryView'
import { DatasetRepositoryView } from './DatasetRepositoryView'
import { NetworksView } from './NetworksView'
import { SettingsView } from './SettingsView'
import { MessageSquare, BarChart3, Search, GitBranch, Radio, Bot, X, Package, Settings, Network } from 'lucide-react'

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
  networks:        { label: '协作网络',  icon: Network },
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
  const { activeView, openViews, setActiveView, closeView } = useUIStore()
  const agents = useResourceStore((s) => s.agentResources)
  const tools = useResourceStore((s) => s.toolResources)

  return (
    <div className="flex border-b border-maia-border/30 bg-maia-bg/60 backdrop-blur-sm px-2 gap-1 shrink-0 select-none overflow-x-auto">
      {openViews.map((viewId) => {
        // 参数化视图（单个 Agent/工具）动态解析标题与图标
        const agentId = parseAgentDetailView(viewId)
        const toolId = parseToolDetailView(viewId)
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
          }
        }
        if (!cfg) return null
        const Icon = cfg.icon
        const isActive = activeView === viewId
        const isChat = viewId === 'chat'
        return (
          <button
            key={viewId}
            onClick={() => setActiveView(viewId)}
            className={`flex items-center gap-2 py-2.5 text-[13px] font-semibold tracking-wider transition-all duration-150 border-b-[1.5px] -mb-[1px] rounded-t-sm shrink-0 group ${isChat ? 'px-5 min-w-[80px]' : 'pl-5 pr-2 min-w-[80px]'} ${
              isActive
                ? 'text-maia-accent border-maia-accent bg-maia-surface glow-subtle'
                : 'text-maia-text-muted border-transparent hover:text-maia-text-secondary hover:bg-maia-sidebar-hover'
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
  )
}

function ViewRouter({ view }: { view: ActiveView }) {
  // 单个 Agent / 工具的独立视图
  const agentId = parseAgentDetailView(view)
  if (agentId) return <AgentDetailView agentId={agentId} />
  const toolId = parseToolDetailView(view)
  if (toolId) return <ToolDetailView toolId={toolId} />

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
    case 'networks': return <NetworksView />
    case 'settings': return <SettingsView />
  }
}

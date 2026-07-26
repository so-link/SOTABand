import { useUIStore, type ActiveView } from '@/stores/ui-store'
import { ChatView } from './ChatView'
import { DataPreviewView } from './DataPreviewView'
import { CodeReviewView } from './CodeReviewView'
import { OrchestrationView } from './OrchestrationView'
import { TaskMonitorView } from './TaskMonitorView'
import { AgentEditorView } from './AgentEditorView'
import { AgentDetailView } from './AgentDetailView'
import { ToolEditorView } from './ToolEditorView'
import { ToolDetailView } from './ToolDetailView'
import { DatasetEditorView } from './DatasetEditorView'
import { MessageSquare, BarChart3, Search, GitBranch, Radio, Bot, X } from 'lucide-react'

const TAB_CONFIG: Record<ActiveView, { label: string; icon: typeof MessageSquare }> = {
  chat:            { label: '对话',    icon: MessageSquare },
  'data-preview':  { label: '数据',    icon: BarChart3 },
  'code-review':   { label: '核验',    icon: Search },
  orchestration:   { label: '编排',    icon: GitBranch },
  'task-monitor':  { label: '监控',    icon: Radio },
  'agent-editor':  { label: 'Agent',   icon: Bot },
  'agent-detail':  { label: 'Agent',   icon: Bot },
  'tool-editor':   { label: '工具',    icon: Bot },
  'tool-detail':   { label: '工具',    icon: Bot },
  'dataset-editor':{ label: '数据集',  icon: BarChart3 },
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

  return (
    <div className="flex border-b border-maia-border/30 bg-maia-bg/60 backdrop-blur-sm px-2 gap-1 shrink-0 select-none overflow-x-auto">
      {openViews.map((viewId) => {
        const cfg = TAB_CONFIG[viewId]
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
            {cfg.label}
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
  switch (view) {
    case 'chat': return <ChatView />
    case 'data-preview': return <DataPreviewView />
    case 'code-review': return <CodeReviewView />
    case 'orchestration': return <OrchestrationView />
    case 'task-monitor': return <TaskMonitorView />
    case 'agent-editor': return <AgentEditorView />
    case 'agent-detail': return <AgentDetailView />
    case 'tool-editor': return <ToolEditorView />
    case 'tool-detail': return <ToolDetailView />
    case 'dataset-editor': return <DatasetEditorView />
  }
}

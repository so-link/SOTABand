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

export function CenterPanel() {
  const { activeView } = useUIStore()

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      <ViewTabBar />
      <div className="flex-1 min-h-0">
        <ViewRouter view={activeView} />
      </div>
    </div>
  )
}

function ViewTabBar() {
  const { activeView, setActiveView } = useUIStore()

  const tabs: { id: ActiveView; label: string }[] = [
    { id: 'chat', label: '💬 对话' },
    { id: 'data-preview', label: '📊 数据预览' },
    { id: 'code-review', label: '🔍 代码核验' },
    { id: 'orchestration', label: '🎯 编排' },
    { id: 'task-monitor', label: '📡 监控' },
    { id: 'agent-editor', label: '🤖 Agent 编辑器' },
  ]

  return (
    <div className="flex border-b border-maia-border bg-maia-bg/50 px-3 gap-1 shrink-0 select-none">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => setActiveView(tab.id)}
          className={`py-1.5 px-4 text-[11px] font-medium tracking-wider transition-colors border-b-[1.5px] -mb-[1px] rounded-t-sm ${
            activeView === tab.id
              ? 'text-maia-text-heading border-maia-accent bg-maia-surface'
              : 'text-maia-text-muted border-transparent hover:text-maia-text-secondary hover:bg-maia-sidebar-hover'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

function ViewRouter({ view }: { view: ActiveView }) {
  switch (view) {
    case 'chat':
      return <ChatView />
    case 'data-preview':
      return <DataPreviewView />
    case 'code-review':
      return <CodeReviewView />
    case 'orchestration':
      return <OrchestrationView />
    case 'task-monitor':
      return <TaskMonitorView />
    case 'agent-editor':
      return <AgentEditorView />
    case 'agent-detail':
      return <AgentDetailView />
    case 'tool-editor':
      return <ToolEditorView />
    case 'tool-detail':
      return <ToolDetailView />
  }
}

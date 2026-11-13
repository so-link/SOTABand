import { FolderOpen, Package } from 'lucide-react'
import { useUIStore } from '@/stores/ui-store'
import { WorkspaceFileTree } from './WorkspaceFileTree'
import { ResourceBrowser } from './ResourceBrowser'

export function LeftPanel() {
  const { leftPanelTab, setLeftPanelTab } = useUIStore()

  const tabs = [
    { id: 'files' as const, label: '我的文件', icon: FolderOpen },
    { id: 'resources' as const, label: '资源空间', icon: Package },
  ]

  return (
    <div className="flex flex-col h-full bg-maia-sidebar border-r border-maia-border select-none">
      <div className="flex items-center h-9 px-2 border-b border-maia-border shrink-0">
        <div className="flex items-center gap-0.5 p-0.5 rounded-md bg-maia-bg/60 w-full">
          {tabs.map((t) => {
            const Icon = t.icon
            const active = leftPanelTab === t.id
            return (
              <button
                key={t.id}
                data-testid={`left-tab-${t.id}`}
                onClick={() => setLeftPanelTab(t.id)}
                className={`flex-1 flex items-center justify-center gap-1.5 h-6 rounded text-[13px] transition-colors ${
                  active
                    ? 'bg-maia-surface text-maia-text border border-maia-border'
                    : 'text-maia-text-muted hover:text-maia-text-secondary hover:bg-maia-sidebar-hover'
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {t.label}
              </button>
            )
          })}
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {leftPanelTab === 'files' ? <WorkspaceFileTree /> : <ResourceBrowser />}
      </div>
    </div>
  )
}

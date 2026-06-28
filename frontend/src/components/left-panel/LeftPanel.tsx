import { FolderOpen, Package } from 'lucide-react'
import { useUIStore } from '@/stores/ui-store'
import { WorkspaceFileTree } from './WorkspaceFileTree'
import { ResourceBrowser } from './ResourceBrowser'

export function LeftPanel() {
  const { leftPanelTab, setLeftPanelTab } = useUIStore()

  return (
    <div className="flex flex-col h-full bg-maia-sidebar border-r border-maia-border select-none">
      {/* Tab bar — VS Code style */}
      <div className="flex border-b border-maia-border bg-maia-sidebar">
        <button
          onClick={() => setLeftPanelTab('files')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-medium tracking-wider uppercase transition-colors ${
            leftPanelTab === 'files'
              ? 'text-maia-text-heading border-b-[2px] border-maia-accent bg-maia-sidebar-active/50'
              : 'text-maia-text-muted hover:text-maia-text-secondary'
          }`}
        >
          <FolderOpen className="h-3.5 w-3.5" />
          工作区间
        </button>
        <button
          onClick={() => setLeftPanelTab('resources')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-medium tracking-wider uppercase transition-colors ${
            leftPanelTab === 'resources'
              ? 'text-maia-text-heading border-b-[2px] border-maia-accent bg-maia-sidebar-active/50'
              : 'text-maia-text-muted hover:text-maia-text-secondary'
          }`}
        >
          <Package className="h-3.5 w-3.5" />
          资源空间
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-auto">
        {leftPanelTab === 'files' ? <WorkspaceFileTree /> : <ResourceBrowser />}
      </div>
    </div>
  )
}

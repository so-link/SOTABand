import { FolderOpen, Package } from 'lucide-react'
import { useUIStore } from '@/stores/ui-store'
import { WorkspaceFileTree } from './WorkspaceFileTree'
import { ResourceBrowser } from './ResourceBrowser'

export function LeftPanel() {
  const { leftPanelTab, setLeftPanelTab } = useUIStore()

  return (
    <div className="flex flex-col h-full bg-maia-sidebar border-r border-maia-border select-none">
      <div className="flex border-b border-maia-border bg-maia-sidebar px-1 pt-1 gap-0.5">
        <button
          onClick={() => setLeftPanelTab('files')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-semibold tracking-wider rounded-t transition-all duration-150 ${
            leftPanelTab === 'files'
              ? 'text-maia-accent bg-maia-surface border-t border-x border-maia-border-glow glow-subtle'
              : 'text-maia-text-muted hover:text-maia-text-secondary hover:bg-maia-sidebar-hover'
          }`}
        ><FolderOpen className="h-3.5 w-3.5" />工作区间</button>
        <button
          onClick={() => setLeftPanelTab('resources')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-semibold tracking-wider rounded-t transition-all duration-150 ${
            leftPanelTab === 'resources'
              ? 'text-maia-accent bg-maia-surface border-t border-x border-maia-border-glow glow-subtle'
              : 'text-maia-text-muted hover:text-maia-text-secondary hover:bg-maia-sidebar-hover'
          }`}
        ><Package className="h-3.5 w-3.5" />资源空间</button>
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {leftPanelTab === 'files' ? <WorkspaceFileTree /> : <ResourceBrowser />}
      </div>
    </div>
  )
}

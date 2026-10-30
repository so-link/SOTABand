import { Wrench, Database, Terminal } from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'
import { APP_VERSION } from '@/lib/utils'

export function StatusBar() {
  // 与左栏「工具空间」同一口径：已加载到当前工作区、Agent 实际可调用的工具。
  // 仓库里的工具总数在「工具仓库」页单独展示，避免两处数字对不上。
  const toolCount = useWorkspaceToolStore((s) => s.tools.length)
  const dataCount = useResourceStore((s) => s.dataResources.length)

  return (
    <footer className="flex h-7 items-center justify-between gap-4 px-3 text-[12px] shrink-0 select-none glass border-t border-white/[0.03]">
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="h-1.5 w-1.5 rounded-full bg-maia-success shrink-0" />
        <span className="text-maia-text-muted truncate">就绪</span>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span
          className="inline-flex items-center gap-1.5 text-maia-text-muted"
          title="已加载到当前工作区的工具数（工具仓库另有全部可用工具）"
        >
          <Wrench className="h-3.5 w-3.5 text-maia-accent/70" />
          TOOLS:<span className="font-mono text-maia-text-secondary">{toolCount}</span>
        </span>
        <span
          className="inline-flex items-center gap-1.5 text-maia-text-muted"
          title="已注册的数据集数量"
        >
          <Database className="h-3.5 w-3.5 text-maia-accent/70" />
          DATA:<span className="font-mono text-maia-text-secondary">{dataCount}</span>
        </span>
        <span className="inline-flex items-center gap-1.5 pl-3 border-l border-maia-border text-maia-text-muted/70">
          <Terminal className="h-3.5 w-3.5 text-maia-accent/50" />
          <span className="font-mono">SOTABand v{APP_VERSION}</span>
        </span>
      </div>
    </footer>
  )
}

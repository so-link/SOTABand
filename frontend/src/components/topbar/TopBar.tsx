import {
  PanelLeft,
  PanelRight,
  Bell,
  Settings,
  Activity,
  Brain,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { Avatar } from '@/components/ui/avatar'
import { useUIStore } from '@/stores/ui-store'

export function TopBar() {
  const { leftPanelOpen, rightPanelOpen, toggleLeftPanel, toggleRightPanel } =
    useUIStore()

  return (
    <header className="flex h-9 items-center justify-between border-b border-maia-border bg-maia-surface px-3 shrink-0 select-none">
      {/* Left section */}
      <div className="flex items-center gap-1.5">
        <Tooltip content={leftPanelOpen ? '隐藏侧边栏' : '显示侧边栏'}>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={toggleLeftPanel}>
            <PanelLeft className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>

        <div className="flex items-center gap-1.5 ml-1">
          <Brain className="h-4 w-4 text-maia-accent" />
          <span className="text-[13px] font-semibold text-maia-text-heading tracking-wide">
            MAIA Engine
          </span>
        </div>

        <span className="text-maia-text-muted mx-0.5 text-xs">/</span>

        <span className="text-[12px] text-maia-text-secondary tracking-wide">
          my_project
        </span>

        <Badge variant="accent" className="text-[10px] ml-1 tracking-wider">
          v0.1
        </Badge>
      </div>

      {/* Center section */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5">
          <Activity className="h-3 w-3 text-maia-success" />
          <span className="text-[11px] text-maia-text-muted tracking-wide">
            系统正常 · GPU:0 空闲
          </span>
        </div>
      </div>

      {/* Right section */}
      <div className="flex items-center gap-0.5">
        <Tooltip content="通知">
          <Button variant="ghost" size="icon" className="h-7 w-7 relative">
            <Bell className="h-3.5 w-3.5" />
            <span className="absolute top-1.5 right-1.5 flex h-1.5 w-1.5 rounded-full bg-maia-danger" />
          </Button>
        </Tooltip>

        <Tooltip content="设置">
          <Button variant="ghost" size="icon" className="h-7 w-7">
            <Settings className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>

        <Tooltip content={rightPanelOpen ? '隐藏属性面板' : '显示属性面板'}>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={toggleRightPanel}>
            <PanelRight className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>

        <div className="ml-1.5">
          <Avatar fallback="J" className="h-6 w-6 text-[11px]" />
        </div>
      </div>
    </header>
  )
}

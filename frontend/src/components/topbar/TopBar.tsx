import { PanelLeft, PanelRight, Bell, Settings, Sun, Moon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { Avatar } from '@/components/ui/avatar'
import { useUIStore } from '@/stores/ui-store'
import { useFileTreeStore } from '@/stores/file-tree-store'

export function TopBar() {
  const { leftPanelOpen, rightPanelOpen, toggleLeftPanel, toggleRightPanel, theme, toggleTheme, setActiveView } = useUIStore()
  const workspaceName = useFileTreeStore((s) => s.root?.name)

  return (
    <header className="flex h-9 items-center justify-between px-3 shrink-0 select-none glass border-b border-white/[0.04] relative">
      <div className="absolute bottom-0 left-0 right-0 gradient-line" />
      <div className="flex items-center gap-2">
        <Tooltip content={leftPanelOpen ? '隐藏侧边栏' : '显示侧边栏'}>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={toggleLeftPanel}>
            <PanelLeft className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>
        <div className="flex items-center gap-2 ml-1">
          <img src="/image.png" alt="Logo" className="w-5 h-5 rounded object-contain"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none'
              const fallback = (e.target as HTMLElement).nextElementSibling as HTMLElement
              if (fallback) fallback.style.display = 'flex'
            }} />
          <div className="flex items-center justify-center w-5 h-5 rounded bg-maia-accent/10 border border-maia-accent-border" style={{ display: 'none' }}>
            <span className="h-3 w-3 text-maia-accent text-[12px] font-semibold flex items-center justify-center">S</span>
          </div>
          <span className="text-[13px] font-semibold text-maia-text-heading">SOTABand</span>
        </div>
        {workspaceName && workspaceName !== '工作区间' && (
          <>
            <span className="text-maia-text-muted mx-0.5 text-[13px] font-mono">/</span>
            <span className="text-[13px] text-maia-text-secondary font-mono">{workspaceName}</span>
          </>
        )}
        <Badge variant="accent" className="text-[12px] ml-1 font-mono">v0.1</Badge>
      </div>

      <div className="flex items-center gap-0.5">
        <Tooltip content={theme === 'dark' ? '浅色模式' : '深色模式'}>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={toggleTheme}>
            {theme === 'dark' ? <Sun className="h-3.5 w-3.5 text-amber-400" /> : <Moon className="h-3.5 w-3.5 text-violet-400" />}
          </Button>
        </Tooltip>
        <Tooltip content="通知">
          <Button variant="ghost" size="icon" className="h-7 w-7 relative">
            <Bell className="h-3.5 w-3.5" />
            <span className="absolute top-1.5 right-1.5 flex h-1.5 w-1.5 rounded-full bg-maia-danger" />
          </Button>
        </Tooltip>
        <Tooltip content="设置">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setActiveView('settings')}><Settings className="h-3.5 w-3.5" /></Button>
        </Tooltip>
        <Tooltip content={rightPanelOpen ? '隐藏属性面板' : '显示属性面板'}>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={toggleRightPanel}>
            <PanelRight className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>
        <div className="ml-1.5"><Avatar fallback="J" className="h-6 w-6 text-[12px]" /></div>
      </div>
    </header>
  )
}

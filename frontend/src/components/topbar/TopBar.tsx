import { PanelLeft, PanelRight, Bell, Settings, Activity, Cpu, Sun, Moon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { UserMenu } from '@/components/topbar/UserMenu'
import { useUIStore } from '@/stores/ui-store'

export function TopBar() {
  const { leftPanelOpen, rightPanelOpen, toggleLeftPanel, toggleRightPanel, theme, toggleTheme, setActiveView } = useUIStore()

  // header 上的 z-40 是必需的，不是随手加的：
  // `.glass` 带 backdrop-filter（会创建层叠上下文），header 自身 z-index 是 auto；
  // 下方内容区（react-resizable-panels 的 Panel）在 DOM 里更靠后，同层级下后者绘制在上
  // → 用户菜单浮窗（z-50，但被限制在 header 的层叠上下文内）会被页签栏盖住/糊掉。
  // 给 header 一个显式 z-index 后，整条顶栏（含所有下拉浮层）才真正浮在内容之上；
  // 内容区的模态框是 fixed + z-50，仍然高于这里。
  return (
    <header className="grid h-9 items-center grid-cols-[1fr_auto_1fr] px-3 shrink-0 select-none glass border-b border-white/[0.04] relative z-40">
      <div className="absolute bottom-0 left-0 right-0 gradient-line" />
      <div className="flex items-center gap-2 justify-self-start">
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
            <Cpu className="h-3 w-3 text-maia-accent" />
          </div>
          <span className="text-[13px] font-semibold text-maia-text-heading tracking-wider">SOTABand 优智联邦</span>
        </div>
        <span className="text-maia-text-muted mx-0.5 text-xs font-mono">/</span>
        <span className="text-[12px] text-maia-text-secondary tracking-wide font-mono">my_project</span>
        <Badge variant="accent" className="text-[10px] ml-1 tracking-wider font-mono">v0.1</Badge>
      </div>

      <div className="flex items-center gap-3 justify-self-center">
        <div className="flex items-center gap-1.5 px-2.5 py-0.5 rounded border border-maia-border-glow bg-maia-accent-light/30">
          <Activity className="h-3 w-3 text-maia-success" />
          <span className="text-[10px] text-maia-text-muted tracking-wider font-mono">SYS.ONLINE · GPU:0</span>
        </div>
      </div>

      <div className="flex items-center gap-0.5 justify-self-end">
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
        <div className="ml-1.5"><UserMenu /></div>
      </div>
    </header>
  )
}

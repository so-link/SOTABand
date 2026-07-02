import { CheckCircle2, Wrench, Box } from 'lucide-react'

export function StatusBar() {
  return (
    <footer className="flex h-6 items-center justify-between border-t border-maia-border bg-maia-accent text-white px-3 text-[11px] tracking-wide shrink-0 select-none">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1 opacity-90">
          <CheckCircle2 className="h-3 w-3" />
          <span>上次任务: EEG异常检测 #42 — 完成 (5m 23s)</span>
        </div>
      </div>

      <div className="flex items-center gap-3 opacity-80">
        <div className="flex items-center gap-1">
          <Wrench className="h-3 w-3" />
          <span>工具: 8</span>
        </div>
        <div className="flex items-center gap-1">
          <Box className="h-3 w-3" />
          <span>资源: 23</span>
        </div>
        <span className="text-[10px] tracking-wider opacity-60">
          SOTABand v0.1.0
        </span>
      </div>
    </footer>
  )
}

import { CheckCircle2, Wrench, Box, Terminal } from 'lucide-react'

export function StatusBar() {
  return (
    <footer className="flex h-6 items-center justify-between border-t border-maia-border bg-maia-surface px-3 text-[11px] tracking-wide shrink-0 select-none">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <CheckCircle2 className="h-3 w-3 text-maia-success" />
          <span className="font-mono text-[10px] text-maia-text-muted tracking-wider">LAST: EEG#42 — DONE 5m23s</span>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5"><Wrench className="h-3 w-3 text-maia-accent/70" /><span className="font-mono text-[10px] text-maia-text-muted tracking-wider">TOOLS:8</span></div>
        <div className="flex items-center gap-1.5"><Box className="h-3 w-3 text-maia-accent/70" /><span className="font-mono text-[10px] text-maia-text-muted tracking-wider">RES:23</span></div>
        <div className="flex items-center gap-1.5 pl-2 border-l border-maia-border">
          <Terminal className="h-3 w-3 text-maia-accent/50" />
          <span className="font-mono text-[10px] text-maia-text-muted/60 tracking-wider">SOTABand v0.1.0</span>
        </div>
      </div>
    </footer>
  )
}

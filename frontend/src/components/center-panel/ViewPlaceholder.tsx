import { GitBranch } from 'lucide-react'

export interface PlaceholderStep {
  title: string
  desc: string
}

/**
 * 未实现视图的统一占位骨架：与主界面共用一套语义 token，深浅色都可读。
 */
export function ViewPlaceholder({
  icon: Icon,
  title,
  desc,
  steps,
  hint,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  desc: string
  steps: PlaceholderStep[]
  hint?: string
}) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-maia-border bg-maia-surface text-maia-text-muted">
        <Icon className="h-5 w-5" />
      </span>
      <h2 className="text-[16px] leading-6 font-semibold text-maia-text-heading mt-3">{title}</h2>
      <p className="text-[13px] leading-[20px] text-maia-text-muted mt-1.5 max-w-[440px]">{desc}</p>

      <div className="mt-4 grid grid-cols-3 gap-2 w-full max-w-[480px]">
        {steps.map((s) => (
          <div key={s.title} className="rounded-[10px] border border-maia-border bg-maia-surface px-3 py-2.5 text-left">
            <div className="text-[13px] font-medium text-maia-text">{s.title}</div>
            <div className="text-[13px] leading-[18px] text-maia-text-muted mt-0.5">{s.desc}</div>
          </div>
        ))}
      </div>

      {hint && (
        <p className="mt-4 inline-flex items-center gap-1.5 rounded-[8px] border border-maia-border bg-maia-sidebar px-3 py-1.5 text-[13px] text-maia-text-secondary">
          <GitBranch className="h-3 w-3 text-maia-text-muted" />
          {hint}
        </p>
      )}
    </div>
  )
}

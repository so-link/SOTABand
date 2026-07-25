import { cn } from '@/lib/utils'

interface BadgeProps { children: React.ReactNode; variant?: 'default' | 'success' | 'warning' | 'danger' | 'accent'; className?: string }

const badgeVariants = {
  default: 'bg-maia-sidebar-active text-maia-text-secondary border border-maia-border',
  success: 'bg-maia-success-bg text-maia-success border border-maia-success/20',
  warning: 'bg-maia-warning-bg text-maia-warning border border-maia-warning/20',
  danger: 'bg-maia-danger-bg text-maia-danger border border-maia-danger/20',
  accent: 'bg-maia-accent-light text-maia-accent border border-maia-accent-border',
}

export function Badge({ children, variant = 'default', className }: BadgeProps) {
  return <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wider', badgeVariants[variant], className)}>{children}</span>
}

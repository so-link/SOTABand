import { cn } from '@/lib/utils'

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode
  variant?: 'default' | 'success' | 'warning' | 'danger' | 'accent'
}

const badgeVariants = {
  default: 'bg-maia-sidebar-active text-maia-text-secondary border border-maia-border',
  success: 'bg-maia-success-bg text-maia-success border border-maia-success/20',
  warning: 'bg-maia-warning-bg text-maia-warning border border-maia-warning/20',
  danger: 'bg-maia-danger-bg text-maia-danger border border-maia-danger/20',
  accent: 'bg-maia-accent-light text-maia-accent border border-maia-accent-border',
}

/**
 * 徽标。
 *
 * ⚠️ `...props` 必须透传：调用方会给 `data-testid`、`title`、`aria-*` 等属性。
 * 早期版本只接收 `className` 并丢弃其余属性，导致挂在徽标上的 `data-testid`
 * 静默消失 —— 端到端测试因此找不到元素，排查时还以为是测试写错了。
 */
export function Badge({ children, variant = 'default', className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wider',
        badgeVariants[variant],
        className
      )}
      {...props}
    >
      {children}
    </span>
  )
}

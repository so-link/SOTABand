import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const variants = {
  default: 'bg-maia-accent text-[#0a0e14] hover:bg-maia-accent-hover shadow-[0_0_12px_var(--color-maia-accent-glow)]',
  ghost: 'hover:bg-maia-sidebar-hover text-maia-text-secondary',
  outline: 'border border-maia-border hover:bg-maia-sidebar-hover text-maia-text-secondary hover:border-maia-accent-border',
  danger: 'bg-maia-danger text-white hover:bg-red-600 shadow-[0_0_10px_rgba(255,82,82,0.2)]',
} as const

const sizes = {
  sm: 'h-7 px-2.5 text-[11px] tracking-wider rounded',
  md: 'h-8 px-3 text-[12px] tracking-wider rounded-md',
  lg: 'h-9 px-4 text-[13px] tracking-wider rounded-lg',
  icon: 'h-7 w-7 rounded-md',
} as const

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> { variant?: keyof typeof variants; size?: keyof typeof sizes }

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'md', ...props }, ref) => (
    <button ref={ref} className={cn(
      'inline-flex items-center justify-center gap-1.5 font-semibold transition-all duration-150',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-maia-accent/40',
      'disabled:opacity-30 disabled:pointer-events-none',
      variants[variant], sizes[size], className
    )} {...props} />
  )
)
Button.displayName = 'Button'

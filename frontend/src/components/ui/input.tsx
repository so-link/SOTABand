import { forwardRef, type InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'h-8 w-full rounded border border-maia-border bg-maia-surface px-3 text-[12px] text-maia-text tracking-wide',
        'placeholder:text-maia-text-muted',
        'focus:outline-none focus:ring-2 focus:ring-maia-accent/20 focus:border-maia-accent',
        'disabled:opacity-40 disabled:bg-maia-sidebar-hover',
        className
      )}
      {...props}
    />
  )
)

Input.displayName = 'Input'

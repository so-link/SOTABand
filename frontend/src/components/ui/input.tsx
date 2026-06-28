import { forwardRef, type InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'h-8 w-full rounded border border-maia-border bg-white px-3 text-[12px] tracking-wide',
        'placeholder:text-maia-text-muted',
        'focus:outline-none focus:ring-2 focus:ring-maia-accent/20 focus:border-maia-accent',
        'disabled:opacity-40 disabled:bg-maia-bg',
        className
      )}
      {...props}
    />
  )
)

Input.displayName = 'Input'

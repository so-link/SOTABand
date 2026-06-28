import { forwardRef, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        'w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm',
        'placeholder:text-gray-400',
        'focus:outline-none focus:ring-2 focus:ring-maia-accent/30 focus:border-maia-accent',
        'disabled:opacity-50 disabled:bg-gray-50',
        'resize-none',
        className
      )}
      {...props}
    />
  )
)

Textarea.displayName = 'Textarea'

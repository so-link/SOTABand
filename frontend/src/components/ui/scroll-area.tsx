import { cn } from '@/lib/utils'
import { type ReactNode } from 'react'

interface ScrollAreaProps {
  children: ReactNode
  className?: string
}

export function ScrollArea({ children, className }: ScrollAreaProps) {
  return (
    <div className={cn('overflow-auto', className)}>
      {children}
    </div>
  )
}

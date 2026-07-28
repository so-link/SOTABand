import { cn } from '@/lib/utils'
import { type ReactNode, type HTMLAttributes } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  className?: string
}

export function Card({ children, className, ...props }: CardProps) {
  return (
    <div className={cn('rounded-lg border border-maia-border bg-maia-surface shadow-sm', className)} {...props}>
      {children}
    </div>
  )
}

export function CardHeader({ children, className }: CardProps) {
  return <div className={cn('border-b border-maia-border px-4 py-3', className)}>{children}</div>
}

export function CardBody({ children, className }: CardProps) {
  return <div className={cn('px-4 py-3', className)}>{children}</div>
}

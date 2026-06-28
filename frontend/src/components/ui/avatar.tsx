import { cn } from '@/lib/utils'

interface AvatarProps {
  fallback?: string
  className?: string
}

export function Avatar({ fallback = 'U', className }: AvatarProps) {
  return (
    <div
      className={cn(
        'flex h-8 w-8 items-center justify-center rounded-full bg-maia-accent text-sm font-medium text-white',
        className
      )}
    >
      {fallback}
    </div>
  )
}

import { cn } from '@/lib/utils'
import type { Message } from '@/types/chat'
import { InlineCard } from './InlineCard'
import {
  Bot,
  User,
  Info,
  Paperclip,
} from 'lucide-react'

interface MessageBubbleProps {
  message: Message
}

const ROLE_STYLES: Record<Message['role'], {
  container: string
  icon: typeof Bot
  label: string
  iconBg: string
}> = {
  system: {
    container: 'bg-maia-warning-bg border border-amber-200/50 rounded-lg',
    icon: Info,
    label: '系统',
    iconBg: 'bg-amber-100 text-amber-600',
  },
  user: {
    container: '',
    icon: User,
    label: '你',
    iconBg: 'bg-maia-accent text-white',
  },
  agent: {
    container: '',
    icon: Bot,
    label: 'MAIA Agent',
    iconBg: 'bg-purple-100 text-purple-600',
  },
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const { role, content, timestamp, cards, attachments } = message
  const style = ROLE_STYLES[role]
  const Icon = style.icon

  // Simple markdown → JSX conversion for basic formatting
  const formattedContent = renderMarkdown(content)

  return (
    <div className={cn('flex gap-3', role === 'user' && 'flex-row-reverse')}>
      {/* Avatar */}
      <div className={cn('flex h-7 w-7 shrink-0 items-center justify-center rounded-full', style.iconBg)}>
        <Icon className="h-3.5 w-3.5" />
      </div>

      {/* Content */}
      <div className={cn('flex-1 min-w-0', role === 'user' && 'flex flex-col items-end')}>
        {/* Role label + timestamp */}
        <div className={cn('flex items-center gap-2 mb-1', role === 'user' && 'justify-end')}>
          <span className="text-[11px] font-medium text-maia-text-secondary tracking-wide">{style.label}</span>
          <span className="text-[10px] text-maia-text-muted tracking-tight">
            {new Date(timestamp).toLocaleTimeString('zh-CN', {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
        </div>

        {/* Message body */}
        <div className={cn(style.container)}>
          {role === 'system' ? (
            <div className="px-3 py-2 text-[12px] tracking-wide text-maia-text-secondary leading-relaxed">
              <div dangerouslySetInnerHTML={{ __html: formatHtml(formattedContent) }} />
            </div>
          ) : (
            <>
              {/* Render images from cards */}
              {cards && cards.map(card => {
                if (card.type === 'result-summary' && card.data) {
                  const result = (card.data as Record<string, unknown>).result as Record<string, unknown> | undefined
                  if (result?.output_format === 'image') {
                    const imgData = result.data as Record<string, unknown> | undefined
                    const imgPath = imgData?.image_path as string | undefined
                    if (imgPath) {
                      const src = `http://localhost:8001/api/file/image?path=${encodeURIComponent(imgPath)}`
                      return (
                        <img key={card.id} src={src} alt="工具输出"
                          className="w-full max-h-[400px] object-contain rounded-lg border border-gray-200 bg-gray-50 mb-3"
                          onError={(e) => {
                            const t = e.target as HTMLImageElement
                            t.outerHTML = `<div class="text-xs text-red-500 p-2 bg-red-50 rounded">图片加载失败: ${imgPath}</div>`
                          }}
                        />
                      )
                    }
                  }
                }
                return null
              })}

              {/* Text content */}
              <div className="text-[13px] leading-relaxed text-maia-text tracking-wide">
                <div dangerouslySetInnerHTML={{ __html: renderWithImages(formatHtml(formattedContent)) }} />
              </div>

              {/* Attachments */}
              {attachments && attachments.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {attachments.map((att) => (
                    <div
                      key={att.id}
                      className="flex items-center gap-1 rounded bg-maia-accent-light border border-maia-accent-border px-2 py-1 text-[11px] tracking-wide text-maia-accent"
                    >
                      <Paperclip className="h-3 w-3" />
                      <span>{att.fileName}</span>
                      <span className="text-[10px] text-maia-accent/60">
                        {att.format?.toUpperCase()}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Inline cards */}
              {cards && cards.length > 0 && (
                <div className="flex flex-col gap-2 mt-3">
                  {cards.map((card) => (
                    <InlineCard key={card.id} card={card} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ---- Simple Markdown Rendering ----

function renderMarkdown(text: string): string {
  return text
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code class="bg-gray-100 text-purple-700 px-1 py-0.5 rounded text-xs font-mono">$1</code>')
    // Line breaks → paragraphs
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br/>')
}

function formatHtml(text: string): string {
  return `<p>${text}</p>`
}

/** 将 [IMAGE:path] 转换为 img 标签 */
function renderWithImages(html: string): string {
  return html.replace(
    /\[IMAGE:([^\]]+)\]/g,
    (_m, path) => {
      const url = `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'}/api/file/image?path=${encodeURIComponent(path)}`
      return `<img src="${url}" alt="工具输出" style="max-width:100%;max-height:400px;border-radius:8px;margin:8px 0" />`
    }
  )
}

import { cn } from '@/lib/utils'
import type { Message } from '@/types/chat'
import { InlineCard } from './InlineCard'
import { Bot, Info } from 'lucide-react'
import { FileCategoryIcon } from '@/lib/file-icon'

interface MessageBubbleProps {
  message: Message
}

function formatTime(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const { role, content, timestamp, cards, attachments } = message

  if (role === 'system') {
    return (
      <div className="flex items-start justify-center py-1">
        <div className="inline-flex items-start gap-2 max-w-[90%] px-3 py-2 rounded-lg border border-maia-border bg-maia-chat-system text-[13px] leading-[18px] text-maia-text-secondary">
          <Info className="h-3.5 w-3.5 shrink-0 mt-0.5 text-maia-text-muted" />
          <div className="[&_strong]:text-maia-text [&_strong]:font-medium" dangerouslySetInnerHTML={{ __html: formatHtml(renderMarkdown(content)) }} />
        </div>
      </div>
    )
  }

  if (role === 'user') {
    return (
      <div className="flex flex-col items-end gap-1.5 max-w-[90%] ml-auto">
        <div className="rounded-xl border border-maia-accent-border bg-maia-chat-user px-3.5 py-2.5 text-[13px] leading-[21px] text-maia-text whitespace-pre-wrap break-words">
          {content}
          {attachments && attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {attachments.map((att) => (
                <span
                  key={att.id}
                  className="inline-flex items-center gap-1 rounded border border-maia-accent-border bg-maia-accent-light px-2 py-0.5 text-[12px] text-maia-accent"
                >
                  <FileCategoryIcon name={att.fileName} className="h-3 w-3" />
                  {att.fileName}
                </span>
              ))}
            </div>
          )}
        </div>
        <span className="text-[13px] text-maia-text-muted pr-1">{formatTime(timestamp)}</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 min-w-0">
      <div className="flex items-center gap-1.5">
        <Bot className="h-3.5 w-3.5 text-maia-accent" />
        <span className="text-[13px] font-medium text-maia-text-secondary">SOTABand Agent</span>
        <span className="text-[13px] text-maia-text-muted">{formatTime(timestamp)}</span>
      </div>

      <div
        className={cn(
          'text-[14px] leading-[24px] text-maia-text',
          '[&_p]:my-0 [&_p+p]:mt-3 [&_strong]:font-semibold [&_strong]:text-maia-text-heading',
          '[&_code]:rounded [&_code]:bg-maia-sidebar-hover [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px] [&_code]:leading-[20px] [&_code]:text-maia-accent',
          '[&_code]:font-mono',
        )}
        dangerouslySetInnerHTML={{ __html: formatHtml(renderMarkdown(content)) }}
      />

      {cards && cards.length > 0 && (
        <div className="flex flex-col gap-2">
          {cards.map((card) => (
            <InlineCard key={card.id} card={card} messageId={message.id} />
          ))}
        </div>
      )}
    </div>
  )
}

/** 极简 Markdown：粗体、行内代码、换行分段。输出前统一转义，避免 XSS。 */
function renderMarkdown(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/\n/g, '<br/>')
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function formatHtml(text: string): string {
  return `<p>${text}</p>`
}

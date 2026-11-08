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
    container: 'bg-maia-warning-bg border border-maia-card-amber rounded-lg',
    icon: Info,
    label: '系统',
    iconBg: 'bg-maia-card-amber text-amber-600',
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
    label: 'SOTABand Agent',
    iconBg: 'bg-maia-card-purple text-purple-600',
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
              {/* Text content */}
              <div className="text-[13px] leading-relaxed text-maia-text tracking-wide">
                <div dangerouslySetInnerHTML={{ __html: formatHtml(formattedContent) }} />
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
  const tables: string[] = []
  const headings: string[] = []
  const codeBlocks: string[] = []
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  const withCodePlaceholders = escaped.replace(
    /```[^\r\n]*\r?\n([\s\S]*?)```/g,
    (_match, code: string) => {
      const index = codeBlocks.length
      codeBlocks.push(`<pre class="my-2 overflow-x-auto rounded bg-maia-sidebar-hover p-2 text-xs leading-relaxed"><code>${code.trim()}</code></pre>`)
      return `\u0000CODE_${index}\u0000`
    },
  )

  const withTablePlaceholders = withCodePlaceholders.replace(
    /(^|\n)\|([^\n]+)\|\n\|[\s:|-]+\|\n((?:\|[^\n]+\|(?:\n|$))*)/g,
    (_match, prefix: string, header: string, rows: string) => {
      const index = tables.length
      tables.push(renderTable(header, rows))
      return `${prefix}\u0000TABLE_${index}\u0000`
    },
  )

  const withBlockPlaceholders = withTablePlaceholders.replace(
    /(?:^|\r?\n+)[ \t]*(#{1,4})[ \t]+([^\r\n]+)(?:\r?\n+|$)/g,
    (_match, hashes: string, title: string) => {
      const index = headings.length
      headings.push(renderHeading(hashes.length, title.trim()))
      return `\u0000HEADING_${index}\u0000`
    },
  )

  const formatted = withBlockPlaceholders
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code class="bg-maia-sidebar-hover text-maia-accent px-1 py-0.5 rounded text-xs font-mono">$1</code>')
    // Headings own their surrounding whitespace; plain text keeps lightweight flow.
    .replace(/\n{2,}/g, '<div class="h-2"></div>')
    .replace(/\n/g, '<br/>')

  return formatted
    .replace(/\u0000TABLE_(\d+)\u0000/g, (_match, index: string) => tables[Number(index)])
    .replace(/\u0000HEADING_(\d+)\u0000/g, (_match, index: string) => headings[Number(index)])
    .replace(/\u0000CODE_(\d+)\u0000/g, (_match, index: string) => codeBlocks[Number(index)])
}

function formatHtml(text: string): string {
  return text
}

function renderTable(header: string, rows: string): string {
  const cells = (line: string) => line.split('|').map((cell) => cell.trim()).filter(Boolean)
  const headers = cells(header)
  const bodyRows = rows.trim().split('\n').filter(Boolean).map(cells)

  const head = headers.map((cell) =>
    `<th class="border border-maia-border bg-maia-bg px-2 py-1.5 text-left text-[11px] font-medium text-maia-text-secondary">${cell}</th>`,
  ).join('')
  const body = bodyRows.map((row) =>
    `<tr>${row.map((cell) =>
      `<td class="border border-maia-border px-2 py-1.5 text-[11px] text-maia-text">${cell}</td>`,
    ).join('')}</tr>`,
  ).join('')

  return `<div class="my-2 overflow-x-auto"><table class="w-full border-collapse text-[11px]"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`
}

function renderHeading(level: number, title: string): string {
  const classes: Record<number, string> = {
    1: 'mt-5 mb-2 text-lg font-semibold text-maia-text',
    2: 'mt-4 mb-2 text-base font-semibold text-maia-text',
    3: 'mt-4 mb-1 text-sm font-semibold text-maia-text',
    4: 'mt-3 mb-1 text-[13px] font-semibold text-maia-text',
  }
  return `<div class="block ${classes[level]}">${title}</div>`
}


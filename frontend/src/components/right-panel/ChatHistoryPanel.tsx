import { useEffect, useState } from 'react'
import { Loader2, MessageSquare, Trash2 } from 'lucide-react'
import { useChatStore } from '@/stores/chat-store'

interface SessionItem {
  session_id: string
  agent_id: string
  title: string
  created_at: number
  updated_at: number
  message_count: number
}

/** 相对时间：与对话历史列表的时间列配合 */
function formatTime(sec: number): string {
  const diff = Date.now() / 1000 - sec
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return new Date(sec * 1000).toLocaleDateString()
}

/**
 * 右栏「对话历史」：替换原「会话上下文」。
 * 列出后端持久化的会话（标题由 LLM 总结），点击即加载该会话并继续对话。
 */
export function ChatHistoryPanel() {
  const sessionId = useChatStore((s) => s.sessionId)
  const sessionsVersion = useChatStore((s) => s.sessionsVersion)
  const switchSession = useChatStore((s) => s.switchSession)
  const deleteSession = useChatStore((s) => s.deleteSession)

  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch('/api/chat/sessions?limit=50')
      .then((r) => (r.ok ? r.json() : { sessions: [] }))
      .then((data) => {
        if (!cancelled) setSessions((data.sessions || []) as SessionItem[])
      })
      .catch(() => { if (!cancelled) setSessions([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sessionsVersion])

  return (
    <div className="flex flex-col h-full w-full min-w-0 overflow-hidden">
      <div className="flex items-center h-9 px-3 shrink-0 border-b border-maia-border">
        <span className="text-[13px] text-maia-text-muted">对话历史</span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden p-2 flex flex-col gap-0.5">
        {loading && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-4 w-4 animate-spin text-maia-text-muted" />
          </div>
        )}

        {!loading && sessions.length === 0 && (
          <p className="px-2 py-3 text-[13px] text-maia-text-muted">还没有历史对话，发一条消息即可创建会话。</p>
        )}

        {!loading && sessions.map((s) => {
          const active = s.session_id === sessionId
          return (
            <div
              key={s.session_id}
              role="button"
              tabIndex={0}
              onClick={() => { if (!active) switchSession(s.session_id) }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!active) switchSession(s.session_id) }
              }}
              className={`group flex items-start gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors ${
                active ? 'bg-maia-sidebar-active' : 'hover:bg-maia-sidebar-hover'
              }`}
              title={s.title || s.session_id}
            >
              <MessageSquare className="h-3.5 w-3.5 shrink-0 mt-0.5 text-maia-text-muted" />
              <div className="min-w-0 flex-1">
                <div className={`text-[13px] truncate ${active ? 'text-maia-text' : 'text-maia-text-secondary'}`}>
                  {s.title || '未命名对话'}
                </div>
                <div className="text-[12px] text-maia-text-muted truncate">
                  {formatTime(s.updated_at)} · {s.message_count} 条消息
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  if (confirm('删除该对话？此操作不可恢复。')) deleteSession(s.session_id)
                }}
                className="shrink-0 flex items-center justify-center h-5 w-5 rounded text-maia-text-muted opacity-0 group-hover:opacity-100 hover:text-maia-danger hover:bg-maia-danger-bg transition-all"
                title="删除对话"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

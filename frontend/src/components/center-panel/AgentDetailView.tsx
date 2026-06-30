import { useState, useEffect, useRef } from 'react'
import {
  Bot,
  Send,
  Play,
  Square,
  RefreshCw,
  Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useResourceStore } from '@/stores/resource-store'
import type { AgentResource } from '@/types/resources'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

export function AgentDetailView() {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const agent = selectedResource as AgentResource | null
  const [running, setRunning] = useState(false)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([])
  const [streaming, setStreaming] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  if (!agent) {
    return (
      <div className="flex items-center justify-center h-full text-maia-text-muted text-sm">
        请在左侧 Agent 空间选择一个 Agent
      </div>
    )
  }

  const start = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/start`, { method: 'POST' })
      setRunning(true)
      setMessages([])
      setStreaming('')
    } catch { /* ignore */ }
    setLoading(false)
  }

  const stop = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/stop`, { method: 'POST' })
      setRunning(false)
    } catch { /* ignore */ }
    setLoading(false)
  }

  const restart = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/restart`, { method: 'POST' })
      setRunning(true)
      setMessages([])
      setStreaming('')
    } catch { /* ignore */ }
    setLoading(false)
  }

  const sendMessage = async () => {
    if (!input.trim() || isSending) return
    const userInput = input
    setInput('')
    setMessages((m) => [...m, { role: 'user', content: userInput }])
    setIsSending(true)
    setStreaming('')

    try {
      const res = await fetch(`${BASE_URL}/api/agent/${agent.id}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: userInput }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let full = ''
      let eventType = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6))
              if (eventType === 'content' || !eventType) {
                full += payload.text || ''
                setStreaming(full)
              } else if (eventType === 'done') {
                setMessages((m) => [...m, { role: 'agent', content: full }])
                setStreaming('')
              }
              eventType = ''
            } catch { /* skip */ }
          }
        }
      }
    } catch {
      setStreaming('')
      setMessages((m) => [...m, { role: 'system', content: '⚠️ 请求失败' }])
    }
    setIsSending(false)
  }

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-maia-accent" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">
            {agent.name}
          </span>
          <Badge variant={running ? 'success' : 'default'}>
            {running ? '运行中' : '未启动'}
          </Badge>
        </div>
        <div className="flex items-center gap-1">
          {!running ? (
            <Button size="sm" variant="default" onClick={start} disabled={loading}>
              {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
              启动
            </Button>
          ) : (
            <>
              <Button size="sm" variant="outline" onClick={stop} disabled={loading}>
                <Square className="h-3 w-3" />
                停止
              </Button>
              <Button size="sm" variant="outline" onClick={restart} disabled={loading}>
                <RefreshCw className="h-3 w-3" />
                重启
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Output area */}
      <div className="flex-1 min-h-0 overflow-auto px-4 py-3">
        {messages.length === 0 && !streaming && (
          <div className="flex flex-col items-center justify-center h-full text-maia-text-muted gap-2">
            <Bot className="h-8 w-8 opacity-30" />
            <p className="text-sm">
              {running ? 'Agent 已启动，输入消息开始交互' : '点击"启动"按钮启动 Agent'}
            </p>
          </div>
        )}

        <div className="max-w-2xl mx-auto space-y-3">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex gap-2 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              <div
                className={`flex-shrink-0 h-6 w-6 rounded-full flex items-center justify-center text-[10px] ${
                  msg.role === 'user'
                    ? 'bg-maia-accent text-white'
                    : msg.role === 'system'
                      ? 'bg-amber-100 text-amber-600'
                      : 'bg-purple-100 text-purple-600'
                }`}
              >
                {msg.role === 'user' ? 'U' : msg.role === 'system' ? 'S' : 'A'}
              </div>
              <div className="text-[13px] leading-relaxed tracking-wide text-maia-text whitespace-pre-wrap">
                {msg.content}
              </div>
            </div>
          ))}

          {streaming && (
            <div className="flex gap-2">
              <div className="flex-shrink-0 h-6 w-6 rounded-full bg-purple-100 text-purple-600 flex items-center justify-center text-[10px]">
                A
              </div>
              <div className="text-[13px] leading-relaxed tracking-wide text-maia-text whitespace-pre-wrap">
                {streaming}
                <span className="inline-block w-1.5 h-4 bg-maia-accent ml-0.5 animate-pulse" />
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input */}
      {running && (
        <div className="border-t border-maia-border px-4 py-3 shrink-0">
          <div className="flex items-end gap-2 max-w-2xl mx-auto bg-maia-bg rounded-lg border border-maia-border p-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  sendMessage()
                }
              }}
              placeholder={`向 ${agent.name} 发送消息...`}
              rows={1}
              className="flex-1 bg-transparent text-[13px] tracking-wide outline-none resize-none placeholder:text-maia-text-muted"
              disabled={isSending}
            />
            <Button
              size="icon"
              onClick={sendMessage}
              disabled={isSending || !input.trim()}
              className="shrink-0 h-8 w-8 rounded-lg"
            >
              {isSending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

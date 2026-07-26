/** ApiChatService — 通过 SSE 调用后端交互 Agent */

import type { IChatService } from '@/services/types'
import type { Message, CreateMessageInput, InlineCard } from '@/types/chat'

const BASE_URL = ''

export class ApiChatService implements IChatService {
  private baseUrl: string
  private currentAbortController: AbortController | null = null
  private currentSessionId: string = 'default'

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl || BASE_URL
  }

  async *sendMessage(input: CreateMessageInput): AsyncGenerator<Message> {
    // 创建新的 AbortController
    this.currentAbortController = new AbortController()
    this.currentSessionId = input.sessionId || 'default'

    const response = await fetch(`${this.baseUrl}/api/chat/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: input.content,
        attachments: input.attachments || [],
        sessionId: this.currentSessionId,
        userId: 'default',
      }),
      signal: this.currentAbortController.signal,
    })

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`)
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('No response body')

    const decoder = new TextDecoder()
    let buffer = ''
    let fullContent = ''
    let pendingCards: InlineCard[] = []

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse SSE lines
        const parts = buffer.split('\n')
        buffer = parts.pop() || ''

        let currentEvent = ''

        for (const line of parts) {
          if (line === '') {
            currentEvent = ''
            continue
          }

          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            continue
          }

          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6))
              const eventType = currentEvent || 'content'

              switch (eventType) {
                case 'content':
                  fullContent += payload.text || ''
                  yield {
                    id: 'streaming',
                    role: 'agent',
                    content: fullContent,
                    timestamp: new Date().toISOString(),
                  }
                  break

                case 'card':
                  pendingCards.push({
                    id: `card-${Date.now()}`,
                    type: payload.type,
                    title: payload.title,
                    summary: payload.summary || '',
                    data: payload.data || {},
                  })
                  break

                case 'done':
                  yield {
                    id: payload.messageId || `msg-${Date.now()}`,
                    role: 'agent',
                    content: fullContent,
                    timestamp: new Date().toISOString(),
                    cards: pendingCards.length > 0 ? pendingCards : undefined,
                  }
                  break

                case 'stopped':
                  yield {
                    id: `msg-${Date.now()}`,
                    role: 'agent',
                    content: fullContent || '对话已停止',
                    timestamp: new Date().toISOString(),
                    cards: pendingCards.length > 0 ? pendingCards : undefined,
                  }
                  break

                case 'error':
                  throw new Error(payload.message || 'Unknown error')
              }

              currentEvent = ''
            } catch (e) {
              if (e instanceof SyntaxError) continue
              throw e
            }
          }
        }
      }
    } finally {
      this.currentAbortController = null
    }
  }

  async stopChat(sessionId?: string): Promise<void> {
    const sid = sessionId || this.currentSessionId
    // 先 abort 前端的 fetch
    if (this.currentAbortController) {
      this.currentAbortController.abort()
      this.currentAbortController = null
    }
    // 调用后端 stop 端点
    try {
      await fetch(`${this.baseUrl}/api/chat/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid }),
      })
    } catch {
      // 忽略网络错误
    }
  }
}

/** ApiChatService — 通过 SSE 调用后端交互 Agent */

import type { IChatService } from '@/services/types'
import type { Message, CreateMessageInput, InlineCard } from '@/types/chat'

export class ApiChatService implements IChatService {
  private baseUrl: string

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl || import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
  }

  async *sendMessage(input: CreateMessageInput): AsyncGenerator<Message> {
    const response = await fetch(`${this.baseUrl}/api/chat/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: input.content,
        attachments: input.attachments || [],
        sessionId: 'default',
        userId: 'default',
      }),
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

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Parse SSE lines
      const parts = buffer.split('\n')
      buffer = parts.pop() || ''

      let currentEvent = ''

      for (const line of parts) {
        // Empty line = end of an SSE event
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
  }
}

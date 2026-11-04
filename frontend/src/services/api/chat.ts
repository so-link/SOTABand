/** ApiChatService — 通过 SSE 调用后端交互 Agent */

import type { IChatService } from '@/services/types'
import type { Message, CreateMessageInput, InlineCard } from '@/types/chat'
import { apiFetchRaw, apiPost, assertOk } from './client'

export class ApiChatService implements IChatService {
  private currentAbortController: AbortController | null = null
  private currentSessionId: string = 'default'

  async *sendMessage(input: CreateMessageInput): AsyncGenerator<Message> {
    // 创建新的 AbortController
    this.currentAbortController = new AbortController()
    this.currentSessionId = input.sessionId || 'default'

    // SSE：必须用 apiFetchRaw 拿原始 body 的 reader（不能先解析 JSON）。
    // 但也必须 assertOk —— 否则错误响应会被当成事件流解析，最后报一句
    // 毫无信息量的"没有收到结束事件"。
    const response = await apiFetchRaw('/api/chat/send', {
      method: 'POST',
      body: JSON.stringify({
        content: input.content,
        attachments: input.attachments || [],
        workspaceToolIds: input.workspaceToolIds || [],
        sessionId: this.currentSessionId,
        userId: 'default',
      }),
      signal: this.currentAbortController.signal,
    })

    await assertOk(response)

    const reader = response.body?.getReader()
    if (!reader) throw new Error('No response body')

    const decoder = new TextDecoder()
    let buffer = ''
    let fullContent = ''
    let pendingCards: InlineCard[] = []
    let currentEvent = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse SSE lines
        const parts = buffer.split('\n')
        buffer = parts.pop() || ''

        for (const line of parts) {
          const normalizedLine = line.endsWith('\r') ? line.slice(0, -1) : line

          if (normalizedLine === '') {
            currentEvent = ''
            continue
          }

          if (normalizedLine.startsWith('event: ')) {
            currentEvent = normalizedLine.slice(7).trim()
            continue
          }

          if (normalizedLine.startsWith('data: ')) {
            try {
              const payload = JSON.parse(normalizedLine.slice(6))
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
                  if (!fullContent.trim() && pendingCards.length === 0) {
                    throw new Error('Agent 未返回内容')
                  }
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
      if (!fullContent.trim() && pendingCards.length === 0) {
        throw new Error('连接已结束，但 Agent 未返回内容')
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
      await apiPost('/api/chat/stop', { session_id: sid })
    } catch {
      // 忽略网络错误 / 已停止
    }
  }
}

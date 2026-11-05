/** ApiChatService — 通过 SSE 调用后端交互 Agent */

import type { IChatService } from '@/services/types'
import type { Message, CreateMessageInput, InlineCard, ChatStep } from '@/types/chat'

const BASE_URL = ''

export class ApiChatService implements IChatService {
  private baseUrl: string
  private currentAbortController: AbortController | null = null
  private currentSessionId: string = 'default'

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl || BASE_URL
  }

  async *sendMessage(
    input: CreateMessageInput,
    opts?: { onTitle?: (title: string, sessionId: string) => void },
  ): AsyncGenerator<Message> {
    const onTitle = opts?.onTitle
    // 创建新的 AbortController
    this.currentAbortController = new AbortController()
    this.currentSessionId = input.sessionId || 'default'

    const response = await fetch(`${this.baseUrl}/api/chat/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: input.content,
        attachments: input.attachments || [],
        workspaceToolIds: input.workspaceToolIds || [],
        sessionId: this.currentSessionId,
        // 不传 userId：身份由服务端按登录态判定（子进程据此决定文件落盘目录）
        ...(input.kind ? { kind: input.kind, receipt: input.receipt || {} } : {}),
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
    // 步骤时间线：同一 id 的事件覆盖更新（running → done/error）
    let steps: ChatStep[] = []
    // currentEvent 必须跨 chunk 保留：event: 行与 data: 行可能被读块边界切开，
    // 每块都重置会把事件名丢掉（如 done 被当成 content 吞掉，表现为空回复）。
    let currentEvent = ''
    // 是否已产出终态消息（done/stopped）：流被截断且从未产出时按空回复报错
    let produced = false

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse SSE lines
        const parts = buffer.split('\n')
        buffer = parts.pop() || ''

        for (const line of parts) {
          if (line.trim() === '') {
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
                    steps: steps.length > 0 ? [...steps] : undefined,
                  }
                  break

                case 'step': {
                  const stepId = payload.id || `step-${steps.length}`
                  const status = payload.status === 'error' ? 'error'
                    : payload.status === 'running' ? 'running' : 'done'
                  const now = Date.now()
                  const idx = steps.findIndex((s) => s.id === stepId)
                  // running 时记录开始时间；done/error 时算出该步耗时
                  const prev = idx >= 0 ? steps[idx] : undefined
                  const next: ChatStep = {
                    id: stepId,
                    text: payload.text || '',
                    status,
                    startedAt: status === 'running'
                      ? (prev?.startedAt ?? now)
                      : prev?.startedAt,
                    durationMs: status !== 'running' && prev?.startedAt !== undefined
                      ? now - prev.startedAt
                      : prev?.durationMs,
                  }
                  steps = idx >= 0
                    ? steps.map((s, i) => (i === idx ? next : s))
                    : [...steps, next]
                  // 立即回传，让时间线实时更新（此时正文可能还没开始输出）
                  yield {
                    id: 'streaming',
                    role: 'agent',
                    content: fullContent,
                    timestamp: new Date().toISOString(),
                    steps: [...steps],
                  }
                  break
                }

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
                  if (!fullContent.trim() && pendingCards.length === 0 && steps.length === 0) {
                    throw new Error('Agent 未返回内容')
                  }
                  produced = true
                  yield {
                    id: payload.messageId || `msg-${Date.now()}`,
                    role: 'agent',
                    content: fullContent,
                    timestamp: new Date().toISOString(),
                    cards: pendingCards.length > 0 ? pendingCards : undefined,
                    steps: steps.length > 0 ? [...steps] : undefined,
                  }
                  break

                case 'title':
                  // 会话标题总结完成：回调 store 刷新「对话历史」列表
                  // （不构造消息，title 事件是流内最后一条，无需 yield）
                  onTitle?.(payload.title || '', payload.session_id || '')
                  break

                case 'stopped':
                  produced = true
                  yield {
                    id: `msg-${Date.now()}`,
                    role: 'agent',
                    content: fullContent || '对话已停止',
                    timestamp: new Date().toISOString(),
                    cards: pendingCards.length > 0 ? pendingCards : undefined,
                    steps: steps.length > 0 ? [...steps] : undefined,
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
      // 流已结束但从未产出终态消息（done/stopped）：Agent 进程静默退出，
      // 表现为「转圈后空回复」——显式报错让上层可见，而非停在空正文。
      if (!produced) {
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

/* ============================================================
   平台操作确认 / 取消（对话内控制系统，见 docs/chat-platform-control-design.md）
   确认卡上的按钮直连网关端点，不经 LLM 往返；结果卡只认网关回执。
   ============================================================ */

/** 网关回执（confirm / cancel 共用结构） */
export interface PlatformOpReceipt {
  status: 'ok' | 'error'
  op_id?: string
  tool?: string
  result?: Record<string, unknown>
  error?: string
  idempotent?: boolean
}

async function postPlatformOp(path: string, body: Record<string, unknown>): Promise<PlatformOpReceipt> {
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = (await res.json().catch(() => ({}))) as PlatformOpReceipt & { detail?: string }
    if (!res.ok) {
      return { status: 'error', error: data.error || data.detail || `HTTP ${res.status} ${res.statusText}` }
    }
    return data
  } catch (e) {
    return { status: 'error', error: e instanceof Error ? e.message : String(e) }
  }
}

/** 确认执行待确认的平台操作（fingerprint 原样回传，网关复核「确认内容=执行内容」） */
export function confirmPlatformCall(req: {
  opId: string
  token: string
  fingerprint?: string
  sessionId: string
}): Promise<PlatformOpReceipt> {
  // userId 与 sendMessage 保持一致，作为无登录态时的兜底身份
  return postPlatformOp('/api/chat/platform-call/confirm', { ...req, userId: 'default' })
}

/** 取消待确认的平台操作 */
export function cancelPlatformCall(req: {
  opId: string
  sessionId: string
}): Promise<PlatformOpReceipt> {
  return postPlatformOp('/api/chat/platform-call/cancel', { ...req, userId: 'default' })
}

/** 平台操作审计流水条目 */
export interface PlatformOpRow {
  op_id: string
  session_id: string
  tool: string
  risk: string
  status: string
  result_json?: string
  error?: string
  created_at: number
}

/** 查询会话的平台操作审计流水（恢复历史时回放确认卡终态用） */
export async function fetchPlatformOps(sessionId: string, limit = 200): Promise<PlatformOpRow[]> {
  try {
    const res = await fetch(
      `${BASE_URL}/api/chat/platform-ops?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`,
    )
    if (!res.ok) return []
    const data = await res.json()
    return Array.isArray(data.ops) ? data.ops : []
  } catch {
    return []
  }
}

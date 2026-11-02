import { create } from 'zustand'
import type { Message, FileAttachment, InlineCard } from '@/types/chat'
import { MockChatService } from '@/services/mock/chat'
import { ApiChatService, confirmPlatformCall, cancelPlatformCall } from '@/services/api/chat'

// 开发模式下可通过 VITE_USE_MOCK 切换回 mock 服务
const useMock = import.meta.env.VITE_USE_MOCK === 'true'
const chatService = useMock ? new MockChatService() : new ApiChatService()

/** 路径引用：@文件名 占位符与完整路径的映射 */
export interface PathRef {
  name: string
  path: string
}

interface ChatState {
  messages: Message[]
  isSending: boolean
  attachedFiles: FileAttachment[]
  inputText: string
  /** 当前会话 ID：新会话创建时生成唯一 ID，切换历史会话时替换 */
  sessionId: string
  /** 会话列表/标题变更计数，右侧「对话历史」据此刷新 */
  sessionsVersion: number
  /** 拖拽进来的文件/目录路径引用（@文件名 → 完整路径） */
  pathRefs: PathRef[]

  setInputText: (text: string) => void
  addAttachment: (file: FileAttachment) => void
  removeAttachment: (id: string) => void
  addPathRef: (ref: PathRef) => void
  removePathRef: (name: string) => void
  pruneAttachmentsToValidIds: (validIds: Set<string>) => void
  sendMessage: () => Promise<void>
  sendDirectMessage: (content: string) => Promise<void>
  /** 隐藏轮次：把平台操作回执交给助手解读，生成一条新的助手气泡 */
  explainPlatformReceipt: (receipt: Record<string, unknown>) => Promise<void>
  stopMessage: () => void
  addMessage: (msg: Message) => void
  clearMessages: () => void
  loadMessagesFromApi: () => Promise<void>
  /** 加载指定历史会话的消息并切换为当前会话 */
  switchSession: (sessionId: string) => Promise<void>
  /** 删除一个历史会话 */
  deleteSession: (sessionId: string) => Promise<void>
  /** 平台确认卡：确认/取消待执行操作，结果卡追加到同一消息 */
  resolvePlatformOp: (messageId: string, cardId: string, action: 'confirm' | 'cancel') => Promise<void>
}

/** 生成会话唯一 ID（浏览器无 crypto.randomUUID 时退化为时间戳 + 随机数） */
function newSessionId(): string {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  } catch { /* ignore */ }
  return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

let nextId = 1
function genId(): string {
  return `msg-${Date.now()}-${nextId++}`
}

/**
 * 卡片 `_action` 钩子：平台操作成功后自动刷新对应空间。
 * （定时任务视图 CronJobsView 挂载时自动 reload，无需在此刷新。）
 */
async function refreshSpacesForAction(action: unknown): Promise<void> {
  if (typeof action !== 'string' || !action) return
  try {
    const { useResourceStore } = await import('@/stores/resource-store')
    const rs = useResourceStore.getState()
    switch (action) {
      case 'register_dataset':
        await rs.fetchDatasetsFromApi()
        break
      case 'register_model':
        await rs.fetchModelsFromApi()
        break
      case 'create_tool':
      case 'modify_tool':
      case 'register_tool':
        await rs.fetchToolsFromApi()
        break
      case 'create_agent':
      case 'register_agent':
      case 'start_agent':
      case 'stop_agent':
        await rs.fetchAgentsFromApi()
        break
    }
  } catch { /* 刷新失败不影响对话 */ }
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isSending: false,
  attachedFiles: [],
  inputText: '',
  sessionId: newSessionId(),
  sessionsVersion: 0,
  pathRefs: [],

  setInputText: (text) => set({ inputText: text }),

  // 同一个文件重复附加没有意义（拖拽可能连拖两次，双击也可能手抖），按 id 去重。
  // 注意：必须返回对象而非裸数组，zustand 会把返回值 Object.assign 进 state，
  // 返回数组会让下标变成 state 的键，直接污染整个 store。
  addAttachment: (file) =>
    set((s) =>
      s.attachedFiles.some((f) => f.id === file.id)
        ? { attachedFiles: s.attachedFiles }
        : { attachedFiles: [...s.attachedFiles, file] },
    ),

  removeAttachment: (id) =>
    set((s) => ({ attachedFiles: s.attachedFiles.filter((f) => f.id !== id) })),

  addPathRef: (ref) =>
    set((s) => ({ pathRefs: [...s.pathRefs, ref] })),

  removePathRef: (name) =>
    set((s) => ({
      pathRefs: s.pathRefs.filter((r) => r.name !== name),
    })),

  // 工作区间是文件的唯一来源：文件被删掉后，输入框里待发的附件和
  // 历史消息上的附件标签都会变成指向不存在路径的悬空引用，
  // 继续发给后端只会让工具执行时找不到文件。故由工作区间在变动后
  // 传入"仍然有效的文件 id 集合"，一次性清掉失效引用。
  // （清空工作区即传空集合；将来若支持删除单个文件，传剩余集合即可复用。）
  pruneAttachmentsToValidIds: (validIds) =>
    set((s) => {
      const attachedFiles = s.attachedFiles.filter((f) => validIds.has(f.id))
      let changed = attachedFiles.length !== s.attachedFiles.length

      const messages = s.messages.map((m) => {
        if (!m.attachments || m.attachments.length === 0) return m
        const kept = m.attachments.filter((f) => validIds.has(f.id))
        if (kept.length === m.attachments.length) return m
        changed = true
        // 全部失效时置为 undefined 而非空数组，避免气泡渲染出空的附件区
        return { ...m, attachments: kept.length > 0 ? kept : undefined }
      })

      // 返回原 state 表示无变化：zustand 内部用 Object.is 比较，
      // 引用相同则跳过通知，避免消息列表无谓重渲染。
      if (!changed) return s
      return { attachedFiles, messages }
    }),

  addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),

  // 清空对话 = 开一个新会话（旧会话保留在「对话历史」里，可随时点回来继续）
  clearMessages: () => {
    set((s) => ({
      messages: [],
      sessionId: newSessionId(),
      sessionsVersion: s.sessionsVersion + 1,
    }))
  },

  // 启动时从后端恢复最近一次会话（此前只存 localStorage，换浏览器即丢）
  loadMessagesFromApi: async () => {
    if (get().messages.length > 0) return
    try {
      const res = await fetch('/api/chat/sessions?limit=1')
      if (!res.ok) return
      const data = await res.json()
      const latest = (data.sessions || [])[0]
      if (latest?.session_id) await get().switchSession(latest.session_id)
    } catch { /* 后端不可用时保持空对话 */ }
  },

  // 点击「对话历史」中的会话：加载消息并把当前会话切过去，后续消息接在该会话上
  switchSession: async (sessionId) => {
    if (!sessionId) return
    try {
      const res = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`)
      if (!res.ok) return
      const data = await res.json()
      const restored: Message[] = (data.messages || []).map(
        (m: { id: number; role: string; content: string; cards?: unknown[]; created_at: number }) => ({
          id: `db-${m.id}`,
          role: m.role === 'agent' ? 'agent' : 'user',
          content: m.content,
          timestamp: new Date(m.created_at * 1000).toISOString(),
          cards: Array.isArray(m.cards) && m.cards.length > 0 ? m.cards as Message['cards'] : undefined,
        }),
      )
      set({ messages: restored, sessionId, isSending: false })
    } catch { /* ignore */ }
  },

  // 删除历史会话；若删的是当前会话，则回到新会话
  deleteSession: async (sessionId) => {
    try {
      await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
    } catch { /* ignore */ }
    set((s) => ({
      sessionsVersion: s.sessionsVersion + 1,
      ...(s.sessionId === sessionId
        ? { messages: [], sessionId: newSessionId() }
        : {}),
    }))
  },

  stopMessage: () => {
    chatService.stopChat().catch(() => {})
    set({ isSending: false })
  },

  // 平台确认卡的确认/取消：直连网关端点（不经 LLM），网关回执以结果卡呈现。
  resolvePlatformOp: async (messageId, cardId, action) => {
    const msg = get().messages.find((m) => m.id === messageId)
    const card = msg?.cards?.find((c) => c.id === cardId)
    if (!msg || !card || card.type !== 'platform-confirm') return
    const d = card.data
    // 重复点击/终态忽略（网关侧也有幂等与状态机兜底）
    if (d.submitting || (typeof d.status === 'string' && d.status !== 'pending')) return
    const opId = String(d.op_id || '')
    if (!opId) return

    const patchCard = (patch: Record<string, unknown>) =>
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id !== messageId
            ? m
            : {
                ...m,
                cards: m.cards?.map((c) =>
                  c.id === cardId ? { ...c, data: { ...c.data, ...patch } } : c,
                ),
              },
        ),
      }))
    const appendCard = (newCard: InlineCard) =>
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id !== messageId ? m : { ...m, cards: [...(m.cards || []), newCard] },
        ),
      }))

    patchCard({ submitting: action, error: undefined })

    const receipt =
      action === 'confirm'
        ? await confirmPlatformCall({
            opId,
            token: String(d.token || ''),
            fingerprint: String(d.fingerprint || ''),
            sessionId: get().sessionId,
          })
        : await cancelPlatformCall({ opId, sessionId: get().sessionId })

    if (action === 'cancel') {
      // 取消失败（已过期/已确认等）：原因直接展示在确认卡上
      patchCard(
        receipt.status === 'ok'
          ? { submitting: undefined, status: 'cancelled' }
          : { submitting: undefined, error: receipt.error || '取消失败' },
      )
      return
    }

    // confirm：成功/失败都追加结果卡（结果只认网关回执）
    const ok = receipt.status === 'ok' || receipt.status === 'executed'
    const tool = String(receipt.tool || d.tool || '')
    patchCard({
      submitting: undefined,
      status: ok ? 'confirmed' : 'failed',
      error: ok ? undefined : receipt.error || '执行失败',
    })
    appendCard({
      id: `card-${Date.now()}-platform-result`,
      type: 'platform-result',
      title: ok ? '平台操作已执行' : '平台操作未执行',
      summary: String(d.summary || ''),
      data: {
        tool,
        status: ok ? 'ok' : 'error',
        result: receipt.result,
        error: receipt.error,
        // 工具名即动作名（register_dataset/create_tool/...），驱动空间自动刷新
        _action: ok ? tool : undefined,
      },
    })
    if (ok) await refreshSpacesForAction(tool)
    // 把网关回执交回助手，用自然语言解读进对话（隐藏轮次）。
    // 助手繁忙时跳过——结果卡本身已完整展示，不影响功能。
    void get().explainPlatformReceipt({
      tool,
      status: ok ? 'ok' : 'error',
      summary: String(d.summary || ''),
      result: receipt.result,
      error: receipt.error,
    })
  },

  explainPlatformReceipt: async (receipt) => {
    // 平台确认卡执行成功后的隐藏轮次：不落 user 消息，仅生成助手解读气泡
    if (get().isSending) return
    set({ isSending: true })
    const agentMsgId = genId()
    set((s) => ({
      messages: [...s.messages, {
        id: agentMsgId, role: 'agent', content: '',
        timestamp: new Date().toISOString(),
      }],
    }))
    try {
      const stream = chatService.sendMessage({
        content: '（平台操作回执）', kind: 'platform_receipt', receipt,
        sessionId: get().sessionId,
      })
      for await (const chunk of stream) {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === agentMsgId
              ? { ...m, content: chunk.content, cards: chunk.cards || m.cards }
              : m
          ),
        }))
      }
    } catch { /* 解读失败不影响结果卡本身 */ }
    finally {
      set((s) => ({ isSending: false, sessionsVersion: s.sessionsVersion + 1 }))
    }
  },

  sendDirectMessage: async (content) => {
    // 直接发送一条消息（不走输入框）
    if (!content.trim() || get().isSending) return
    set({ inputText: content })
    await get().sendMessage()
  },

  sendMessage: async () => {
    const { inputText, attachedFiles, pathRefs } = get()
    if (!inputText.trim() && attachedFiles.length === 0 && pathRefs.length === 0) return

    // 把拖拽的文件/目录完整路径追加到文本末尾（作为路径参数提交）
    const paths = pathRefs.map((r) => r.path).filter(Boolean)
    const finalText = [inputText.trim(), ...paths].filter(Boolean).join(' ')

    set({ isSending: true, inputText: '', attachedFiles: [], pathRefs: [] })

    // 添加用户消息
    const userMsg: Message = {
      id: genId(),
      role: 'user',
      content: finalText,
      timestamp: new Date().toISOString(),
      attachments: attachedFiles.length > 0 ? attachedFiles : undefined,
    }
    set((s) => ({ messages: [...s.messages, userMsg] }))

    // 创建 Agent 占位消息（流式更新）
    const agentMsgId = genId()
    const agentMsg: Message = {
      id: agentMsgId,
      role: 'agent',
      content: '',
      timestamp: new Date().toISOString(),
    }
    set((s) => ({ messages: [...s.messages, agentMsg] }))

    try {
      // 获取当前工具空间的工具 ID 列表
      let workspaceToolIds: string[] = []
      try {
        const { useWorkspaceToolStore } = await import('@/stores/workspace-tool-store')
        workspaceToolIds = useWorkspaceToolStore.getState().tools.map(t => t.id)
      } catch { /* ignore */ }
      const stream = chatService.sendMessage({ content: finalText, attachments: attachedFiles, workspaceToolIds, sessionId: get().sessionId })

      for await (const chunk of stream) {
        // 卡片 _action 钩子：平台操作成功后自动刷新对应空间
        if (chunk.cards) {
          for (const card of chunk.cards) {
            const cardData = card.data
            if (!cardData) continue
            if (cardData.registered_dataset_id) {
              refreshSpacesForAction('register_dataset')
              continue
            }
            refreshSpacesForAction(cardData._action)
          }
        }
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === agentMsgId
              ? {
                  ...m,
                  content: chunk.content,
                  cards: chunk.cards || m.cards,
                }
              : m
          ),
        }))
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        // 用户主动停止，不显示错误
      } else {
        const errMsg = e instanceof Error ? e.message : String(e)
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === agentMsgId
              ? { ...m, content: `[WARN] ${errMsg || '响应失败，请重试。'}` }
              : m
          ),
        }))
      }
    } finally {
      set((s) => ({ isSending: false, sessionsVersion: s.sessionsVersion + 1 }))
      // 标题由后端 LLM 异步总结，稍后再刷一次会话列表让标题显示出来
      setTimeout(() => set((s) => ({ sessionsVersion: s.sessionsVersion + 1 })), 3000)
      // 刷新任务历史与最近使用（后端在 done 事件前已写入 SQLite）
      try {
        const { useResourceStore } = await import('@/stores/resource-store')
        useResourceStore.getState().fetchTasksFromApi()
      } catch { /* ignore */ }
    }
  },
}))

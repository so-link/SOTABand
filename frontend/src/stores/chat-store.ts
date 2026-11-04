import { create } from 'zustand'
import type { Message, FileAttachment, InlineCard } from '@/types/chat'
import { MockChatService } from '@/services/mock/chat'
import { ApiChatService, confirmPlatformCall, cancelPlatformCall } from '@/services/api/chat'

// 开发模式下可通过 VITE_USE_MOCK 切换回 mock 服务
const useMock = import.meta.env.VITE_USE_MOCK === 'true'
const chatService = useMock ? new MockChatService() : new ApiChatService()

/** 引用：文件（file_id）或数据集（dataset_id）。
 *  文件引用发送时带内容预览；数据集引用只带 id，由 LLM 用
 *  list_datasets/get_dataset_info 查到并传给工具。 */
export interface PathRef {
  name: string
  fileId: string
  /** dataset:<id> 时为数据集引用；否则为文件引用 */
  kind: 'file' | 'dataset'
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
  /** 平台操作确认策略：手工（每张确认卡都要用户点）/ 自动（收到即执行） */
  confirmPolicy: 'manual' | 'auto'
  setConfirmPolicy: (p: 'manual' | 'auto') => void

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

/** 从消息文本里恢复数据集引用（dataset:<id> 拼在文本里，历史回放时补成 chip） */
function restoreDatasetRefs(content: string): Message['attachments'] {
  const ids = (content || '').match(/dataset:[A-Za-z0-9_.-]+/g) || []
  return ids.map((fid) => ({
    id: fid,
    fileName: fid.replace('dataset:', ''),
    filePath: '',
    fileSize: 0,
    format: '',
    fileId: fid,
  }))
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

/**
 * 从历史恢复后回放平台操作终态：落库的确认卡是下发时的 pending 快照，
 * 确认/执行发生在前端内存里，刷新后会「退回待确认」。用审计流水（按
 * owner+session 过滤）把 op 的最终状态写回卡片 data，并为已执行/失败的
 * 操作补上结果卡，使历史视图与执行时一致。
 */
async function replayPlatformCards(sessionId: string): Promise<void> {
  try {
    const { fetchPlatformOps } = await import('@/services/api/chat')
    const ops = await fetchPlatformOps(sessionId)
    if (ops.length === 0) return
    // op_id → 最终审计行（后端按时间倒序返回，首次出现即最新）
    const finalByOp = new Map<string, (typeof ops)[number]>()
    for (const o of ops) {
      if (o.op_id && !finalByOp.has(o.op_id)) finalByOp.set(o.op_id, o)
    }
    const stateOf = (status: string): string | null => {
      if (status === 'executed') return 'confirmed'
      if (status === 'failed') return 'failed'
      if (status === 'cancelled') return 'cancelled'
      if (status === 'superseded') return 'superseded'
      // pending / confirmed / confirm_rejected 等中间态与异常态不改卡片：
      // 是否过期由卡片按 expires_at 自行判定，避免误标成"未执行成功"
      return null
    }
    let changedAny = false
    const before = useChatStore.getState().messages
    const patched = before.map((m) => {
      if (!m.cards?.length) return m
      let cards = m.cards
      let changed = false
      // ① 确认卡回放到终态
      cards = cards.map((c) => {
        if (c.type !== 'platform-confirm') return c
        const opId = String((c.data as Record<string, unknown>)?.op_id || '')
        const row = opId ? finalByOp.get(opId) : undefined
        const st = row ? stateOf(row.status) : null
        if (!st) return c
        changed = true
        return { ...c, data: { ...c.data, status: st, submitting: undefined } }
      })
      // ② 已执行/失败但缺结果卡（结果卡只在内存中追加过）→ 按审计补上
      for (const c of cards) {
        if (c.type !== 'platform-confirm') continue
        const d = c.data as Record<string, unknown>
        const opId = String(d.op_id || '')
        const row = opId ? finalByOp.get(opId) : undefined
        if (!row || !['executed', 'failed'].includes(row.status)) continue
        if (cards.some((x) => x.type === 'platform-result'
          && String((x.data as Record<string, unknown>)?.op_id || '') === opId)) continue
        let result: unknown
        try { result = row.result_json ? JSON.parse(row.result_json) : undefined } catch { result = undefined }
        cards = [...cards, {
          id: `card-replay-${opId}`,
          type: 'platform-result' as const,
          title: row.status === 'executed' ? '平台操作已执行' : '平台操作未执行',
          summary: String(d.summary || ''),
          data: {
            op_id: opId,
            tool: row.tool,
            status: row.status === 'executed' ? 'ok' : 'error',
            result,
            error: row.error || undefined,
          },
        }]
        changed = true
      }
      return changed ? { ...m, cards } : m
    })
    changedAny = patched.some((m, i) => m !== before[i])
    // 回放请求期间用户可能已切走会话，此时不能再覆盖新会话的消息
    if (changedAny && useChatStore.getState().sessionId === sessionId) {
      useChatStore.setState({ messages: patched })
    }
  } catch { /* 回放失败保持快照展示 */ }
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isSending: false,
  attachedFiles: [],
  inputText: '',
  sessionId: newSessionId(),
  sessionsVersion: 0,
  pathRefs: [],
  // 确认策略：默认手工，用户可切换并持久化到 localStorage
  confirmPolicy: (typeof localStorage !== 'undefined' &&
    (localStorage.getItem('sota.platform_confirm_policy') === 'auto' ? 'auto' : 'manual')) as 'manual' | 'auto',
  setConfirmPolicy: (p) => {
    try { localStorage.setItem('sota.platform_confirm_policy', p) } catch { /* ignore */ }
    set({ confirmPolicy: p })
  },

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

  // 「我的文件」是文件的唯一来源：文件被删掉后，输入框里待发的附件和
  // 历史消息上的附件标签都会变成指向不存在路径的悬空引用，
  // 继续发给后端只会让工具执行时找不到文件。故由「我的文件」在变动后
  // 传入"仍然有效的文件 id 集合"，一次性清掉失效引用。
  // （清空「我的文件」即传空集合；将来若支持删除单个文件，传剩余集合即可复用。）
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

  // 从消息文本里恢复数据集引用（dataset:<id> 拼在文本里，历史回放时补成 chip）
  // 点击「对话历史」中的会话：加载消息并把当前会话切过去，后续消息接在该会话上
  switchSession: async (sessionId) => {
    if (!sessionId) return
    try {
      const res = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`)
      if (!res.ok) return
      const data = await res.json()
      const restored: Message[] = (data.messages || []).map(
        (m: { id: number; role: string; content: string; cards?: unknown[]; attachments?: unknown[]; created_at: number }) => ({
          id: `db-${m.id}`,
          role: m.role === 'agent' ? 'agent' : 'user',
          content: m.content,
          timestamp: new Date(m.created_at * 1000).toISOString(),
          cards: Array.isArray(m.cards) && m.cards.length > 0 ? m.cards as Message['cards'] : undefined,
          // 历史回放：附件/引用一并恢复（刷新后仍能看到）
          attachments: Array.isArray(m.attachments) && m.attachments.length > 0
            ? m.attachments as Message['attachments']
            : restoreDatasetRefs(m.content),
        }),
      )
      // 落库的卡片是下发时的 pending 快照：用审计流水把已确认/已执行/已取消的
      // 操作回放成终态，并补上缺失的执行结果卡，刷新后不再看到假「待确认」。
      set({ messages: restored, sessionId, isSending: false })
      void replayPlatformCards(sessionId)
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
        op_id: opId,
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

    // 引用分两类：文件作为附件传（后端读内容进 prompt）；数据集引用只把
    // id 拼进消息文本，由 LLM 用平台工具查到（不传内容）。
    const finalText = inputText.trim()
    const fileRefs = pathRefs.filter((r) => r.kind !== 'dataset')
    const datasetRefs = pathRefs.filter((r) => r.kind === 'dataset')
    const datasetTokens = datasetRefs.map((r) => r.fileId).filter(Boolean)
    const textWithDatasets = [finalText, ...datasetTokens].filter(Boolean).join(' ')
    const fileAttachments: FileAttachment[] = fileRefs.map((r) => ({
      id: r.fileId, fileName: r.name, fileId: r.fileId, filePath: '', fileSize: 0, format: '',
    }))
    const allAttachments = [...attachedFiles, ...fileAttachments]

    set({ isSending: true, inputText: '', attachedFiles: [], pathRefs: [] })

    // 添加用户消息
    const userMsg: Message = {
      id: genId(),
      role: 'user',
      content: textWithDatasets,
      timestamp: new Date().toISOString(),
      attachments: allAttachments.length > 0 ? allAttachments : undefined,
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
      const stream = chatService.sendMessage(
        { content: textWithDatasets, attachments: allAttachments, workspaceToolIds, sessionId: get().sessionId },
        {
          // 后端标题总结完成后立即推送：刷新「对话历史」即时显示新标题
          onTitle: () => set((s) => ({ sessionsVersion: s.sessionsVersion + 1 })),
        },
      )

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
            // 自动确认策略：工作空间工具执行确认卡（workspace-exec）自动确认。
            // 与 platform-confirm 不同，这里走「选择工具」消息触发后端执行。
            if (card.type === 'tool-confirm' &&
                cardData.kind === 'workspace-exec' &&
                get().confirmPolicy === 'auto') {
              const candidate = (cardData.candidates as Array<{ name: string }>)[0]
              if (candidate) {
                void get().sendDirectMessage(`我选择第1个工具：${candidate.name}`)
              }
              continue
            }
            // 自动确认策略：收到 platform-confirm 卡即直接执行，不再等用户点按钮
            if (card.type === 'platform-confirm' &&
                get().confirmPolicy === 'auto' &&
                cardData.status === 'pending') {
              const msgWithThisCard = get().messages.find(m =>
                m.id === agentMsgId && m.cards?.some(c => c.id === card.id))
              // 若当前 chunk 还没把 card 挂到 agentMsg 上（同一次 set 里），
              // 先保证卡片在消息上再 resolve——resolvePlatformOp 内部会再打
              // 一次 set 刷新结果卡
              if (!msgWithThisCard) {
                set((s) => ({
                  messages: s.messages.map((m) =>
                    m.id === agentMsgId
                      ? { ...m, cards: [...(m.cards || []), card] }
                      : m
                  ),
                }))
              }
              void get().resolvePlatformOp(agentMsgId, card.id, 'confirm')
            }
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

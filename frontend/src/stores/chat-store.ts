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
  /** 首屏初始化完成：历史记录加载（成功/失败/跳过）结束后置 true。
      用于 ChatEmptyState 判断是否可以渲染最终内容，避免资源+历史加载期间的闪刷。 */
  initialized: boolean
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
  /** 登出/换人：清空对话、待发附件与输入框，开一个新会话 */
  resetForUserSwitch: () => void
  loadMessagesFromApi: () => Promise<void>
  /** 加载指定历史会话的消息并切换为当前会话 */
  switchSession: (sessionId: string) => Promise<void>
  /** 删除一个历史会话 */
  deleteSession: (sessionId: string) => Promise<void>
  /** 平台确认卡：确认/取消待执行操作，结果卡追加到同一消息。
   *  auto=true 表示由「自动确认」策略触发（收卡即执行），用于区分同一
   *  点击路径下的后续行为（如 start_agent 成功后自动续跑原请求）。 */
  resolvePlatformOp: (messageId: string, cardId: string, action: 'confirm' | 'cancel',
    auto?: boolean) => Promise<void>
  /** 工具选择/执行确认卡：点选后回写 selected 标记，按钮即刻失效防止重复选择 */
  markCardSelected: (messageId: string, cardId: string, label: string) => void
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
 * 执行确认卡终态同步：确认卡所在轮次与工具执行轮次天然分开（确认卡下发后
 * 本轮即结束，执行发生在确认之后的新消息里），因此**不能**把结果搬到确认卡
 * 上——那会让执行结果出现在用户选择之前，且选择卡本身被改写得看不见了。
 *
 * 这里只按 target_card_id 给确认卡打终态徽标（已执行/执行失败），结果数据留在
 * 结果卡原位展示（时序正确：确认卡 → 用户选择 → 结果卡 → 助手分析）。
 */
function markExecCardStatus(messages: Message[]): Message[] {
  const statusByTarget = new Map<string, 'executed' | 'failed'>()
  for (const m of messages) {
    for (const c of m.cards || []) {
      const d = c.data as Record<string, unknown>
      if (c.type !== 'result-summary') continue
      const target = String(d?.target_card_id || '')
      if (!target) continue
      const res = (d.result as Record<string, unknown>) || null
      const hasError = d.error !== undefined && d.error !== null && d.error !== ''
      statusByTarget.set(target,
        (hasError || (!!res && typeof res.status === 'string' && res.status !== 'success'))
          ? 'failed' : 'executed')
    }
  }
  if (statusByTarget.size === 0) return messages
  let changed = false
  const out = messages.map((m) => {
    if (!m.cards?.some((c) => statusByTarget.has(c.id))) return m
    changed = true
    return {
      ...m,
      cards: m.cards.map((c) => {
        const st = statusByTarget.get(c.id)
        return st && c.type === 'tool-confirm'
          ? { ...c, data: { ...c.data, exec_status: st } }
          : c
      }),
    }
  })
  return changed ? out : messages
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
  initialized: false,
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

  // 登出/换人：上一个账号的对话与输入草稿一律丢弃
  resetForUserSwitch: () => {
    set((s) => ({
      messages: [],
      attachedFiles: [],
      pathRefs: [],
      inputText: '',
      isSending: false,
      sessionId: newSessionId(),
      sessionsVersion: s.sessionsVersion + 1,
      initialized: false,
    }))
  },

  // 启动时从后端恢复最近一次会话（此前只存 localStorage，换浏览器即丢）
  loadMessagesFromApi: async () => {
    if (get().messages.length > 0) {
      set({ initialized: true })
      return
    }
    try {
      const res = await fetch('/api/chat/sessions?limit=1')
      if (!res.ok) return
      const data = await res.json()
      const latest = (data.sessions || [])[0]
      if (latest?.session_id) await get().switchSession(latest.session_id)
    } catch { /* 后端不可用时保持空对话 */ }
    finally {
      set({ initialized: true })
    }
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
          // 落库卡片是 {type, card_id?, title, summary, data} 原始事件形状：
          // 规范化成前端 InlineCard（稳定 id 优先取 card_id——执行卡/结果卡
          // 的回灌按它配对；旧数据无 card_id 则按消息+序号生成），否则
          // 历史卡片没有 id，重复选择禁用与结果回灌都会失效。
          cards: Array.isArray(m.cards) && m.cards.length > 0
            ? (m.cards as Array<Record<string, unknown>>).map((c, i) => ({
                id: (typeof c.card_id === 'string' && c.card_id) || `db-card-${m.id}-${i}`,
                type: c.type as InlineCard['type'],
                title: String(c.title || ''),
                summary: String(c.summary || ''),
                data: (c.data as Record<string, unknown>) || {},
              }))
            : undefined,
          // 历史回放：附件/引用一并恢复（刷新后仍能看到）
          attachments: Array.isArray(m.attachments) && m.attachments.length > 0
            ? m.attachments as Message['attachments']
            : restoreDatasetRefs(m.content),
        }),
      )
      // 落库的卡片是下发时的 pending 快照：用审计流水把已确认/已执行/已取消的
      // 操作回放成终态，并补上缺失的执行结果卡，刷新后不再看到假「待确认」。
      set({ messages: markExecCardStatus(restored), sessionId, isSending: false })
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
  resolvePlatformOp: async (messageId, cardId, action, auto) => {
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
    // start_agent 确认成功 → 自动续跑原请求。
    // 后端 _dispatch_to_agent 遇到「Agent 未运行」只发起启动，并把原请求
    // 存进 _pending_agents 后 return；启动经确认卡异步完成，若没人再触发
    // 一轮对话，用户的原始提问（如「广州明天天气怎么样？」）就被丢弃。
    // 这里补发原话（confirm_data.request 由后端随卡下发），让后端把待办
    // 请求重投给刚启动的 Agent。
    // 仅限自动策略：手工点「确认执行」时用户正看着卡片，由下面的回执解读
    // 自然衔接，不额外补一轮。续跑必须排在解读之前——解读会置 isSending，
    // 一旦它先跑，续跑会被忙线拦掉；反过来续跑忙线时解读自行跳过。
    if (auto && ok && tool === 'start_agent') {
      const original = String(d.request || '').trim()
      if (original) void get().sendDirectMessage(original)
    }
    // 把网关回执交回助手，用自然语言解读进对话（隐藏轮次）。
    // 助手繁忙（含上面的自动续跑）时跳过——结果卡本身已完整展示。
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
              ? { ...m, content: chunk.content, cards: chunk.cards || m.cards, steps: chunk.steps || m.steps }
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

  markCardSelected: (messageId, cardId, label) => {
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id !== messageId
          ? m
          : {
              ...m,
              cards: m.cards?.map((c) =>
                c.id === cardId ? { ...c, data: { ...c.data, selected: label } } : c,
              ),
            },
      ),
    }))
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
    // 附件只带 file_id：真实大小/格式由后端从登记表解析（前端不猜、不查）
    const fileAttachments: FileAttachment[] = fileRefs.map((r) => ({
      id: r.fileId,
      fileName: r.name,
      fileId: r.fileId,
      filePath: '',
      fileSize: 0,
      format: '',
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

    // 自动确认队列：流式进行中 isSending=true，此时直接调 sendDirectMessage /
    // resolvePlatformOp 会被「忙线」拦掉——auto 模式因此形同虚设，确认卡一直
    // 停在原地。统一收集到队列，流结束（isSending 归位）后按序执行。
    // 声明在 try 之外：finally 也要访问它（try 内声明会触发 ReferenceError）。
    const autoConfirmQueue: Array<() => void> = []

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
                autoConfirmQueue.push(
                  () => void get().sendDirectMessage(`我选择第1个工具：${candidate.name}`))
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
              autoConfirmQueue.push(
                () => void get().resolvePlatformOp(agentMsgId, card.id, 'confirm', true))
            }
          }
        }
        set((s) => ({
          messages: markExecCardStatus(s.messages.map((m) =>
            m.id === agentMsgId
              ? {
                  ...m,
                  content: chunk.content,
                  cards: chunk.cards || m.cards,
                  steps: chunk.steps || m.steps,
                }
              : m
          )),
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
      // 忙线解除后再执行自动确认（队列保序，逐条发起新一轮对话/网关确认）
      for (const run of autoConfirmQueue) run()
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

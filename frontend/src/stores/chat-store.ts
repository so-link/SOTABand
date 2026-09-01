import { create } from 'zustand'
import type { Message, FileAttachment } from '@/types/chat'
import { MockChatService } from '@/services/mock/chat'
import { ApiChatService } from '@/services/api/chat'

// 开发模式下可通过 VITE_USE_MOCK 切换回 mock 服务
const useMock = import.meta.env.VITE_USE_MOCK === 'true'
const chatService = useMock ? new MockChatService() : new ApiChatService()

interface ChatState {
  messages: Message[]
  isSending: boolean
  attachedFiles: FileAttachment[]
  inputText: string

  setInputText: (text: string) => void
  addAttachment: (file: FileAttachment) => void
  removeAttachment: (id: string) => void
  pruneAttachmentsToValidIds: (validIds: Set<string>) => void
  sendMessage: () => Promise<void>
  stopMessage: () => void
  addMessage: (msg: Message) => void
  clearMessages: () => void
}

let nextId = 1
function genId(): string {
  return `msg-${Date.now()}-${nextId++}`
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [
    {
      id: 'msg-welcome',
      role: 'system',
      content:
        '欢迎回来！当前工作区间：**my_project/**  \n上次处理了 eeg_data/subj01.edf  \n可用工具：8 · 可用Agent：6 · 任务历史：3',
      timestamp: new Date().toISOString(),
    },
  ],
  isSending: false,
  attachedFiles: [],
  inputText: '',

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

  clearMessages: () =>
    set({
      messages: [
        {
          id: 'msg-welcome',
          role: 'system',
          content: '对话已清空。有什么可以帮你的？',
          timestamp: new Date().toISOString(),
        },
      ],
    }),

  stopMessage: () => {
    chatService.stopChat().catch(() => {})
    set({ isSending: false })
  },

  sendMessage: async () => {
    const { inputText, attachedFiles } = get()
    if (!inputText.trim() && attachedFiles.length === 0) return

    set({ isSending: true, inputText: '', attachedFiles: [] })

    // 添加用户消息
    const userMsg: Message = {
      id: genId(),
      role: 'user',
      content: inputText,
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
      const stream = chatService.sendMessage({ content: inputText, attachments: attachedFiles, workspaceToolIds })

      for await (const chunk of stream) {
        // 检查是否有新数据集注册，自动刷新数据空间列表
        if (chunk.cards) {
          for (const card of chunk.cards) {
            const cardData = (card as Record<string, unknown>).data as Record<string, unknown> | undefined
            if (cardData?.registered_dataset_id) {
              try {
                const { useResourceStore } = await import('@/stores/resource-store')
                useResourceStore.getState().fetchDatasetsFromApi()
              } catch { /* ignore */ }
              break
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
              ? { ...m, content: `⚠️ ${errMsg || '响应失败，请重试。'}` }
              : m
          ),
        }))
      }
    } finally {
      set({ isSending: false })
    }
  },
}))

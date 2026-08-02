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

  addAttachment: (file) =>
    set((s) => ({ attachedFiles: [...s.attachedFiles, file] })),

  removeAttachment: (id) =>
    set((s) => ({ attachedFiles: s.attachedFiles.filter((f) => f.id !== id) })),

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
        // 检查是否有新数据集注册，自动加入数据空间并刷新列表
        if (chunk.cards) {
          for (const card of chunk.cards) {
            const cardData = (card as Record<string, unknown>).data as Record<string, unknown> | undefined
            if (cardData?.registered_dataset_id || cardData?._action === 'register_dataset') {
              try {
                const { useResourceStore } = await import('@/stores/resource-store')
                useResourceStore.getState().fetchDatasetsFromApi()
              } catch { /* ignore */ }
              // 自动添加到数据空间
              try {
                const { useWorkspaceDatasetStore } = await import('@/stores/workspace-dataset-store')
                const dsId = (cardData?.registered_dataset_id || cardData?.dataset_id) as string
                const dsName = (cardData?.name) as string || dsId
                const dsTags = (cardData?.tags as string[]) || []
                if (dsId) {
                  useWorkspaceDatasetStore.getState().addDataset({
                    id: dsId,
                    name: dsName,
                    tags: dsTags,
                    loadedAt: new Date().toISOString(),
                  })
                }
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

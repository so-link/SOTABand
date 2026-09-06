import { create } from 'zustand'
import type { Message, FileAttachment } from '@/types/chat'
import { MockChatService } from '@/services/mock/chat'
import { ApiChatService } from '@/services/api/chat'

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
  /** 拖拽进来的文件/目录路径引用（@文件名 → 完整路径） */
  pathRefs: PathRef[]

  setInputText: (text: string) => void
  addAttachment: (file: FileAttachment) => void
  removeAttachment: (id: string) => void
  addPathRef: (ref: PathRef) => void
  removePathRef: (name: string) => void
  sendMessage: () => Promise<void>
  sendDirectMessage: (content: string) => Promise<void>
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
  pathRefs: [],

  setInputText: (text) => set({ inputText: text }),

  addAttachment: (file) =>
    set((s) => ({ attachedFiles: [...s.attachedFiles, file] })),

  removeAttachment: (id) =>
    set((s) => ({ attachedFiles: s.attachedFiles.filter((f) => f.id !== id) })),

  addPathRef: (ref) =>
    set((s) => ({ pathRefs: [...s.pathRefs, ref] })),

  removePathRef: (name) =>
    set((s) => ({
      pathRefs: s.pathRefs.filter((r) => r.name !== name),
    })),

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
      const stream = chatService.sendMessage({ content: finalText, attachments: attachedFiles, workspaceToolIds })

      for await (const chunk of stream) {
        // 检查是否有新数据集注册，自动加入数据空间并刷新列表
        if (chunk.cards) {
          for (const card of chunk.cards) {
            const cardData = (card as Record<string, unknown>).data as Record<string, unknown> | undefined
            if (cardData?.registered_dataset_id || cardData?._action === 'register_dataset' || cardData?._action === 'register_model') {
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

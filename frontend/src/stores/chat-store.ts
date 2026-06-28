import { create } from 'zustand'
import type { Message, FileAttachment } from '@/types/chat'
import { MockChatService } from '@/services/mock/chat'

const chatService = new MockChatService()

interface ChatState {
  messages: Message[]
  isSending: boolean
  attachedFiles: FileAttachment[]
  inputText: string

  setInputText: (text: string) => void
  addAttachment: (file: FileAttachment) => void
  removeAttachment: (id: string) => void
  sendMessage: () => Promise<void>
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
      const stream = chatService.sendMessage({ content: inputText, attachments: attachedFiles })

      for await (const chunk of stream) {
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
    } catch {
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === agentMsgId
            ? { ...m, content: '⚠️ 响应失败，请重试。' }
            : m
        ),
      }))
    } finally {
      set({ isSending: false })
    }
  },
}))

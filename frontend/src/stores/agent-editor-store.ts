import { create } from 'zustand'
import { agentApi } from '@/services/api/agent'

export type EditorStep = 1 | 2 | 3 | 4

interface AgentEditorState {
  step: EditorStep
  description: string
  generatedMd: string
  generatedCode: string
  sandboxResults: Record<string, unknown> | null
  registeredId: string | null
  isGenerating: boolean
  error: string | null

  setDescription: (text: string) => void
  setGeneratedMd: (md: string) => void
  generateSpec: () => Promise<void>
  generateCode: () => Promise<void>
  registerAgent: () => Promise<void>
  setStep: (step: EditorStep) => void
  reset: () => void
}

export const useAgentEditorStore = create<AgentEditorState>((set, get) => ({
  step: 1,
  description: '',
  generatedMd: '',
  generatedCode: '',
  sandboxResults: null,
  registeredId: null,
  isGenerating: false,
  error: null,

  setDescription: (text) => set({ description: text }),

  setGeneratedMd: (md) => set({ generatedMd: md }),

  setStep: (step) => set({ step }),

  reset: () =>
    set({
      step: 1,
      description: '',
      generatedMd: '',
      generatedCode: '',
      sandboxResults: null,
      registeredId: null,
      error: null,
    }),

  generateSpec: async () => {
    const { description } = get()
    if (!description.trim()) return

    set({ isGenerating: true, error: null })
    try {
      const result = await agentApi.generateSpec(description)
      set({ generatedMd: result.spec_md, step: 2, isGenerating: false })
    } catch (e) {
      set({ error: String(e), isGenerating: false })
    }
  },

  generateCode: async () => {
    const { generatedMd } = get()
    if (!generatedMd.trim()) return

    set({ isGenerating: true, error: null })
    try {
      const result = await agentApi.generateCode(generatedMd)
      set({
        generatedCode: result.code,
        sandboxResults: result.sandbox_results,
        step: 3,
        isGenerating: false,
      })
    } catch (e) {
      set({ error: String(e), isGenerating: false })
    }
  },

  registerAgent: async () => {
    const { generatedMd, generatedCode } = get()
    if (!generatedMd.trim()) return

    set({ isGenerating: true, error: null })
    try {
      const result = await agentApi.register(generatedMd, generatedCode)
      set({
        registeredId: result.agent_id,
        step: 4,
        isGenerating: false,
      })
      // 刷新左侧 Agent 空间列表
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchAgentsFromApi()
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },
}))

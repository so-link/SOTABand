import { create } from 'zustand'
import { agentApi } from '@/services/api/agent'
import { apiApi } from '@/services/api/api'
import { toolApi } from '@/services/api/tool'

export type EditorStep = 1 | 2 | 3 | 4

export interface AutocompleteItem {
  name: string
  id: string
}

interface AgentEditorState {
  step: EditorStep
  description: string
  generatedMd: string
  generatedCode: string
  sandboxResults: Record<string, unknown> | null
  registeredId: string | null
  isGenerating: boolean
  error: string | null

  // Autocomplete data (lazy-loaded)
  apiItems: AutocompleteItem[]
  toolItems: AutocompleteItem[]

  setDescription: (text: string) => void
  setGeneratedMd: (md: string) => void
  generateSpec: () => Promise<void>
  generateCode: () => Promise<void>
  registerAgent: () => Promise<void>
  setStep: (step: EditorStep) => void
  reset: () => void

  // Lazy fetch autocomplete items
  fetchApis: () => Promise<void>
  fetchTools: () => Promise<void>
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
  apiItems: [],
  toolItems: [],

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
      apiItems: [],
      toolItems: [],
    }),

  fetchApis: async () => {
    // Only fetch once
    if (get().apiItems.length > 0) return
    try {
      const result = await apiApi.list()
      const apis = (result.apis || []).map((a: Record<string, unknown>) => ({
        name: (a.name as string) || (a.id as string),
        id: (a.id as string) || '',
      }))
      set({ apiItems: apis })
    } catch {
      // Silently fail — autocomplete will just show nothing
    }
  },

  fetchTools: async () => {
    // Only fetch once
    if (get().toolItems.length > 0) return
    try {
      const result = await toolApi.list()
      const tools = ((result as Record<string, unknown>).tools as Array<Record<string, unknown>> || []).map(
        (t: Record<string, unknown>) => ({
          name: (t.name as string) || (t.id as string),
          id: (t.id as string) || '',
        })
      )
      set({ toolItems: tools })
    } catch {
      // Silently fail
    }
  },

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
      const result = await agentApi.register(generatedMd, generatedCode, get().description)
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

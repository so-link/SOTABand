import { create } from 'zustand'
import { toolApi } from '@/services/api/tool'

export type EditorStep = 1 | 2 | 3 | 4

interface ToolEditorState {
  step: EditorStep
  description: string
  generatedMd: string
  generatedCode: string
  testData: Record<string, unknown> | null
  sandboxResults: Record<string, unknown> | null
  registeredId: string | null
  isGenerating: boolean
  isTesting: boolean
  error: string | null

  setDescription: (text: string) => void
  setGeneratedMd: (md: string) => void
  generateSpec: () => Promise<void>
  generateCode: () => Promise<void>
  runTests: () => Promise<void>
  registerTool: () => Promise<void>
  setStep: (step: EditorStep) => void
  prefill: (text: string) => void
  reset: () => void
}

export const useToolEditorStore = create<ToolEditorState>((set, get) => ({
  step: 1, description: '', generatedMd: '', generatedCode: '',
  testData: null, sandboxResults: null, registeredId: null,
  isGenerating: false, isTesting: false, error: null,

  setDescription: (text) => set({ description: text }),
  setGeneratedMd: (md) => set({ generatedMd: md }),
  setStep: (step) => set({ step }),

  /** 从外部预填描述（如对话跳转过来） */
  prefill: (text: string) => set({ step: 1, description: text, generatedMd: '', generatedCode: '', testData: null, sandboxResults: null, registeredId: null, error: null }),

  reset: () => set({
    step: 1, description: '', generatedMd: '', generatedCode: '',
    testData: null, sandboxResults: null, registeredId: null,
    isGenerating: false, isTesting: false, error: null,
  }),

  generateSpec: async () => {
    const { description } = get()
    if (!description.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.generateSpec(description)
      set({ generatedMd: result.spec_md, step: 2, isGenerating: false })
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },

  generateCode: async () => {
    const { generatedMd } = get()
    if (!generatedMd.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.generateCode(generatedMd)
      set({
        generatedCode: result.code, testData: result.test_data,
        sandboxResults: null, step: 3, isGenerating: false,
      })
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },

  runTests: async () => {
    const { generatedMd, generatedCode } = get()
    if (!generatedCode.trim()) return
    set({ isTesting: true, error: null, sandboxResults: null })
    try {
      const result = await toolApi.testCode(generatedMd, generatedCode)
      set({ sandboxResults: result.sandbox_results, isTesting: false })
    } catch (e) { set({ error: String(e), isTesting: false }) }
  },

  registerTool: async () => {
    const { generatedMd, generatedCode, testData } = get()
    if (!generatedMd.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.register(generatedMd, generatedCode, testData || {})
      set({ registeredId: result.tool_id, step: 4, isGenerating: false })
      // 刷新左侧工具空间列表
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchToolsFromApi()
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },
}))

import { create } from 'zustand'
import { toolApi } from '@/services/api/tool'

export type EditorStep = 1 | 2 | 3 | 4

export interface DebugRound {
  round: number
  stdout: string
  stderr: string
  success: boolean
  analysis: string
  code?: string
}

interface ToolEditorState {
  step: EditorStep
  description: string
  referenceCode: string
  generatedMd: string
  generatedCode: string
  params: Array<{name: string; type: string; required: boolean; default?: string | null; desc: string}>
  testInputs: Record<string, string>
  testOutput: { stdout: string; stderr: string; exit_code: number; success: boolean } | null
  registeredId: string | null
  isGenerating: boolean
  isTesting: boolean
  isAutoDebugging: boolean
  abortController: AbortController | null
  testAbortController: AbortController | null
  error: string | null
  debugRounds: DebugRound[]
  debugStream: string

  setDescription: (text: string) => void
  setReferenceCode: (code: string) => void
  setGeneratedMd: (md: string) => void
  setTestInput: (key: string, value: string) => void
  generateSpec: () => Promise<void>
  generateCode: () => Promise<void>
  runTest: (files?: File[]) => Promise<void>
  stopTest: () => void
  autoDebug: (files?: File[]) => Promise<void>
  stopAutoDebug: () => void
  registerTool: () => Promise<void>
  setStep: (step: EditorStep) => void
  prefill: (text: string, referenceCode?: string) => void
  reset: () => void
}

export const useToolEditorStore = create<ToolEditorState>((set, get) => ({
  step: 1, description: '', referenceCode: '', generatedMd: '', generatedCode: '',
  params: [], testInputs: {}, testOutput: null, registeredId: null,
  isGenerating: false, isTesting: false, isAutoDebugging: false, abortController: null, testAbortController: null, error: null,
  debugRounds: [], debugStream: '',

  setDescription: (text) => set({ description: text }),
  setReferenceCode: (code) => set({ referenceCode: code }),
  setGeneratedMd: (md) => set({ generatedMd: md }),
  setStep: (step) => set({ step }),
  setTestInput: (key, value) => set((s) => ({ testInputs: { ...s.testInputs, [key]: value } })),

  prefill: (text: string, refCode?: string) => set({
    step: 1, description: text, referenceCode: refCode || '', generatedMd: '', generatedCode: '',
    testInputs: {}, testOutput: null, registeredId: null, error: null,
    debugRounds: [], debugStream: '', params: [],
  }),

  reset: () => set({
    step: 1, description: '', referenceCode: '', generatedMd: '', generatedCode: '',
    testInputs: {}, testOutput: null, registeredId: null,
    isGenerating: false, isTesting: false, isAutoDebugging: false,
    error: null, debugRounds: [], debugStream: '', params: [],
  }),

  generateSpec: async () => {
    const { description, referenceCode } = get()
    if (!description.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.generateSpec(description, referenceCode)
      set({ generatedMd: result.spec_md, step: 2, isGenerating: false })
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },

  generateCode: async () => {
    const { generatedMd } = get()
    if (!generatedMd.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.generateCode(generatedMd)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const params: any[] = result.params || []
      // 初始化测试输入
      const inputs: Record<string, string> = {}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      params.forEach((p: any) => { inputs[p.name] = p.default || '' })
      set({
        generatedCode: result.code, params, testInputs: inputs,
        testOutput: null, step: 3, isGenerating: false,
        debugRounds: [], debugStream: '',
      })
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },

  runTest: async (files?: File[]) => {
    const { generatedMd, generatedCode, testInputs } = get()
    if (!generatedCode.trim()) return
    const controller = new AbortController()
    set({ isTesting: true, testAbortController: controller, error: null, testOutput: null })
    try {
      const result = await toolApi.testWithInput(generatedMd, generatedCode, testInputs, files, controller.signal)
      set({ testOutput: result, isTesting: false, testAbortController: null })
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        set({ isTesting: false, testAbortController: null })
      } else {
        set({ error: String(e), isTesting: false, testAbortController: null })
      }
    }
  },

  stopTest: () => {
    const { testAbortController } = get()
    if (testAbortController) testAbortController.abort()
    set({ isTesting: false, testAbortController: null })
  },

  autoDebug: async (files?: File[]) => {
    const { generatedMd, generatedCode, testInputs } = get()
    if (!generatedCode.trim()) return

    const controller = new AbortController()
    set({ isAutoDebugging: true, abortController: controller, error: null, debugRounds: [], debugStream: '' })

    try {
      await toolApi.autoDebug(generatedMd, generatedCode, testInputs, files, (eventType, data) => {
        switch (eventType) {
          case 'round_start':
            set((s) => ({ debugRounds: [...s.debugRounds, { round: Number(data.round), stdout: '', stderr: '', success: false, analysis: '' }] }))
            break
          case 'exec_result':
            set((s) => {
              const rounds = [...s.debugRounds]
              const last = rounds[rounds.length - 1]
              if (last) { last.stdout = String(data.stdout || ''); last.stderr = String(data.stderr || ''); last.success = Boolean(data.success) }
              return { debugRounds: rounds, testOutput: { stdout: String(data.stdout || ''), stderr: String(data.stderr || ''), exit_code: 0, success: Boolean(data.success) } }
            })
            break
          case 'thinking':
            set((s) => {
              const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
              if (last) last.analysis = 'LLM 分析中...'
              return { debugRounds: rounds }
            })
            break
          case 'thinking_stream':
            set((s) => {
              const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
              if (last) last.analysis = (last.analysis || '') + (data.token || '')
              return { debugRounds: rounds, debugStream: s.debugStream + (data.token || '') }
            })
            break
          case 'missing_dep':
            set((s) => {
              const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
              if (last) last.analysis = `安装依赖: ${data.module}`
              return { debugRounds: rounds }
            })
            break
          case 'dep_installed':
            set((s) => {
              const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
              if (last) last.analysis = `依赖已安装: ${data.module}`
              return { debugRounds: rounds }
            })
            break
          case 'code_updated':
            set({ generatedCode: String(data.code || '') })
            set((s) => { const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]; if (last) last.code = String(data.code || ''); return { debugRounds: rounds } })
            break
          case 'done':
          case 'stopped':
            if (data.code) set({ generatedCode: String(data.code) })
            set({ isAutoDebugging: false, abortController: null })
            break
        }
      })
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        set({ isAutoDebugging: false, abortController: null })
      } else {
        set({ error: String(e), isAutoDebugging: false, abortController: null })
      }
    }
  },

  stopAutoDebug: () => {
    const { abortController } = get()
    if (abortController) abortController.abort()
    set({ isAutoDebugging: false, abortController: null })
  },

  registerTool: async () => {
    const { generatedMd, generatedCode, testInputs, referenceCode } = get()
    if (!generatedMd.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.register(generatedMd, generatedCode, testInputs, get().description, referenceCode)
      set({ registeredId: result.tool_id, step: 4, isGenerating: false })
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchToolsFromApi()
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },
}))

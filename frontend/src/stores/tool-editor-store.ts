import { create } from 'zustand'
import { toolApi } from '@/services/api/tool'

export type EditorStep = 1 | 2 | 3 | 4

// 保存策略：手动保存（点击保存按钮或 Ctrl/Cmd+S）。
// 不做自动保存，是因为工具代码改动会直接影响已发布的工具，
// 应让使用者明确确认后再落盘，并提供可回退的时机。

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
  tags: string[]
  params: Array<{name: string; type: string; required: boolean; default?: string | null; desc: string}>
  testInputs: Record<string, string>
  testOutput: { stdout: string; stderr: string; exit_code: number; success: boolean } | null
  registeredId: string | null
  // ── 编辑已有工具模式 ──
  // editingToolId 非空表示当前是在编辑一个已发布的工具（而非新建），
  // 注册时应覆盖该工具，而不是创建新条目。
  editingToolId: string | null
  editingToolName: string
  // 进入编辑时快照的原始代码，用于「同步代码改动到文档」时做 diff
  baselineCode: string
  // 进入编辑时快照的原始 MD
  baselineMd: string
  // ── 手动保存 ──
  // 'idle' 无改动 | 'dirty' 有改动未保存 | 'saving' 保存中
  // | 'saved' 已保存 | 'error' 保存失败
  saveState: 'idle' | 'dirty' | 'saving' | 'saved' | 'error'
  saveError: string | null
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
  setTags: (tags: string[]) => void
  addTag: (tag: string) => void
  removeTag: (tag: string) => void
  setTestInput: (key: string, value: string) => void
  generateSpec: () => Promise<void>
  generateCode: () => Promise<void>
  runTest: (files?: File[]) => Promise<void>
  stopTest: () => void
  autoDebug: (files?: File[]) => Promise<void>
  stopAutoDebug: () => void
  registerTool: () => Promise<void>
  setStep: (step: EditorStep) => void
  setGeneratedCode: (code: string) => void
  prefill: (text: string, referenceCode?: string) => void
  /** 依据手工微调后的代码，让 AI 反向更新 MD 规范文档 */
  syncSpecFromCode: () => Promise<void>
  /** 后台预生成人话摘要（不阻塞界面），结果由后端缓存 */
  prefetchSummaries: () => Promise<void>
  /** 编辑内容变更后调用：仅标记「有未保存改动」，不自动落盘 */
  notifyEdit: () => void
  /** 保存当前 MD 与代码（点击保存按钮 / Ctrl+S 时调用） */
  flushSave: () => Promise<void>
  /** 当前是否存在未保存的改动 */
  hasUnsavedChanges: () => boolean
  /** 编辑一个已发布的工具：带齐现有 MD/代码，从「审阅」步（step 2）开始 */
  prefillFromTool: (opts: {
    toolId: string
    toolName: string
    description: string
    specMd: string
    code: string
    tags?: string[]
    params?: Array<{name: string; type: string; required: boolean; default?: string | null; desc: string}>
  }) => void
  reset: () => void
}

export const useToolEditorStore = create<ToolEditorState>((set, get) => ({
  step: 1, description: '', referenceCode: '', generatedMd: '', generatedCode: '', tags: [],
  params: [], testInputs: {}, testOutput: null, registeredId: null,
  isGenerating: false, isTesting: false, isAutoDebugging: false, abortController: null, testAbortController: null, error: null,
  debugRounds: [], debugStream: '',
  editingToolId: null, editingToolName: '', baselineCode: '', baselineMd: '',
  saveState: 'idle', saveError: null,

  setDescription: (text) => set({ description: text }),
  setReferenceCode: (code) => set({ referenceCode: code }),
  setGeneratedMd: (md) => set({ generatedMd: md }),
  setTags: (tags) => set({ tags }),
  addTag: (tag) => set((s) => ({ tags: s.tags.includes(tag) ? s.tags : [...s.tags, tag] })),
  removeTag: (tag) => set((s) => ({ tags: s.tags.filter(t => t !== tag) })),
  setStep: (step) => set({ step }),
  setTestInput: (key, value) => set((s) => ({ testInputs: { ...s.testInputs, [key]: value } })),
  setGeneratedCode: (code) => set({ generatedCode: code }),

  // 手工微调代码后，让 AI 依据代码改动反向更新 MD 规范文档，
  // 实现「文档 → 代码 → 文档」的双向闭环。
  syncSpecFromCode: async () => {
    const { editingToolId, generatedCode, baselineCode, generatedMd } = get()
    if (!editingToolId) {
      set({ error: '仅支持编辑已有工具时同步；请先注册该工具' })
      return
    }
    if (generatedCode.trim() === baselineCode.trim()) {
      set({ error: '代码尚未修改，无需同步' })
      return
    }
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.syncSpecFromCode({
        tool_id: editingToolId,
        code: generatedCode,
        original_code: baselineCode,
        current_spec: generatedMd,
      })
      set((s) => ({
        generatedMd: result.spec_md || s.generatedMd,
        baselineCode: s.generatedCode, // 同步后重置基线，避免重复同步
        isGenerating: false,
      }))
    } catch (e) {
      set({ error: String(e), isGenerating: false })
    }
  },

  // ── 手动保存 ──
  // 编辑内容变更后只标记「有未保存改动」，不自动落盘；
  // 由使用者点击保存按钮或按 Ctrl/Cmd+S 显式保存。
  notifyEdit: () => {
    const s = get()
    if (!s.editingToolId) return
    const dirty = s.generatedMd !== s.baselineMd || s.generatedCode !== s.baselineCode
    set({ saveState: dirty ? 'dirty' : 'idle' })
  },

  flushSave: async () => {
    const s = get()
    if (!s.editingToolId) return

    const mdChanged = s.generatedMd !== s.baselineMd
    const codeChanged = s.generatedCode !== s.baselineCode
    if (!mdChanged && !codeChanged) {
      set({ saveState: 'idle' })
      return
    }

    set({ saveState: 'saving', saveError: null })

    const toolId = s.editingToolId
    const md = s.generatedMd
    const code = s.generatedCode
    const errs: string[] = []

    try {
      if (mdChanged && md.trim()) await toolApi.saveSpec(toolId, md)
    } catch (e) { errs.push(`文档: ${String(e)}`) }
    try {
      if (codeChanged && code.trim()) await toolApi.saveCode(toolId, code)
    } catch (e) { errs.push(`代码: ${String(e)}`) }

    if (errs.length) {
      set({ saveState: 'error', saveError: errs.join('；') })
      return
    }
    // 保存成功后推进基线，使「未保存」标记消失
    set((cur) => ({
      baselineMd: mdChanged ? md : cur.baselineMd,
      baselineCode: codeChanged ? code : cur.baselineCode,
      saveState: 'saved',
      saveError: null,
    }))
  },

  hasUnsavedChanges: () => {
    const s = get()
    return Boolean(s.editingToolId) &&
      (s.generatedMd !== s.baselineMd || s.generatedCode !== s.baselineCode)
  },

  prefill: (text: string, refCode?: string) => set({
    step: 1, description: text, referenceCode: refCode || '', generatedMd: '', generatedCode: '', tags: [],
    testInputs: {}, testOutput: null, registeredId: null, error: null,
    debugRounds: [], debugStream: '', params: [],
    editingToolId: null, editingToolName: '', baselineCode: '', baselineMd: '',
  saveState: 'idle', saveError: null,
  }),

  // 编辑已发布工具：保留现有 MD 与代码，直接从 step 2（审阅）开始，
  // 避免每次微调都被迫从「重新描述需求」走一遍。
  prefillFromTool: ({ toolId, toolName, description, specMd, code, tags = [], params = [] }) => {
    const inputs: Record<string, string> = {}
    params.forEach((p) => { inputs[p.name] = p.default || '' })
    set({
      step: 2,
      editingToolId: toolId,
      editingToolName: toolName,
      description: description || toolName,
      referenceCode: '',
      generatedMd: specMd,
      generatedCode: code,
      baselineCode: code,
      baselineMd: specMd,
      tags,
      params,
      testInputs: inputs,
      testOutput: null,
      registeredId: toolId,
      error: null,
      debugRounds: [],
      debugStream: '',
      isGenerating: false,
      isTesting: false,
      isAutoDebugging: false,
    })
  },

  reset: () => set({
    step: 1, description: '', referenceCode: '', generatedMd: '', generatedCode: '', tags: [],
    testInputs: {}, testOutput: null, registeredId: null,
    isGenerating: false, isTesting: false, isAutoDebugging: false,
    error: null, debugRounds: [], debugStream: '', params: [],
    editingToolId: null, editingToolName: '', baselineCode: '', baselineMd: '',
  saveState: 'idle', saveError: null,
  }),

  generateSpec: async () => {
    const { description, referenceCode } = get()
    if (!description.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.generateSpec(description, referenceCode)
      set({ generatedMd: result.spec_md, tags: result.tags || [], step: 2, isGenerating: false })
      // 文档生成后，后台预生成「人话摘要」。
      // 摘要需调用推理模型，实测约 30 秒——不能并入主流程（会让生成文档
      // 从 27 秒变成 57 秒，产生卡死感）。因此这里后台静默发起，
      // 结果由后端按文档内容缓存；使用者点开概览时通常已就绪。
      void get().prefetchSummaries()
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },

  // 后台预生成摘要：不设置 isGenerating，避免阻塞界面交互
  prefetchSummaries: async () => {
    const { generatedMd, editingToolId } = get()
    if (!generatedMd.trim()) return
    try {
      await toolApi.getSpecOutline(editingToolId || '_draft_', generatedMd, true)
    } catch {
      // 预生成失败无需打扰使用者：点开概览时可手动重试
    }
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
          case 'debug_start':
            set({ debugStream: `🚀 自动调试启动 (最多 ${data.max_rounds} 轮)\n` })
            break
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
          case 'deps_start':
            set((s) => ({
              debugStream: s.debugStream + `\n📦 检测到依赖: ${(data.deps as string[]).join(', ')} (${data.env})\n`,
            }))
            break
          case 'dep_installing':
            set((s) => ({
              debugStream: s.debugStream + `\n⏳ 安装 ${data.dep} (${data.env})...\n`,
            }))
            break
          case 'pip_output':
            set((s) => ({
              debugStream: s.debugStream + `  ${data.line}\n`,
            }))
            break
          case 'pip_analyzing':
            set((s) => ({
              debugStream: s.debugStream + `🤔 安装失败，LLM 分析原因中... (第${data.attempt}次重试)\n`,
            }))
            break
          case 'pip_analysis':
            set((s) => ({
              debugStream: s.debugStream + `💡 ${(data.analysis as Record<string,string>)?.reason || '未知'}: ${(data.analysis as Record<string,string>)?.suggestion || ''}\n`,
            }))
            break
          case 'dep_already':
            set((s) => ({
              debugStream: s.debugStream + `✅ ${data.dep} 已安装，跳过 (${data.env})\n`,
            }))
            break
          case 'dep_installed':
            set((s) => ({
              debugStream: s.debugStream + `✅ ${data.dep} 安装完成 (${data.env})\n`,
            }))
            break
          case 'dep_failed':
            set((s) => ({
              debugStream: s.debugStream + `❌ ${data.dep} 安装失败: ${data.reason || ''}\n`,
            }))
            break
          case 'env_switch':
            set((s) => ({
              debugStream: s.debugStream + `⚠️ 全局安装 ${data.dep} 失败，切换到本地环境\n`,
            }))
            break
          case 'deps_done':
            set((s) => ({
              debugStream: s.debugStream + `✅ 依赖安装完成\n`,
            }))
            break
          case 'missing_dep':
            set((s) => {
              const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
              if (last) last.analysis = `安装依赖: ${data.module}`
              return { debugRounds: rounds, debugStream: s.debugStream + `📦 安装缺失依赖: ${data.module}\n` }
            })
            break
          case 'dep_installed':
            set((s) => {
              const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
              if (last) last.analysis = `依赖已安装: ${data.module}`
              return { debugRounds: rounds, debugStream: s.debugStream + `✅ 依赖已安装: ${data.module}\n` }
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
      // 异常退出时也调用 stop 端点，确保后台停止
      if ((e as Error).name === 'AbortError') {
        set({ isAutoDebugging: false, abortController: null })
      } else {
        set({ error: String(e), isAutoDebugging: false, abortController: null })
      }
      // 调用后端 stop 端点确保后台也停止
      if (generatedMd) {
        toolApi.stopAutoDebug(generatedMd).catch(() => {})
      }
    }
  },

  stopAutoDebug: () => {
    const { abortController, generatedMd } = get()
    if (abortController) abortController.abort()
    // 同时调用后端 stop 端点（即使 SSE 连接已断开也能可靠停止）
    if (generatedMd) {
      toolApi.stopAutoDebug(generatedMd).catch(() => {})
    }
    set({ isAutoDebugging: false, abortController: null })
  },

  registerTool: async () => {
    const { generatedMd, generatedCode, testInputs, referenceCode, tags } = get()
    if (!generatedMd.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const result = await toolApi.register(generatedMd, generatedCode, testInputs, get().description, referenceCode, tags)
      set({ registeredId: result.tool_id, step: 4, isGenerating: false })
      // 刷新资源列表
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchToolsFromApi()
      // 自动加入工具空间
      const { useWorkspaceToolStore } = await import('@/stores/workspace-tool-store')
      const toolName = generatedMd.match(/^#\s*(.+)$/m)?.[1] || result.tool_id
      useWorkspaceToolStore.getState().addTool({
        id: result.tool_id,
        name: toolName,
        tags: tags,
        loadedAt: new Date().toISOString(),
      })
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },
}))

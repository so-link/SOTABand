import { create } from 'zustand'
import { agentApi } from '@/services/api/agent'
import { apiApi } from '@/services/api/api'
import { toolApi } from '@/services/api/tool'

export type EditorStep = 1 | 2 | 3 | 4

export interface AutocompleteItem {
  name: string
  id: string
}

/** Agent 类型（三类体系，见 docs/agent-types-design.md） */
export type AgentRoleChoice = 'interactive' | 'task' | 'web-service'

export const AGENT_ROLE_OPTIONS: Array<{ value: AgentRoleChoice; label: string; desc: string }> = [
  { value: 'interactive', label: '交互式', desc: '通过对话完成任务，理解用户意图并调用合适的工具' },
  { value: 'task', label: '任务式', desc: '按既定流程依次调用工具完成任务' },
  { value: 'web-service', label: 'Web 服务式', desc: '以 HTTP API 形式对外提供服务，组合调用工具' },
]

/** 三类 Agent 的默认需求模板（用户可在此基础上编辑） */
export const AGENT_ROLE_TEMPLATES: Record<AgentRoleChoice, string> = {
  interactive:
`我需要一个交互式 Agent，用于【描述你的业务场景】。

- 功能：接收用户输入，理解用户意图，并调用合适的工具完成任务。
- 可用工具：$工具名（按需引用）
- 交互方式：与用户多轮对话，逐步澄清需求后执行。
- 输出：以自然语言回复，并展示工具执行结果。`,

  task:
`我需要一个任务式 Agent，用于【描述你的业务流程】。

- 功能：按既定流程依次调用工具完成任务。
- 触发方式：用户点击「启动」后执行。
- 执行流程：
    步骤 1：调用 $工具名1，参数 ...
    步骤 2：调用 $工具名2，使用步骤 1 的输出 ...
    步骤 3：...
- 启动参数：【列出启动时需要的参数】
- 输出：最终结果输出到消息界面。`,

  'web-service':
`我需要一个 Web 服务式 Agent，用于【描述你的服务场景】。

- 功能：以 HTTP API 形式对外提供服务。
- 提供服务（可多个）：
    API 1：POST /xxx
        参数：{ 参数名: 类型 }
        流程：调用 $工具名 ...
        返回：{ 返回字段 }
    API 2：...
- 监听端口：自动分配
- 输出：JSON 响应。`,
}

interface AgentEditorState {
  step: EditorStep
  /** 新建 Agent 时选择的类型（null 表示尚未选择，需先选类型再填需求） */
  agentRole: AgentRoleChoice | null
  description: string
  generatedMd: string
  generatedCode: string
  sandboxResults: Record<string, unknown> | null
  registeredId: string | null
  /** 上次注册是否命中了已有 Agent（原地更新，而非新增一条） */
  lastRegisterUpdated: boolean
  /** 上次注册后的版本号，用于完成页展示 */
  lastRegisterVersion: string
  isGenerating: boolean
  error: string | null
  // ── 编辑已有 Agent 模式 ──
  // editingAgentId 非空表示当前在编辑已发布的 Agent（而非新建），
  // 注册时应覆盖该 Agent，而不是创建新条目。
  editingAgentId: string | null
  editingAgentName: string
  // 进入编辑时的代码快照，用于「同步代码改动到文档」时做 diff
  baselineCode: string
  // 进入编辑时的 MD 快照
  baselineMd: string
  // 手动保存状态：'idle' 无改动 | 'dirty' 有改动未保存
  // | 'saving' 保存中 | 'saved' 已保存 | 'error' 失败
  saveState: 'idle' | 'dirty' | 'saving' | 'saved' | 'error'
  saveError: string | null

  // Autocomplete data (lazy-loaded)
  apiItems: AutocompleteItem[]
  toolItems: AutocompleteItem[]

  setDescription: (text: string) => void
  /** 选择 Agent 类型；若需求框为空则自动填入该类型默认模板 */
  setAgentRole: (role: AgentRoleChoice) => void
  setGeneratedMd: (md: string) => void
  setGeneratedCode: (code: string) => void
  generateSpec: () => Promise<void>
  generateCode: () => Promise<void>
  registerAgent: () => Promise<void>
  setStep: (step: EditorStep) => void
  reset: () => void
  /** 编辑一个已发布的 Agent：带齐现有 MD/代码，从「审阅」步（step 2）开始 */
  prefillFromAgent: (opts: {
    agentId: string
    agentName: string
    description: string
    specMd: string
    code: string
  }) => void
  /** 依据手工微调后的代码，让 AI 反向更新 MD 规范文档 */
  syncSpecFromCode: () => Promise<void>
  /** 保存手工微调后的代码到该 Agent */
  saveCode: () => Promise<void>
  /** 编辑内容变更后调用：仅标记「有未保存改动」，不自动落盘 */
  notifyEdit: () => void
  /** 保存当前 MD 与代码（点击保存按钮 / Ctrl+S 时调用） */
  flushSave: () => Promise<void>
  /** 当前是否存在未保存的改动 */
  hasUnsavedChanges: () => boolean

  // Lazy fetch autocomplete items
  fetchApis: () => Promise<void>
  fetchTools: () => Promise<void>
}

export const useAgentEditorStore = create<AgentEditorState>((set, get) => ({
  step: 1,
  agentRole: null,
  description: '',
  generatedMd: '',
  generatedCode: '',
  sandboxResults: null,
  registeredId: null,
  lastRegisterUpdated: false,
  lastRegisterVersion: '',
  isGenerating: false,
  error: null,
  editingAgentId: null,
  editingAgentName: '',
  baselineCode: '',
  baselineMd: '',
  saveState: 'idle',
  saveError: null,
  apiItems: [],
  toolItems: [],

  setDescription: (text) => set({ description: text }),

  setAgentRole: (role) => {
    const { description } = get()
    // 需求框为空 → 自动填入该类型默认模板；已有内容 → 保留（由 UI 层询问是否覆盖）
    const next = description.trim() ? description : AGENT_ROLE_TEMPLATES[role]
    set({ agentRole: role, description: next })
  },

  setGeneratedMd: (md) => set({ generatedMd: md }),

  setGeneratedCode: (code) => set({ generatedCode: code }),

  setStep: (step) => set({ step }),

  reset: () =>
    set({
      step: 1,
      agentRole: null,
      description: '',
      generatedMd: '',
      generatedCode: '',
      sandboxResults: null,
      registeredId: null,
      lastRegisterUpdated: false,
      lastRegisterVersion: '',
      error: null,
      apiItems: [],
      toolItems: [],
      editingAgentId: null,
      editingAgentName: '',
      baselineCode: '',
    }),

  // 编辑已发布 Agent：保留现有 MD 与代码，直接从 step 2（审阅）开始，
  // 避免每次微调都被迫从「重新描述需求」走一遍。
  prefillFromAgent: ({ agentId, agentName, description, specMd, code }) =>
    set({
      step: 2,
      editingAgentId: agentId,
      editingAgentName: agentName,
      description: description || agentName,
      generatedMd: specMd,
      generatedCode: code,
      baselineCode: code,
      baselineMd: specMd,
      sandboxResults: null,
      registeredId: agentId,
      lastRegisterUpdated: false,
      lastRegisterVersion: '',
      error: null,
      isGenerating: false,
      saveState: 'idle',
      saveError: null,
    }),

  // 手工微调代码后，让 AI 依据代码改动反向更新 MD 规范文档，
  // 实现「文档 → 代码 → 文档」的双向闭环。
  syncSpecFromCode: async () => {
    const { editingAgentId, generatedCode, baselineCode, generatedMd } = get()
    if (!editingAgentId) {
      set({ error: '仅支持编辑已有 Agent 时同步；请先注册该 Agent' })
      return
    }
    if (generatedCode.trim() === baselineCode.trim()) {
      set({ error: '代码尚未修改，无需同步' })
      return
    }
    set({ isGenerating: true, error: null })
    try {
      const result = await agentApi.syncSpecFromCode({
        agent_id: editingAgentId,
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

  // 保存手工微调后的代码
  saveCode: async () => {
    const { editingAgentId, generatedCode, baselineCode } = get()
    if (!editingAgentId) {
      set({ error: '仅支持编辑已有 Agent 时保存；请先注册该 Agent' })
      return
    }
    if (!generatedCode.trim()) {
      set({ error: '代码不能为空' })
      return
    }
    if (generatedCode.trim() === baselineCode.trim()) {
      set({ error: '代码尚未修改' })
      return
    }
    set({ isGenerating: true, error: null })
    try {
      await agentApi.saveCode(editingAgentId, generatedCode)
      set((s) => ({ baselineCode: s.generatedCode, isGenerating: false }))
    } catch (e) {
      set({ error: String(e), isGenerating: false })
    }
  },

  // ── 手动保存 ──
  notifyEdit: () => {
    const s = get()
    if (!s.editingAgentId) return
    const dirty = s.generatedMd !== s.baselineMd || s.generatedCode !== s.baselineCode
    set({ saveState: dirty ? 'dirty' : 'idle' })
  },

  flushSave: async () => {
    const s = get()
    if (!s.editingAgentId) return

    const mdChanged = s.generatedMd !== s.baselineMd
    const codeChanged = s.generatedCode !== s.baselineCode
    if (!mdChanged && !codeChanged) {
      set({ saveState: 'idle' })
      return
    }

    set({ saveState: 'saving', saveError: null })
    const agentId = s.editingAgentId
    const md = s.generatedMd
    const code = s.generatedCode
    const errs: string[] = []

    try {
      if (mdChanged && md.trim()) await agentApi.saveSpec(agentId, md)
    } catch (e) { errs.push(`文档: ${String(e)}`) }
    try {
      if (codeChanged && code.trim()) await agentApi.saveCode(agentId, code)
    } catch (e) { errs.push(`代码: ${String(e)}`) }

    if (errs.length) {
      set({ saveState: 'error', saveError: errs.join('；') })
      return
    }
    set((cur) => ({
      baselineMd: mdChanged ? md : cur.baselineMd,
      baselineCode: codeChanged ? code : cur.baselineCode,
      saveState: 'saved',
      saveError: null,
    }))
  },

  hasUnsavedChanges: () => {
    const s = get()
    return Boolean(s.editingAgentId) &&
      (s.generatedMd !== s.baselineMd || s.generatedCode !== s.baselineCode)
  },

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
    const { description, agentRole } = get()
    if (!description.trim()) return

    set({ isGenerating: true, error: null })
    try {
      const result = await agentApi.generateSpec(description, agentRole || undefined)
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
    const { generatedMd, generatedCode, editingAgentId } = get()
    if (!generatedMd.trim()) return

    set({ isGenerating: true, error: null })
    try {
      // 编辑态必须把原 Agent id 作为权威身份传过去：MD 里的 id 可能已被
      // 「返回修改需求 → 重新生成文档」改掉，只按 MD 推导会新增重复 Agent。
      const result = await agentApi.register(
        generatedMd, generatedCode, get().description, editingAgentId || undefined,
      )
      set({
        registeredId: result.agent_id,
        step: 4,
        isGenerating: false,
        lastRegisterUpdated: Boolean(result.updated),
        lastRegisterVersion: result.version || '',
      })
      // 刷新左侧 Agent 空间列表
      const { useResourceStore } = await import('@/stores/resource-store')
      await useResourceStore.getState().fetchAgentsFromApi()
      // 编辑态：刷新后按同一 id 重新选中，让详情页重取 MD/代码与新版本徽标
      if (editingAgentId) {
        const fresh = useResourceStore.getState().agentResources.find(a => a.id === result.agent_id)
        if (fresh) useResourceStore.getState().selectResource(fresh)
      }
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },
}))

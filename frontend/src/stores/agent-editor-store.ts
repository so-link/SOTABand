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

export interface AgentDebugRound {
  round: number
  stdout: string
  stderr: string
  success: boolean
  analysis: string
  code?: string
}

/** 从 MD frontmatter 解析 id/name/role（与 agent.ts 的解析规则一致） */
export function parseAgentMeta(specMd: string): { agentId: string; agentName: string; role: string } {
  return {
    agentId: specMd.match(/^id:\s*(.+)$/m)?.[1]?.trim() || 'custom-agent',
    agentName: specMd.match(/^name:\s*(.+)$/m)?.[1]?.trim() || 'Custom Agent',
    role: specMd.match(/^role:\s*(.+)$/m)?.[1]?.trim() || 'task',
  }
}

export interface TestParamDef {
  name: string
  type: string
  required: boolean
  desc: string
}

/** 从 MD 规范解析测试参数定义：
 * - task/interactive：「输入规范」表格行 `| 字段 | 类型 | 必填 | 说明 |`
 * - web-service：```apis 声明块中各 API 的 params（跨 API 按名称去重合并）
 */
export function parseTestParamDefs(specMd: string, role: string): TestParamDef[] {
  const defs: TestParamDef[] = []
  const seen = new Set<string>()
  const push = (d: TestParamDef) => {
    if (d.name && !seen.has(d.name)) { seen.add(d.name); defs.push(d) }
  }
  if (role === 'web-service') {
    const m = specMd.match(/```apis\s*\n([\s\S]*?)```/)
    if (m) {
      try {
        const apis = JSON.parse(m[1])
        if (Array.isArray(apis)) {
          for (const api of apis) {
            for (const p of api?.params || []) {
              if (p && typeof p.name === 'string') {
                push({ name: p.name, type: p.type || 'string', required: Boolean(p.required), desc: p.desc || '' })
              }
            }
          }
        }
      } catch { /* apis JSON 非法时回退到输入规范表 */ }
    }
  }
  // 「输入规范」表格（三类通用兜底；web-service 无 apis 声明时也走这里）
  const sec = specMd.match(/#+\s*[^\n]*输入规范[^\n]*\n([\s\S]*?)(?=\n#+\s|\s*$)/)
  if (sec) {
    for (const line of sec[1].split('\n')) {
      const row = line.trim()
      if (!row.startsWith('|')) continue
      const cells = row.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
      if (cells.length < 3) continue
      const [name, type, req, desc] = cells
      if (!name || name === '字段' || /^-+$/.test(name)) continue
      push({ name, type: type || 'string', required: req === '是' || req.toLowerCase() === 'yes', desc: desc || '' })
    }
  }
  return defs
}

/** 按参数定义初始化测试输入（空值占位，用户填写后保留） */
export function initTestInputs(specMd: string): Record<string, string> {
  const { role } = parseAgentMeta(specMd)
  const inputs: Record<string, string> = {}
  for (const d of parseTestParamDefs(specMd, role)) {
    if (!(d.name in inputs)) inputs[d.name] = ''
  }
  return inputs
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
  isGenerating: boolean
  // 生成过程中的模型思考过程（reasoning 事件累加），用于前端展示
  reasoningStream: string
  // 是否正在提交注册（与"生成中"区分，避免按钮文案混淆）
  isRegistering: boolean
  // 同步代码→MD 文档中（与生成区分，避免按钮误显示"生成中"）
  isSyncing: boolean
  // 保存代码中
  isSaving: boolean
  // 生成规范/代码的中止控制器（点"停止生成"时 abort fetch）
  generationAbort: AbortController | null
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

  // ── 自动调试（todo 4.3）──
  testInputs: Record<string, string>
  debugRounds: AgentDebugRound[]
  debugStream: string
  isAutoDebugging: boolean
  abortController: AbortController | null

  // Autocomplete data (lazy-loaded)
  apiItems: AutocompleteItem[]
  toolItems: AutocompleteItem[]

  setDescription: (text: string) => void
  setTestInput: (key: string, value: string) => void
  /** 选择 Agent 类型；若需求框为空则自动填入该类型默认模板 */
  setAgentRole: (role: AgentRoleChoice) => void
  setGeneratedMd: (md: string) => void
  setGeneratedCode: (code: string) => void
  generateSpec: () => Promise<void>
  generateCode: () => Promise<void>
  stopGeneration: () => void
  autoDebug: () => Promise<void>
  stopAutoDebug: () => void
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
  isGenerating: false,
  reasoningStream: '',
  isRegistering: false,
  isSyncing: false,
  isSaving: false,
  generationAbort: null,
  error: null,
  editingAgentId: null,
  editingAgentName: '',
  baselineCode: '',
  baselineMd: '',
  saveState: 'idle',
  saveError: null,
  testInputs: {},
  debugRounds: [],
  debugStream: '',
  isAutoDebugging: false,
  abortController: null,
  apiItems: [],
  toolItems: [],

  setDescription: (text) => set({ description: text }),

  setTestInput: (key, value) => set((s) => ({ testInputs: { ...s.testInputs, [key]: value } })),

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
      isGenerating: false,
      reasoningStream: '',
      isRegistering: false,
      isSyncing: false,
      isSaving: false,
      generationAbort: null,
      error: null,
      apiItems: [],
      toolItems: [],
      editingAgentId: null,
      editingAgentName: '',
      baselineCode: '',
      testInputs: {},
      debugRounds: [],
      debugStream: '',
      isAutoDebugging: false,
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
      error: null,
      isGenerating: false,
      reasoningStream: '',
      isRegistering: false,
      isSyncing: false,
      isSaving: false,
      generationAbort: null,
      saveState: 'idle',
      saveError: null,
      testInputs: initTestInputs(specMd),
      debugRounds: [],
      debugStream: '',
      isAutoDebugging: false,
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
    set({ isSyncing: true, error: null })
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
        isSyncing: false,
      }))
    } catch (e) {
      set({ error: String(e), isSyncing: false })
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
    set({ isSaving: true, saveState: 'saving', error: null })
    try {
      await agentApi.saveCode(editingAgentId, generatedCode)
      set((s) => ({ baselineCode: s.generatedCode, isSaving: false, saveState: 'saved' }))
    } catch (e) {
      set({ error: String(e), isSaving: false, saveState: 'error', saveError: String(e) })
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

    const abort = new AbortController()
    set({ isGenerating: true, error: null, generatedMd: '', reasoningStream: '', step: 2, generationAbort: abort })
    try {
      const result = await agentApi.generateSpec(
        description,
        agentRole || undefined,
        (token) => set((s) => ({ generatedMd: s.generatedMd + token })),
        (token) => set((s) => ({ reasoningStream: s.reasoningStream + token })),
        abort.signal,
      )
      set({ generatedMd: result.spec_md, isGenerating: false, generationAbort: null })
    } catch (e) {
      // abort 不展示错误
      if (abort.signal.aborted) {
        set({ isGenerating: false, generationAbort: null })
        return
      }
      set({ error: String(e), isGenerating: false, generationAbort: null })
    }
  },

  generateCode: async () => {
    const { generatedMd } = get()
    if (!generatedMd.trim()) return

    const abort = new AbortController()
    set({ isGenerating: true, error: null, generatedCode: '', reasoningStream: '', step: 3, generationAbort: abort })
    try {
      const result = await agentApi.generateCode(
        generatedMd,
        (token) => set((s) => ({ generatedCode: s.generatedCode + token })),
        (token) => set((s) => ({ reasoningStream: s.reasoningStream + token })),
        abort.signal,
      )
      set({
        generatedCode: result.code,
        sandboxResults: result.sandbox_results,
        testInputs: initTestInputs(generatedMd),
        debugRounds: [],
        debugStream: '',
        isGenerating: false,
        generationAbort: null,
      })
    } catch (e) {
      if (abort.signal.aborted) {
        set({ isGenerating: false, generationAbort: null })
        return
      }
      set({ error: String(e), isGenerating: false, generationAbort: null })
    }
  },

  /** 中止正在进行的规范/代码生成 */
  stopGeneration: () => {
    const { generationAbort } = get()
    generationAbort?.abort()
  },

  // ── 自动调试（todo 4.3）──
  // 循环由后端 SSE 驱动（沙箱试运行 entrypoint 子进程 → LLM 修复 → 再跑）。
  // 前端职责：转发事件到 debugRounds/debugStream、把 code_updated 写回
  // generatedCode、异常/中断时兜底调用后端 stop 端点。
  autoDebug: async () => {
    const { generatedMd, generatedCode } = get()
    if (!generatedCode.trim()) return
    const { agentId, agentName, role } = parseAgentMeta(generatedMd)

    const controller = new AbortController()
    set({ isAutoDebugging: true, abortController: controller, error: null, debugRounds: [], debugStream: '' })

    try {
      await agentApi.autoDebug({
        specMd: generatedMd,
        agentId,
        agentName,
        role,
        code: generatedCode,
        testInputs: get().testInputs,
        signal: controller.signal,
        onEvent: (eventType, data) => {
          switch (eventType) {
            case 'debug_start':
              set({ debugStream: `🚀 Agent 自动调试启动 (最多 ${data.max_rounds} 轮)\n` })
              break
            case 'round_start':
              set((s) => ({ debugRounds: [...s.debugRounds, { round: Number(data.round), stdout: '', stderr: '', success: false, analysis: '' }] }))
              break
            case 'exec_result':
              set((s) => {
                const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
                if (last) { last.stdout = String(data.stdout || ''); last.stderr = String(data.stderr || ''); last.success = Boolean(data.success) }
                return { debugRounds: rounds }
              })
              break
            case 'thinking':
              set((s) => {
                const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
                if (last) {
                  const msg = (data.message as string) || ''
                  if (msg.includes('过短') || msg.includes('跳过') || msg.includes('补回')) {
                    last.analysis = last.analysis ? `${last.analysis}\n[WARN] ${msg}` : `[WARN] ${msg}`
                  } else if (!last.analysis) {
                    last.analysis = 'LLM 分析中...'
                  }
                }
                return { debugRounds: rounds }
              })
              break
            case 'thinking_stream':
              set((s) => {
                const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
                if (last) {
                  if (last.analysis === 'LLM 分析中...' || last.analysis.startsWith('LLM 分析中')) last.analysis = ''
                  last.analysis += (data.token || '')
                }
                return { debugRounds: rounds }
              })
              break
            case 'analysis_result':
              set((s) => {
                const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
                if (last) last.analysis = (data.analysis as string) || ''
                return { debugRounds: rounds }
              })
              break
            case 'llm_error':
              set((s) => {
                const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
                if (last) {
                  const msg = (data.message as string) || 'LLM 调用异常'
                  last.analysis = last.analysis && last.analysis !== 'LLM 分析中...'
                    ? `${last.analysis}\n❌ ${msg}` : `❌ ${msg}`
                }
                return { debugRounds: rounds }
              })
              break
            case 'code_updated':
              set({ generatedCode: String(data.code || '') })
              set((s) => {
                const rounds = [...s.debugRounds]; const last = rounds[rounds.length - 1]
                if (last) last.code = String(data.code || '')
                return { debugRounds: rounds }
              })
              break
            case 'done':
            case 'stopped':
              if (data.code) set({ generatedCode: String(data.code) })
              set({ isAutoDebugging: false, abortController: null })
              break
          }
        },
      })
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        set({ isAutoDebugging: false, abortController: null })
      } else {
        set({ error: String(e), isAutoDebugging: false, abortController: null })
      }
      // 异常退出时兜底调用后端 stop 端点，确保后台循环也停止
      agentApi.stopAutoDebug(agentId).catch(() => {})
    }
  },

  stopAutoDebug: () => {
    const { abortController, generatedMd } = get()
    if (abortController) abortController.abort()
    const { agentId } = parseAgentMeta(generatedMd)
    // 同时调用后端 stop 端点（即使 SSE 连接已断开也能可靠停止）
    agentApi.stopAutoDebug(agentId).catch(() => {})
    set({ isAutoDebugging: false, abortController: null })
  },

  registerAgent: async () => {
    const { generatedMd, generatedCode } = get()
    if (!generatedMd.trim()) return

    set({ isRegistering: true, error: null })
    try {
      const result = await agentApi.register(generatedMd, generatedCode, get().description)
      set({
        registeredId: result.agent_id,
        step: 4,
        isRegistering: false,
      })
      // 刷新左侧 Agent 空间列表
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchAgentsFromApi()
    } catch (e) { set({ error: String(e), isRegistering: false }) }
  },
}))

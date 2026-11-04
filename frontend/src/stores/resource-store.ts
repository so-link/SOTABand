import { create } from 'zustand'
import type { Resource, ResourceType, AgentResource, ToolResource, DataResource, TaskResource } from '@/types/resources'
import { agentApi } from '@/services/api/agent'
import { toolApi } from '@/services/api/tool'
import { dataApi } from '@/services/api/data'
import { taskApi, type TaskRecord, type RecentAgentRecord } from '@/services/api/task'

/** 后端任务记录 → 左栏「任务历史」Resource 形状 */
function taskToResource(t: TaskRecord): TaskResource {
  const iso = (sec: number | null) => (sec ? new Date(sec * 1000).toISOString() : '')
  const dur = t.duration_ms != null
    ? (t.duration_ms < 1000 ? `${t.duration_ms}ms` : `${(t.duration_ms / 1000).toFixed(1)}s`)
    : ''
  return {
    id: `task-${t.id}`, name: t.input ? t.input.slice(0, 40) : t.agent_id,
    description: `${t.agent_name || t.agent_id}${dur ? ` · ${dur}` : ''}`,
    type: 'task', version: '', status: 'active',
    createdAt: iso(t.started_at), updatedAt: iso(t.finished_at), tags: [],
    state: t.status === 'running' ? 'running' : t.status === 'failed' ? 'failed' : 'done',
    currentStep: 0, totalSteps: 0,
    progress: t.status === 'success' ? 100 : 0,
    startedAt: iso(t.started_at), finishedAt: iso(t.finished_at),
    relatedResourceIds: [t.agent_id],
    executionLog: t.error ? [t.error] : [],
  }
}

interface ResourceState {
  selectedResource: Resource | null
  cachedToolForDetail: ToolResource | null
  cachedDatasetForDetail: DataResource | null
  dataResources: Resource[]
  toolResources: Resource[]
  modelResources: Resource[]
  agentResources: Resource[]
  taskResources: Resource[]
  recentAgentUsage: RecentAgentRecord[]
  isLoading: boolean

  // 数据集预览的文件清单：由中栏预览拉取后共享给右栏文件列表，
  // 右栏点选文件即可切换中栏预览的 CSV / Markdown。
  // 文件内容一律按「数据集 id + 相对路径」访问，不再共享磁盘路径。
  previewDatasetId: string | null
  previewFiles: Array<Record<string, unknown>>
  activeCsvIndex: number
  activeMdIndex: number
  activeTextIndex: number

  selectResource: (resource: Resource | null) => void
  setCachedToolForDetail: (tool: ToolResource | null) => void
  setPreviewFiles: (datasetId: string, files: Array<Record<string, unknown>>) => void
  setActiveCsvIndex: (idx: number) => void
  setActiveMdIndex: (idx: number) => void
  setActiveTextIndex: (idx: number) => void
  fetchResources: (type: ResourceType) => Promise<void>
  fetchAllResources: () => Promise<void>
  fetchAgentsFromApi: () => Promise<void>
  fetchToolsFromApi: () => Promise<void>
  fetchDatasetsFromApi: () => Promise<void>
  fetchModelsFromApi: () => Promise<void>
  fetchTasksFromApi: () => Promise<void>
}

export const useResourceStore = create<ResourceState>((set, get) => ({
  selectedResource: null,
  dataResources: [],
  toolResources: [],
  modelResources: [],
  agentResources: [],
  taskResources: [],
  recentAgentUsage: [],
  isLoading: false,
  cachedToolForDetail: null,
  cachedDatasetForDetail: null,
  previewDatasetId: null,
  previewFiles: [],
  activeCsvIndex: 0,
  activeMdIndex: 0,
  activeTextIndex: 0,

  setPreviewFiles: (datasetId, files) => set({
    previewDatasetId: datasetId,
    previewFiles: files,
    activeCsvIndex: 0,
    activeMdIndex: 0,
    activeTextIndex: 0,
  }),
  setActiveCsvIndex: (idx) => set({ activeCsvIndex: idx }),
  setActiveMdIndex: (idx) => set({ activeMdIndex: idx }),
  setActiveTextIndex: (idx) => set({ activeTextIndex: idx }),

  selectResource: (resource) => {
    const state: Partial<ResourceState> = { selectedResource: resource }
    if (resource?.type === 'tool') {
      state.cachedToolForDetail = resource as ToolResource
    }
    if (resource?.type === 'data') {
      state.cachedDatasetForDetail = resource as DataResource
    }
    set(state as ResourceState)
  },
  setCachedToolForDetail: (tool) => set({ cachedToolForDetail: tool }),

  fetchResources: async (type) => {
    set({ isLoading: true })
    if (type === 'task') {
      await get().fetchTasksFromApi()
    } else if (type === 'tool') {
      await get().fetchToolsFromApi()
    } else if (type === 'agent') {
      await get().fetchAgentsFromApi()
    } else if (type === 'data') {
      await get().fetchDatasetsFromApi()
    } else if (type === 'model') {
      await get().fetchModelsFromApi()
    }
    set({ isLoading: false })
  },

  fetchAllResources: async () => {
    set({ isLoading: true })

    // 优先从真实 API 加载
    const BASE_URL = ''
    const apiResults = await Promise.allSettled([
      toolApi.list(),
      agentApi.list(),
      dataApi.list(true),   // 只加载本机有数据的数据集
      fetch(`${BASE_URL}/api/model/list`).then(r => r.json()),
      taskApi.list(30),
      taskApi.recentAgents(10),
    ])
    const [toolResult, agentResult, dataResult, modelResult, taskResult, recentAgentsResult] = apiResults

    // 工具
    if (toolResult.status === 'fulfilled' && Array.isArray(toolResult.value?.tools) && toolResult.value.tools.length > 0) {
      set({
        toolResources: toolResult.value.tools.map((t: Record<string, unknown>) => ({
          id: t.id as string, name: (t.name as string) || (t.id as string), description: '', type: 'tool' as const,
          category: ((t.type as string) === 'api-wrapper' ? 'model-wrapper' : 'builtin') as ToolResource['category'],
          version: (t.version as string) || '0.1.0', status: 'active' as const, createdAt: (t.created_at as string) || '',
          updatedAt: '', tags: (t.tags as string[]) || [], inputSpec: { formats: [] }, outputSpec: { formats: [] },
          dependencies: [], runtimeEnv: 'python' as const, usageCount: (t.usage_count as number) || 0,
          // 后端按 owner 字段判定：内置示例工具无 owner → 非本地生成
          isUserGenerated: Boolean(t.is_user_generated),
        }))
      })
    }

    // Agent
    if (agentResult.status === 'fulfilled' && Array.isArray(agentResult.value?.agents) && agentResult.value.agents.length > 0) {
      set({
        agentResources: agentResult.value.agents.map((a: Record<string, unknown>) => ({
          id: a.id as string, name: (a.name as string) || (a.id as string), description: '', type: 'agent' as const,
          version: (a.version as string) || '0.1.0', status: 'active' as const, createdAt: (a.created_at as string) || '',
          updatedAt: '', tags: (a.tags as string[]) || [], role: (a.role as AgentResource['role']) || 'task',
          capabilities: '', requiredTools: (a.tools as string[]) || [], inputFormat: 'text', outputFormat: 'text',
          collaborationProtocol: 'pub-sub', healthStatus: (a.health as AgentResource['healthStatus']) || 'healthy',
        }))
      })
    }

    // 数据集
    if (dataResult.status === 'fulfilled' && Array.isArray(dataResult.value?.datasets) && dataResult.value.datasets.length > 0) {
      set({
        dataResources: dataResult.value.datasets.map((d: Record<string, unknown>) => ({
          id: d.id as string, name: (d.name as string) || (d.id as string), description: '', type: 'data' as const,
          version: (d.version as string) || '0.1.0', status: 'active' as const, createdAt: (d.created_at as string) || '',
          updatedAt: '', tags: (d.tags as string[]) || [], format: ((d.formats as string[])?.[0]) || 'unknown',
          filePath: (d.data_path as string) || '', fileSize: (d.total_size as number) || 0, source: 'upload' as const, lineage: [],
          available: d.available as boolean | undefined,
          dataPathAbs: d.data_path_abs as string | undefined,
          fileCountActual: d.file_count_actual as number | undefined,
        }))
      })
    }

    // 模型
    if (modelResult.status === 'fulfilled' && Array.isArray(modelResult.value?.models) && modelResult.value.models.length > 0) {
      set({
        modelResources: modelResult.value.models.map((m: Record<string, unknown>) => ({
          id: m.id as string, name: (m.name as string) || (m.id as string), description: '', type: 'model' as const,
          version: (m.version as string) || '0.1.0', status: 'active' as const, createdAt: (m.created_at as string) || '',
          updatedAt: '', tags: (m.tags as string[]) || [], format: (m.framework as string) || '',
          filePath: (m.model_path as string) || '', fileSize: 0, source: 'upload' as const, lineage: [],
        }))
      })
    }

    // 任务历史（真实 API）
    if (taskResult.status === 'fulfilled') {
      set({ taskResources: taskResult.value.map(taskToResource) })
    }
    if (recentAgentsResult.status === 'fulfilled') {
      set({ recentAgentUsage: recentAgentsResult.value })
    }

    // 后端不可达时保持空列表（展示空态），绝不用 Mock 假数据填充，
    // 否则用户会把演示数据误认为真实资源。
    set({ isLoading: false })
  },

  fetchAgentsFromApi: async () => {
    try {
      const result = await agentApi.list()
      if (Array.isArray(result.agents) && result.agents.length > 0) {
        set({ agentResources: result.agents.map((a: Record<string, unknown>) => ({
          id: a.id as string, name: (a.name as string) || (a.id as string), description: '', type: 'agent' as const,
          version: (a.version as string) || '0.1.0', status: 'active' as const, createdAt: (a.created_at as string) || '',
          updatedAt: '', tags: (a.tags as string[]) || [], role: (a.role as AgentResource['role']) || 'task',
          source: (a.source as AgentResource['source']) || 'builtin',
          capabilities: '', requiredTools: (a.tools as string[]) || [], inputFormat: 'text', outputFormat: 'text',
          collaborationProtocol: 'pub-sub', healthStatus: (a.health as AgentResource['healthStatus']) || 'healthy',
        })) })
      }
    } catch { /* ignore */ }
  },

  fetchToolsFromApi: async () => {
    try {
      const result = await toolApi.list()
      if (Array.isArray(result.tools) && result.tools.length > 0) {
        set({ toolResources: result.tools.map((t: Record<string, unknown>) => ({
          id: t.id as string, name: (t.name as string) || (t.id as string), description: '', type: 'tool' as const,
          category: ((t.type as string) === 'api-wrapper' ? 'model-wrapper' : 'builtin') as ToolResource['category'],
          version: (t.version as string) || '0.1.0', status: 'active' as const, createdAt: (t.created_at as string) || '',
          updatedAt: '', tags: (t.tags as string[]) || [], inputSpec: { formats: [] }, outputSpec: { formats: [] },
          dependencies: [], runtimeEnv: 'python' as const, usageCount: (t.usage_count as number) || 0,
          isUserGenerated: Boolean(t.is_user_generated),
        })) })
      }
    } catch { /* ignore */ }
  },

  fetchDatasetsFromApi: async () => {
    try {
      // 默认只取本机可用的数据集：注册表里他人环境的条目（data_path 指向
      // 对方机器）本机没有数据，展示出来会造成"能选中但跑不了"的误导。
      const result = await dataApi.list(true)
      if (Array.isArray(result.datasets) && result.datasets.length > 0) {
        set({ dataResources: result.datasets.map((d: Record<string, unknown>) => ({
          id: d.id as string, name: (d.name as string) || (d.id as string), description: '', type: 'data' as const,
          version: (d.version as string) || '0.1.0', status: 'active' as const, createdAt: (d.created_at as string) || '',
          updatedAt: '', tags: (d.tags as string[]) || [], format: ((d.formats as string[])?.[0]) || 'unknown',
          filePath: (d.data_path as string) || '', fileSize: (d.total_size as number) || 0, source: 'upload' as const, lineage: [],
          available: d.available as boolean | undefined,
          dataPathAbs: d.data_path_abs as string | undefined,
          fileCountActual: d.file_count_actual as number | undefined,
        })) })
      }
    } catch { /* ignore */ }
  },

  fetchModelsFromApi: async () => {
    try {
      const BASE_URL = ''
      const res = await fetch(`${BASE_URL}/api/model/list`)
      const data = await res.json()
      if (Array.isArray(data.models) && data.models.length > 0) {
        set({ modelResources: data.models.map((m: Record<string, unknown>) => ({
          id: m.id as string, name: (m.name as string) || (m.id as string), description: '',
          type: 'model' as const, version: (m.version as string) || '0.1.0', status: 'active' as const,
          createdAt: (m.created_at as string) || '', updatedAt: '',
          tags: (m.tags as string[]) || [], format: (m.framework as string) || '',
          filePath: (m.model_path as string) || '', fileSize: 0, source: 'upload' as const, lineage: [],
        })) })
      }
    } catch { /* ignore */ }
  },

  fetchTasksFromApi: async () => {
    try {
      const [tasks, recent] = await Promise.all([
        taskApi.list(30), taskApi.recentAgents(10),
      ])
      set({ taskResources: tasks.map(taskToResource), recentAgentUsage: recent })
    } catch { /* ignore */ }
  },
}))

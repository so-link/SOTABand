import { create } from 'zustand'
import type { Resource, ResourceType, AgentResource, ToolResource, DataResource, ModelResource } from '@/types/resources'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'

async function fetchList(url: string, key: string): Promise<Array<Record<string, unknown>>> {
  const res = await fetch(url, { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  if (!Array.isArray(data[key])) throw new Error('后端返回的资源列表格式错误')
  return data[key]
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
  isLoading: boolean
  loadErrors: Partial<Record<ResourceType, string>>

  selectResource: (resource: Resource | null) => void
  setCachedToolForDetail: (tool: ToolResource | null) => void
  fetchResources: (type: ResourceType) => Promise<void>
  fetchAllResources: () => Promise<void>
  fetchAgentsFromApi: () => Promise<void>
  fetchToolsFromApi: () => Promise<void>
  fetchDatasetsFromApi: () => Promise<void>
  fetchModelsFromApi: () => Promise<void>
}

export const useResourceStore = create<ResourceState>((set, get) => ({
  selectedResource: null,
  dataResources: [],
  toolResources: [],
  modelResources: [],
  agentResources: [],
  taskResources: [],
  isLoading: false,
  loadErrors: {},
  cachedToolForDetail: null,
  cachedDatasetForDetail: null,

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
    const state = get()
    if (type === 'tool') await state.fetchToolsFromApi()
    else if (type === 'agent') await state.fetchAgentsFromApi()
    else if (type === 'data') await state.fetchDatasetsFromApi()
    else if (type === 'model') await state.fetchModelsFromApi()
    else if (type === 'task') set({ taskResources: [] })
  },

  fetchAllResources: async () => {
    if (get().isLoading) return
    // 当前后端没有任务历史列表接口，重新加载时不填入演示记录。
    set({ isLoading: true, taskResources: [] })
    try {
      const state = get()
      await Promise.all([
        state.fetchToolsFromApi(),
        state.fetchAgentsFromApi(),
        state.fetchDatasetsFromApi(),
        state.fetchModelsFromApi(),
      ])
      await useWorkspaceToolStore.getState().fetchFromApi(get().toolResources)

      const current = get()
      const selected = current.selectedResource
      if (selected && ![
        ...current.toolResources, ...current.agentResources,
        ...current.dataResources, ...current.modelResources,
      ].some(r => r.id === selected.id && r.type === selected.type)) {
        set({ selectedResource: null, cachedToolForDetail: null, cachedDatasetForDetail: null })
      }
    } finally {
      set({ isLoading: false })
    }
  },

  fetchAgentsFromApi: async () => {
    try {
      const agents = await fetchList('/api/agent/list', 'agents')
      const statuses = await Promise.allSettled(agents.map(async a => {
        const res = await fetch(`/api/agent/${encodeURIComponent(a.id as string)}/status`, { cache: 'no-store' })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<{ running?: boolean }>
      }))
      set(state => ({
        loadErrors: { ...state.loadErrors, agent: undefined },
        agentResources: agents.map((a: Record<string, unknown>, index) => ({
          id: a.id as string, name: (a.name as string) || (a.id as string), description: '', type: 'agent' as const,
          version: (a.version as string) || '0.1.0', status: 'active' as const, createdAt: (a.created_at as string) || '',
          updatedAt: '', tags: (a.tags as string[]) || [], role: (a.role as AgentResource['role']) || 'task',
          capabilities: '', requiredTools: (a.tools as string[]) || [], inputFormat: 'text', outputFormat: 'text',
          collaborationProtocol: 'pub-sub',
          healthStatus: statuses[index].status === 'fulfilled'
            ? (statuses[index].value.running === true ? 'healthy' as const
              : statuses[index].value.running === false ? 'offline' as const : 'degraded' as const)
            : 'degraded' as const,
        })),
      }))
    } catch (error) {
      set(state => ({ agentResources: [], loadErrors: { ...state.loadErrors, agent: `Agent 加载失败：${String(error)}` } }))
    }
  },

  fetchToolsFromApi: async () => {
    try {
      const tools = await fetchList('/api/tool/list', 'tools')
      set(state => ({
        loadErrors: { ...state.loadErrors, tool: undefined },
        toolResources: tools.map((t: Record<string, unknown>) => ({
          id: t.id as string, name: (t.name as string) || (t.id as string), description: '', type: 'tool' as const,
          category: ((t.type as string) === 'api-wrapper' ? 'model-wrapper' : 'builtin') as ToolResource['category'],
          version: (t.version as string) || '0.1.0', status: 'active' as const, createdAt: (t.created_at as string) || '',
          updatedAt: '', tags: (t.tags as string[]) || [], inputSpec: { formats: [] }, outputSpec: { formats: [] },
          dependencies: [], runtimeEnv: 'python' as const, usageCount: (t.usage_count as number) || 0,
          isUserGenerated: Boolean(t.is_user_generated),
        })),
      }))
    } catch (error) {
      set(state => ({ toolResources: [], loadErrors: { ...state.loadErrors, tool: `工具加载失败：${String(error)}` } }))
    }
  },

  fetchDatasetsFromApi: async () => {
    try {
      // 默认只取本机可用的数据集：注册表里他人环境的条目（data_path 指向
      // 对方机器）本机没有数据，展示出来会造成"能选中但跑不了"的误导。
      const datasets = await fetchList('/api/data/list?available_only=true', 'datasets')
      set(state => ({
        loadErrors: { ...state.loadErrors, data: undefined },
        dataResources: datasets.map((d: Record<string, unknown>) => ({
          id: d.id as string, name: (d.name as string) || (d.id as string), description: '', type: 'data' as const,
          version: (d.version as string) || '0.1.0', status: 'active' as const, createdAt: (d.created_at as string) || '',
          updatedAt: '', tags: (d.tags as string[]) || [], format: ((d.formats as string[])?.[0]) || 'unknown',
          filePath: (d.data_path as string) || '', fileSize: (d.total_size as number) || 0, source: 'upload' as const, lineage: [],
          available: d.available as boolean | undefined,
          dataPathAbs: d.data_path_abs as string | undefined,
          fileCountActual: d.file_count_actual as number | undefined,
        })),
      }))
    } catch (error) {
      set(state => ({ dataResources: [], loadErrors: { ...state.loadErrors, data: `数据集加载失败：${String(error)}` } }))
    }
  },

  fetchModelsFromApi: async () => {
    try {
      const models = await fetchList('/api/model/list', 'models')
      set(state => ({
        loadErrors: { ...state.loadErrors, model: undefined },
        modelResources: models.map((m: Record<string, unknown>) => ({
          id: m.id as string, name: (m.name as string) || (m.id as string), description: '',
          type: 'model' as const, version: (m.version as string) || '0.1.0', status: 'active' as const,
          createdAt: (m.created_at as string) || '', updatedAt: '',
          tags: (m.tags as string[]) || [], framework: (m.framework as string) || '',
          modelType: m.model_type as ModelResource['modelType'],
          weightPath: (m.model_path as string) || '',
          inputFormat: Array.isArray(m.input_format) ? m.input_format as string[]
            : m.input_format ? [m.input_format as string] : [],
          outputFormat: Array.isArray(m.output_format) ? m.output_format as string[]
            : m.output_format ? [m.output_format as string] : [],
          deploymentStatus: (m.deployment_status as ModelResource['deploymentStatus']) || 'registered' as const,
        })),
      }))
    } catch (error) {
      set(state => ({ modelResources: [], loadErrors: { ...state.loadErrors, model: `模型加载失败：${String(error)}` } }))
    }
  },
}))

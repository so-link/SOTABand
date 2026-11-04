import { create } from 'zustand'
import type { Resource, ResourceType, AgentResource, ToolResource, DataResource, ModelResource, ModelGroup, ModelCategory } from '@/types/resources'
import { MockResourceService } from '@/services/mock/resources'
import { agentApi } from '@/services/api/agent'
import { toolApi } from '@/services/api/tool'
import { dataApi } from '@/services/api/data'

const resourceService = new MockResourceService()

function mapModelResource(m: Record<string, unknown>): ModelResource {
  return {
    id: m.id as string, name: (m.name as string) || (m.id as string), description: '',
    type: 'model', version: (m.version as string) || '0.1.0', status: 'active',
    createdAt: (m.created_at as string) || '', updatedAt: '',
    tags: (m.tags as string[]) || [], framework: (m.framework as string) || '',
    modelType: 'timeseries', weightPath: (m.model_path as string) || '',
    inputFormat: m.input_format ? [String(m.input_format)] : [],
    outputFormat: m.output_format ? [String(m.output_format)] : [],
    deploymentStatus: 'registered', relatedToolId: (m.associated_tool_id as string) || '',
    runtimeStatus: (m.runtime_status as string) || 'configured',
    groupPath: (m.group_path as string) || '其它模型',
    modelCategory: m.model_category === 'trained' ? 'trained' : 'architecture',
    artifactAvailable: m.artifact_available === true,
  }
}

interface ResourceState {
  selectedResource: Resource | null
  cachedToolForDetail: ToolResource | null
  cachedDatasetForDetail: DataResource | null
  dataResources: Resource[]
  toolResources: Resource[]
  modelResources: Resource[]
  modelGroups: ModelGroup[]
  agentResources: Resource[]
  taskResources: Resource[]
  isLoading: boolean

  selectResource: (resource: Resource | null) => void
  setCachedToolForDetail: (tool: ToolResource | null) => void
  fetchResources: (type: ResourceType) => Promise<void>
  fetchAllResources: () => Promise<void>
  fetchAgentsFromApi: () => Promise<void>
  fetchToolsFromApi: () => Promise<void>
  fetchDatasetsFromApi: () => Promise<void>
  fetchModelsFromApi: () => Promise<void>
  fetchModelGroups: () => Promise<void>
  createModelGroup: (name: string, category: ModelCategory) => Promise<boolean>
  deleteModelGroup: (id: string) => Promise<string | null>
}

export const useResourceStore = create<ResourceState>((set, get) => ({
  selectedResource: null,
  dataResources: [],
  toolResources: [],
  modelResources: [],
  modelGroups: [],
  agentResources: [],
  taskResources: [],
  isLoading: false,
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
    set({ isLoading: true })
    const resources = await resourceService.list(type)
    const key = type === 'data' ? 'dataResources'
      : type === 'tool' ? 'toolResources'
      : type === 'model' ? 'modelResources'
      : type === 'agent' ? 'agentResources'
      : type === 'task' ? 'taskResources'
      : 'dataResources'
    set({ [key]: resources, isLoading: false } as Partial<ResourceState>)
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
    ])
    const [toolResult, agentResult, dataResult, modelResult] = apiResults

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
        modelResources: modelResult.value.models.map(mapModelResource)
      })
    }

    // Mock 兜底：空的类型用 Mock 数据填充
    const s = get()
    const types: ResourceType[] = ['task']
    if (s.modelResources.length === 0) types.push('model')
    if (s.toolResources.length === 0) types.push('tool')
    if (s.agentResources.length === 0) types.push('agent')
    if (s.dataResources.length === 0) types.push('data')

    const mockResults = await Promise.all(types.map((t) => resourceService.list(t)))
    const updates: Partial<ResourceState> = {}
    types.forEach((t, i) => {
      const key = t === 'data' ? 'dataResources' : t === 'tool' ? 'toolResources' : t === 'model' ? 'modelResources' : t === 'agent' ? 'agentResources' : 'taskResources'
      updates[key as keyof ResourceState] = mockResults[i] as never
    })
    set({ ...updates, isLoading: false } as Partial<ResourceState>)
  },

  fetchAgentsFromApi: async () => {
    try {
      const result = await agentApi.list()
      if (Array.isArray(result.agents) && result.agents.length > 0) {
        set({ agentResources: result.agents.map((a: Record<string, unknown>) => ({
          id: a.id as string, name: (a.name as string) || (a.id as string), description: '', type: 'agent' as const,
          version: (a.version as string) || '0.1.0', status: 'active' as const, createdAt: (a.created_at as string) || '',
          updatedAt: '', tags: (a.tags as string[]) || [], role: (a.role as AgentResource['role']) || 'task',
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
      if (Array.isArray(data.models)) {
        set({ modelResources: data.models.map(mapModelResource) })
      }
    } catch { /* ignore */ }
  },

  fetchModelGroups: async () => {
    try {
      const res = await fetch('/api/model/groups')
      const data = await res.json()
      if (Array.isArray(data.groups)) set({ modelGroups: data.groups as ModelGroup[] })
    } catch { /* ignore */ }
  },

  createModelGroup: async (name: string, category: ModelCategory) => {
    try {
      const res = await fetch('/api/model/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, category }),
      })
      if (!res.ok) return false
      await get().fetchModelGroups()
      return true
    } catch {
      return false
    }
  },

  deleteModelGroup: async (id: string) => {
    try {
      const res = await fetch(`/api/model/groups/${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (!res.ok) {
        const data = await res.json()
        return String(data.detail || '删除分组失败')
      }
      await get().fetchModelGroups()
      return null
    } catch {
      return '删除分组失败'
    }
  },
}))

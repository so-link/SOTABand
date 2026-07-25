import { create } from 'zustand'
import type { Resource, ResourceType, AgentResource, ToolResource, DataResource } from '@/types/resources'
import { MockResourceService } from '@/services/mock/resources'
import { agentApi } from '@/services/api/agent'
import { toolApi } from '@/services/api/tool'
import { dataApi } from '@/services/api/data'

const resourceService = new MockResourceService()

interface ResourceState {
  selectedResource: Resource | null
  dataResources: Resource[]
  toolResources: Resource[]
  modelResources: Resource[]
  agentResources: Resource[]
  taskResources: Resource[]
  isLoading: boolean

  selectResource: (resource: Resource | null) => void
  fetchResources: (type: ResourceType) => Promise<void>
  fetchAllResources: () => Promise<void>
  fetchAgentsFromApi: () => Promise<void>
  fetchToolsFromApi: () => Promise<void>
  fetchDatasetsFromApi: () => Promise<void>
}

export const useResourceStore = create<ResourceState>((set, get) => ({
  selectedResource: null,
  dataResources: [],
  toolResources: [],
  modelResources: [],
  agentResources: [],
  taskResources: [],
  isLoading: false,

  selectResource: (resource) => set({ selectedResource: resource }),

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
    const apiResults = await Promise.allSettled([
      toolApi.list(),
      agentApi.list(),
      dataApi.list(),
    ])
    const [toolResult, agentResult, dataResult] = apiResults

    // 工具
    if (toolResult.status === 'fulfilled' && Array.isArray(toolResult.value?.tools) && toolResult.value.tools.length > 0) {
      set({
        toolResources: toolResult.value.tools.map((t: Record<string, unknown>) => ({
          id: t.id as string, name: (t.name as string) || (t.id as string), description: '', type: 'tool' as const,
          category: ((t.type as string) === 'api-wrapper' ? 'model-wrapper' : 'builtin') as ToolResource['category'],
          version: (t.version as string) || '0.1.0', status: 'active' as const, createdAt: (t.created_at as string) || '',
          updatedAt: '', tags: (t.tags as string[]) || [], inputSpec: { formats: [] }, outputSpec: { formats: [] },
          dependencies: [], runtimeEnv: 'python' as const, usageCount: (t.usage_count as number) || 0, isUserGenerated: true,
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
        }))
      })
    }

    // Mock 兜底：空的类型用 Mock 数据填充
    const s = get()
    const types: ResourceType[] = ['model', 'task']
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
          dependencies: [], runtimeEnv: 'python' as const, usageCount: (t.usage_count as number) || 0, isUserGenerated: true,
        })) })
      }
    } catch { /* ignore */ }
  },

  fetchDatasetsFromApi: async () => {
    try {
      const result = await dataApi.list()
      if (Array.isArray(result.datasets) && result.datasets.length > 0) {
        set({ dataResources: result.datasets.map((d: Record<string, unknown>) => ({
          id: d.id as string, name: (d.name as string) || (d.id as string), description: '', type: 'data' as const,
          version: (d.version as string) || '0.1.0', status: 'active' as const, createdAt: (d.created_at as string) || '',
          updatedAt: '', tags: (d.tags as string[]) || [], format: ((d.formats as string[])?.[0]) || 'unknown',
          filePath: (d.data_path as string) || '', fileSize: (d.total_size as number) || 0, source: 'upload' as const, lineage: [],
        })) })
      }
    } catch { /* ignore */ }
  },
}))

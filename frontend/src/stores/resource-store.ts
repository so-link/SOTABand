import { create } from 'zustand'
import type { Resource, ResourceType, AgentResource, ToolResource } from '@/types/resources'
import { MockResourceService } from '@/services/mock/resources'
import { agentApi } from '@/services/api/agent'
import { toolApi } from '@/services/api/tool'

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
}

export const useResourceStore = create<ResourceState>((set) => ({
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
    const types: ResourceType[] = ['data', 'tool', 'model', 'agent', 'task']
    const results = await Promise.all(types.map((t) => resourceService.list(t)))
    set({
      dataResources: results[0],
      toolResources: results[1],
      modelResources: results[2],
      agentResources: results[3],
      taskResources: results[4],
      isLoading: false,
    })
  },

  fetchAgentsFromApi: async () => {
    set({ isLoading: true })
    try {
      const result = await agentApi.list()
      const agents: AgentResource[] = (result.agents || []).map(
        (a: Record<string, unknown>) => ({
          id: a.id as string,
          name: (a.name as string) || (a.id as string),
          description: '',
          type: 'agent' as const,
          version: (a.version as string) || '0.1.0',
          status: (a.status as string) === 'active' ? 'active' as const : 'active' as const,
          createdAt: (a.created_at as string) || '',
          updatedAt: '',
          tags: (a.tags as string[]) || [],
          role: (a.role as AgentResource['role']) || 'task',
          capabilities: '',
          requiredTools: (a.tools as string[]) || [],
          inputFormat: 'text',
          outputFormat: 'text',
          collaborationProtocol: 'pub-sub',
          healthStatus: (a.health as AgentResource['healthStatus']) || 'healthy',
        })
      )
      set({ agentResources: agents, isLoading: false })
    } catch {
      set({ isLoading: false })
    }
  },

  fetchToolsFromApi: async () => {
    set({ isLoading: true })
    try {
      const result = await toolApi.list()
      const tools: ToolResource[] = (result.tools || []).map(
        (t: Record<string, unknown>) => ({
          id: t.id as string,
          name: (t.name as string) || (t.id as string),
          description: '',
          type: 'tool' as const,
          category: ((t.type as string) === 'api-wrapper' ? 'model-wrapper' : 'builtin') as ToolResource['category'],
          version: (t.version as string) || '0.1.0',
          status: (t.status as string) === 'active' ? 'active' as const : 'active' as const,
          createdAt: (t.created_at as string) || '',
          updatedAt: '',
          tags: (t.tags as string[]) || [],
          inputSpec: { formats: [] },
          outputSpec: { formats: [] },
          dependencies: [],
          runtimeEnv: 'python' as const,
          usageCount: (t.usage_count as number) || 0,
          isUserGenerated: true,
        })
      )
      set({ toolResources: tools, isLoading: false })
    } catch {
      set({ isLoading: false })
    }
  },
}))

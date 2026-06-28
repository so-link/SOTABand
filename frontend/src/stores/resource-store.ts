import { create } from 'zustand'
import type { Resource, ResourceType } from '@/types/resources'
import { MockResourceService } from '@/services/mock/resources'

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
}))

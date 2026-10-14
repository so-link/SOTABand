import { create } from 'zustand'

const STORAGE_KEY = 'sotaband_workspace_tools'

export interface WorkspaceTool {
  id: string
  name: string
  tags: string[]
  loadedAt: string
}

interface WorkspaceToolState {
  tools: WorkspaceTool[]
  loadError: string | null
  fetchFromApi: (availableTools?: ReadonlyArray<{ id: string; name: string; tags: string[] }>) => Promise<void>
  addTool: (tool: WorkspaceTool) => void
  removeTool: (id: string) => void
  isLoaded: (id: string) => boolean
}

function saveToStorage(tools: WorkspaceTool[]) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(tools)) } catch { /* ignore */ }
}

export const useWorkspaceToolStore = create<WorkspaceToolState>((set, get) => ({
  tools: [],
  loadError: null,

  // 重新加载只读取当前后端，且加载清单中的工具必须仍在真实工具库中。
  fetchFromApi: async (availableTools) => {
    try {
      const res = await fetch('/api/tool/workspace', { cache: 'no-store' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (!Array.isArray(data.tools)) throw new Error('后端返回的工具加载清单格式错误')
      let registered = availableTools
      if (!registered) {
        const registryRes = await fetch('/api/tool/list', { cache: 'no-store' })
        if (!registryRes.ok) throw new Error(`HTTP ${registryRes.status}`)
        const registryData = await registryRes.json()
        if (!Array.isArray(registryData.tools)) throw new Error('后端返回的工具列表格式错误')
        registered = registryData.tools.map((t: Record<string, unknown>) => ({
          id: t.id as string, name: (t.name as string) || (t.id as string), tags: (t.tags as string[]) || [],
        }))
      }
      const byId = new Map(registered!.map(t => [t.id, t]))
      const tools = (data.tools as WorkspaceTool[]).flatMap(t => {
        const actual = byId.get(t.id)
        return actual ? [{ ...t, name: actual.name, tags: actual.tags }] : []
      })
      set({ tools, loadError: null })
      saveToStorage(tools)
    } catch (error) {
      set({ tools: [], loadError: `工具加载清单获取失败：${String(error)}` })
      saveToStorage([])
    }
  },

  addTool: (tool) => {
    const { tools } = get()
    if (tools.some(t => t.id === tool.id)) return
    const updated = [...tools, { ...tool, loadedAt: new Date().toISOString() }]
    set({ tools: updated })
    saveToStorage(updated)
    // 同步到后端；失败不阻塞本地（下次 fetchFromApi 会重新对齐）
    fetch('/api/tool/workspace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: tool.id, name: tool.name, tags: tool.tags }),
    }).catch(() => { /* ignore */ })
  },

  removeTool: (id) => {
    const updated = get().tools.filter(t => t.id !== id)
    set({ tools: updated })
    saveToStorage(updated)
    fetch(`/api/tool/workspace/${id}`, { method: 'DELETE' }).catch(() => { /* ignore */ })
  },

  isLoaded: (id) => {
    return get().tools.some(t => t.id === id)
  },
}))

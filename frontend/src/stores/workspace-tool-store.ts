import { create } from 'zustand'

export interface WorkspaceTool {
  id: string
  name: string
  tags: string[]
  loadedAt: string
}

interface WorkspaceToolState {
  tools: WorkspaceTool[]
  /** 后端不可达标记：UI 可据此提示"清单加载失败"，与"清单为空"区分 */
  loadError: boolean
  fetchFromApi: () => Promise<void>
  addTool: (tool: WorkspaceTool) => Promise<void>
  removeTool: (id: string) => Promise<void>
  isLoaded: (id: string) => boolean
}

// 后端 storage/workspace_tools.json 是唯一状态源，不做任何 localStorage
// 缓存/降级：网络不可达就显示空清单（loadError 标记可让 UI 提示加载失败）。
// 曾经的 localStorage 降级导致后端重启/清空后，前端仍显示早已不存在的
// 旧工具，用户端看到的数据与后端不一致。
export const useWorkspaceToolStore = create<WorkspaceToolState>((set, get) => ({
  tools: [],
  loadError: false,

  fetchFromApi: async () => {
    try {
      const res = await fetch('/api/tool/workspace', { cache: 'no-store' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      set({ tools: Array.isArray(data.tools) ? data.tools : [], loadError: false })
    } catch {
      // 网络不可达：显示空清单，不保留旧数据
      set({ tools: [], loadError: true })
    }
  },

  addTool: async (tool) => {
    const { tools } = get()
    if (tools.some(t => t.id === tool.id)) return
    try {
      const res = await fetch('/api/tool/workspace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: tool.id, name: tool.name, tags: tool.tags }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch {
      set({ loadError: true })
      return
    }
    set({ tools: [...tools, { ...tool, loadedAt: new Date().toISOString() }], loadError: false })
  },

  removeTool: async (id) => {
    try {
      const res = await fetch(`/api/tool/workspace/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch {
      set({ loadError: true })
      return
    }
    set({ tools: get().tools.filter(t => t.id !== id), loadError: false })
  },

  isLoaded: (id) => {
    return get().tools.some(t => t.id === id)
  },
}))

// 清掉历史版本遗留的 localStorage 键（升级前工具空间只存本地）
try { localStorage.removeItem('sotaband_workspace_tools') } catch { /* ignore */ }

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
  fetchFromApi: () => Promise<void>
  addTool: (tool: WorkspaceTool) => void
  removeTool: (id: string) => void
  isLoaded: (id: string) => boolean
}

// localStorage 仅作降级缓存。真实状态源是后端 storage/workspace_tools.json：
// 以前清单只存 localStorage，换浏览器 / 无痕窗口 / 清缓存后工具空间就全空了。
function loadFromStorage(): WorkspaceTool[] {
  try {
    const data = localStorage.getItem(STORAGE_KEY)
    return data ? JSON.parse(data) : []
  } catch { return [] }
}

function saveToStorage(tools: WorkspaceTool[]) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(tools)) } catch { /* ignore */ }
}

export const useWorkspaceToolStore = create<WorkspaceToolState>((set, get) => ({
  // 首次渲染用 localStorage 占位；马上被 fetchFromApi 覆盖
  tools: loadFromStorage(),

  // 后端是单一状态源。只有网络层真的不可达（fetch reject）时才降级到
  // localStorage——后端 200 OK 返回空数组必须覆盖本地，否则任何短暂
  // 的后端重启 / 网络抖动都会让前端继续显示早已被清空的旧清单。
  fetchFromApi: async () => {
    try {
      const res = await fetch('/api/tool/workspace', { cache: 'no-store' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      const tools: WorkspaceTool[] = Array.isArray(data.tools) ? data.tools : []
      set({ tools })
      saveToStorage(tools)
    } catch {
      // 后端不可达：保留 localStorage 数据（这是 localStorage 的唯一
      // 真实用途——缓存 + 离线降级）。UI 侧仍可正常交互，后端恢复后
      // 下一次 fetchFromApi 会自动对齐。
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
      return  // 后端挂了，不乐观更新——下次 fetchFromApi 会自动对齐
    }
    const updated = [...tools, { ...tool, loadedAt: new Date().toISOString() }]
    set({ tools: updated })
    saveToStorage(updated)
  },

  removeTool: async (id) => {
    try {
      const res = await fetch(`/api/tool/workspace/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch {
      return
    }
    const updated = get().tools.filter(t => t.id !== id)
    set({ tools: updated })
    saveToStorage(updated)
  },

  isLoaded: (id) => {
    return get().tools.some(t => t.id === id)
  },
}))

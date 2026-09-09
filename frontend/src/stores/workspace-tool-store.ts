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
  tools: loadFromStorage(),

  // 启动时以后端为准（无痕窗口 / 换浏览器等 localStorage 为空的场景也能拉到真实清单）
  fetchFromApi: async () => {
    try {
      const res = await fetch('/api/tool/workspace')
      const data = await res.json()
      if (Array.isArray(data.tools) && data.tools.length > 0) {
        set({ tools: data.tools })
        saveToStorage(data.tools)
        return
      }
      // 一次性迁移：后端清单为空而本地 localStorage 有旧数据（升级前只存
      // localStorage），先把本地清单上传到后端，再拉回对齐。
      const local = loadFromStorage()
      if (local.length > 0) {
        for (const t of local) {
          await fetch('/api/tool/workspace', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: t.id, name: t.name, tags: t.tags }),
          }).catch(() => { /* ignore */ })
        }
        const r2 = await fetch('/api/tool/workspace')
        const d2 = await r2.json()
        if (Array.isArray(d2.tools) && d2.tools.length > 0) {
          set({ tools: d2.tools })
          saveToStorage(d2.tools)
        }
      }
    } catch { /* 后端不可达时保留 localStorage 数据 */ }
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

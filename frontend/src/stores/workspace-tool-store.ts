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
  addTool: (tool: WorkspaceTool) => void
  removeTool: (id: string) => void
  isLoaded: (id: string) => boolean
}

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

  addTool: (tool) => {
    const { tools } = get()
    if (tools.some(t => t.id === tool.id)) return
    const updated = [...tools, { ...tool, loadedAt: new Date().toISOString() }]
    set({ tools: updated })
    saveToStorage(updated)
  },

  removeTool: (id) => {
    set((s) => {
      const updated = s.tools.filter(t => t.id !== id)
      saveToStorage(updated)
      return { tools: updated }
    })
  },

  isLoaded: (id) => {
    return get().tools.some(t => t.id === id)
  },
}))

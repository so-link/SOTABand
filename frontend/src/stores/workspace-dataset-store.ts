import { create } from 'zustand'

const STORAGE_KEY = 'sotaband_workspace_datasets'

export interface WorkspaceDataset {
  id: string
  name: string
  tags: string[]
  loadedAt: string
}

interface WorkspaceDatasetState {
  datasets: WorkspaceDataset[]
  addDataset: (ds: WorkspaceDataset) => void
  removeDataset: (id: string) => void
  isLoaded: (id: string) => boolean
}

function loadFromStorage(): WorkspaceDataset[] {
  try {
    const data = localStorage.getItem(STORAGE_KEY)
    return data ? JSON.parse(data) : []
  } catch { return [] }
}

function saveToStorage(datasets: WorkspaceDataset[]) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(datasets)) } catch { /* ignore */ }
}

export const useWorkspaceDatasetStore = create<WorkspaceDatasetState>((set, get) => ({
  datasets: loadFromStorage(),

  addDataset: (ds) => {
    const { datasets } = get()
    if (datasets.some(d => d.id === ds.id)) return
    const updated = [...datasets, { ...ds, loadedAt: new Date().toISOString() }]
    set({ datasets: updated })
    saveToStorage(updated)
  },

  removeDataset: (id) => {
    set((s) => {
      const updated = s.datasets.filter(d => d.id !== id)
      saveToStorage(updated)
      return { datasets: updated }
    })
  },

  isLoaded: (id) => {
    return get().datasets.some(d => d.id === id)
  },
}))

import { create } from 'zustand'
import type { FileTreeNode } from '@/types/workspace'

const STORAGE_KEY = 'maia-workspace-tree'

function loadPersisted(): FileTreeNode | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return null
}

function persistTree(tree: FileTreeNode) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tree))
  } catch { /* ignore */ }
}

function emptyTree(): FileTreeNode {
  return {
    id: 'root',
    name: '工作区间',
    type: 'directory',
    category: 'folder',
    path: '/workspace',
    expanded: true,
    children: [],
  }
}

interface FileTreeState {
  root: FileTreeNode | null
  selectedFile: FileTreeNode | null
  isLoading: boolean
  searchQuery: string

  loadTree: () => Promise<void>
  selectFile: (node: FileTreeNode | null) => void
  toggleExpand: (nodeId: string) => void
  uploadFiles: (files: FileList) => Promise<void>
  setSearchQuery: (query: string) => void
  getFilteredTree: () => FileTreeNode | null
  persist: () => void
}

export const useFileTreeStore = create<FileTreeState>((set, get) => ({
  root: null,
  selectedFile: null,
  isLoading: false,
  searchQuery: '',

  loadTree: async () => {
    set({ isLoading: true })
    // 1. 尝试从 localStorage 恢复
    const persisted = loadPersisted()
    if (persisted) {
      set({ root: persisted, isLoading: false })
      return
    }
    // 2. 首次启动：空工作区间
    set({ root: emptyTree(), isLoading: false })
  },

  selectFile: (node) => set({ selectedFile: node }),

  toggleExpand: (nodeId) => {
    const { root } = get()
    if (!root) return
    const toggleIn = (n: FileTreeNode): FileTreeNode => {
      if (n.id === nodeId) return { ...n, expanded: !n.expanded }
      if (n.children) return { ...n, children: n.children.map(toggleIn) }
      return n
    }
    const newRoot = toggleIn(root)
    set({ root: newRoot })
    persistTree(newRoot)
  },

  uploadFiles: async (files: FileList) => {
    const BASE_URL = ''
    let { root } = get()
    if (!root) root = emptyTree()

    for (const file of Array.from(files)) {
      try {
        const formData = new FormData()
        formData.append('file', file)
        const res = await fetch(`${BASE_URL}/api/file/upload`, { method: 'POST', body: formData })
        if (!res.ok) continue
        const uploaded = await res.json()
        const newNode: FileTreeNode = {
          id: uploaded.id, name: uploaded.fileName, type: 'file', category: 'unknown',
          path: uploaded.filePath, format: uploaded.format, size: uploaded.fileSize,
        }
        root = { ...root, children: [...(root.children || []), newNode] }
      } catch { /* ignore */ }
    }
    set({ root })
    persistTree(root)
  },

  setSearchQuery: (query) => set({ searchQuery: query }),

  getFilteredTree: () => {
    const { root, searchQuery } = get()
    if (!root || !searchQuery.trim()) return root
    const filter = (n: FileTreeNode): FileTreeNode | null => {
      const m = n.name.toLowerCase().includes(searchQuery.toLowerCase())
      if (n.type === 'file') return m ? n : null
      const filtered = n.children?.map(filter).filter(Boolean) as FileTreeNode[]
      return filtered?.length > 0 || m ? { ...n, children: filtered, expanded: true } : null
    }
    return filter(root)
  },

  persist: () => {
    const { root } = get()
    if (root) persistTree(root)
  },
}))

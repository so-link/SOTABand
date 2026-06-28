import { create } from 'zustand'
import type { FileTreeNode } from '@/types/workspace'
import { MockFileService } from '@/services/mock/files'

const fileService = new MockFileService()

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
}

export const useFileTreeStore = create<FileTreeState>((set, get) => ({
  root: null,
  selectedFile: null,
  isLoading: false,
  searchQuery: '',

  loadTree: async () => {
    set({ isLoading: true })
    const tree = await fileService.getTree()
    set({ root: tree, isLoading: false })
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

    set({ root: toggleIn(root) })
  },

  uploadFiles: async (files: FileList) => {
    const fileArray = Array.from(files)
    await fileService.upload(fileArray)
    await get().loadTree()
  },

  setSearchQuery: (query) => set({ searchQuery: query }),

  getFilteredTree: () => {
    const { root, searchQuery } = get()
    if (!root || !searchQuery.trim()) return root

    const filter = (n: FileTreeNode): FileTreeNode | null => {
      const nameMatch = n.name.toLowerCase().includes(searchQuery.toLowerCase())
      if (n.type === 'file') return nameMatch ? n : null

      const filteredChildren = n.children?.map(filter).filter(Boolean) as FileTreeNode[]
      if (filteredChildren.length > 0 || nameMatch) {
        return { ...n, children: filteredChildren, expanded: true }
      }
      return null
    }
    return filter(root)
  },
}))

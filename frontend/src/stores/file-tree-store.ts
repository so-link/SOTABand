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

/** 判断是否为「真实目录」节点（通过「打开目录」或「导入数据集」加载，需要刷新） */
function isRealDir(n: FileTreeNode): boolean {
  return n.type === 'directory' && !!n.path && n.path !== '/workspace' && !n.path.startsWith('/workspace')
}

/** 递归刷新所有真实目录节点，使其与实际文件系统保持一致 */
async function refreshDirectories(
  tree: FileTreeNode,
  onDone: (newRoot: FileTreeNode) => void,
): Promise<void> {
  const BASE_URL = ''

  const refreshIn = async (n: FileTreeNode): Promise<FileTreeNode> => {
    if (!n.children) return n
    // 先递归刷新子节点
    const refreshedChildren: FileTreeNode[] = []
    for (const child of n.children) {
      refreshedChildren.push(await refreshIn(child))
    }

    // 当前节点是真实目录 → 重新扫描
    if (isRealDir(n)) {
      try {
        const res = await fetch(`${BASE_URL}/api/file/scan-directory?path=${encodeURIComponent(n.path)}`)
        if (res.ok) {
          const data = await res.json()
          const scannedRoot = data.root as FileTreeNode
          // 用扫描结果替换 children，保留原节点的 name/id/expanded 等元信息
          return { ...n, children: scannedRoot?.children ?? [], expanded: n.expanded ?? true }
        }
      } catch { /* 目录不存在或不可访问，保留原数据 */ }
    }
    return { ...n, children: refreshedChildren }
  }

  try {
    const newRoot = await refreshIn(tree)
    onDone(newRoot)
  } catch { /* ignore */ }
}

interface FileTreeState {
  root: FileTreeNode | null
  selectedFile: FileTreeNode | null
  /** 当前正在预览的文件（双击触发） */
  previewFile: FileTreeNode | null
  isLoading: boolean
  searchQuery: string

  loadTree: () => Promise<void>
  selectFile: (node: FileTreeNode | null) => void
  setPreviewFile: (node: FileTreeNode | null) => void
  toggleExpand: (nodeId: string) => void
  uploadFiles: (files: FileList) => Promise<void>
  removeNode: (nodeId: string) => void
  setSearchQuery: (query: string) => void
  getFilteredTree: () => FileTreeNode | null
  persist: () => void
}

export const useFileTreeStore = create<FileTreeState>((set, get) => ({
  root: null,
  selectedFile: null,
  previewFile: null,
  isLoading: false,
  searchQuery: '',

  loadTree: async () => {
    set({ isLoading: true })
    // 1. 尝试从 localStorage 恢复
    const persisted = loadPersisted()
    if (!persisted) {
      // 首次启动：空工作区间
      set({ root: emptyTree(), isLoading: false })
      return
    }
    set({ root: persisted, isLoading: false })
    // 2. 异步刷新目录节点，与实际文件系统保持一致
    refreshDirectories(persisted, (newRoot) => {
      set({ root: newRoot })
      persistTree(newRoot)
    })
  },

  selectFile: (node) => set({ selectedFile: node }),

  setPreviewFile: (node) => set({ previewFile: node }),

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

  removeNode: (nodeId) => {
    const { root } = get()
    if (!root) return
    const removeIn = (n: FileTreeNode): FileTreeNode | null => {
      if (n.id === nodeId) return null
      if (n.children) {
        const children = n.children.map(removeIn).filter((c): c is FileTreeNode => c !== null)
        return { ...n, children }
      }
      return n
    }
    const newRoot = removeIn(root)
    if (newRoot) {
      set({ root: newRoot })
      persistTree(newRoot)
    }
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

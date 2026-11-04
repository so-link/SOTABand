import { create } from 'zustand'
import type { FileTreeNode } from '@/types/workspace'

const STORAGE_KEY = 'maia-workspace-tree'

// localStorage 仅作降级缓存。真实状态源是后端 storage/workspace_tree.json：
// 以前整棵工作区文件树只存 localStorage，换浏览器 / 无痕窗口 / 清缓存后工作区间
// 就全空了，后端也无从知晓用户的工作现场。现在与工具空间同一范式——
// 启动时以后端为准，写入时同步后端，后端不可达才回落到 localStorage。
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

/** 同步写入后端；失败不阻塞本地（下次 loadTree 会重新对齐）。 */
function persistTreeToBackend(tree: FileTreeNode) {
  fetch('/api/file/workspace/tree', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root: tree }),
  }).catch(() => { /* 后端不可达时保留 localStorage 数据 */ })
}

/** 本地 + 后端双写，作为「工作区已变更」的统一出口。 */
function persistEverywhere(tree: FileTreeNode) {
  persistTree(tree)
  persistTreeToBackend(tree)
}

function emptyTree(): FileTreeNode {
  return {
    id: 'root',
    name: '我的文件',
    type: 'directory',
    category: 'folder',
    expanded: true,
    children: [],
  }
}

/** 按扩展名归类图标（与后端 _CATEGORY_BY_EXT 的粗分类对齐） */
const CATEGORY_BY_EXT: Record<string, FileTreeNode['category']> = {
  jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', bmp: 'image', webp: 'image', svg: 'image', ico: 'image',
  csv: 'table', tsv: 'table', xlsx: 'table', xls: 'table', json: 'table',
  md: 'text', txt: 'text', log: 'text', markdown: 'text',
  pt: 'model', pth: 'model', onnx: 'model', h5: 'model', safetensors: 'model',
  zip: 'archive', tar: 'archive', gz: 'archive', rar: 'archive', '7z': 'archive',
  set: 'eeg', edf: 'eeg', bdf: 'eeg',
}

function categoryFor(name: string, format?: string): FileTreeNode['category'] {
  const ext = (format || name.split('.').pop() || '').toLowerCase()
  return CATEGORY_BY_EXT[ext] || 'unknown'
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
  clearWorkspace: () => Promise<void>
}

export const useFileTreeStore = create<FileTreeState>((set, get) => ({
  root: null,
  selectedFile: null,
  previewFile: null,
  isLoading: false,
  searchQuery: '',

  loadTree: async () => {
    set({ isLoading: true })

    // 1. 先以后端为准：后端保存了工作现场（服务重启 / 换浏览器都能恢复）
    let persisted: FileTreeNode | null = null
    try {
      const res = await fetch('/api/file/workspace/tree')
      if (res.ok) {
        const data = await res.json()
        if (data && data.root) {
          persisted = data.root as FileTreeNode
        } else {
          // 后端为空，而本地 localStorage 有数据（升级前只存 localStorage）：
          // 一次性迁移——把本地工作区上传到后端，避免用户已有的工作现场丢失。
          const local = loadPersisted()
          if (local) {
            persistTreeToBackend(local)
            persisted = local
          }
        }
      }
    } catch { /* 后端不可达：回落到 localStorage */ }

    // 2. 后端不可达或为空时，回落到 localStorage
    if (!persisted) {
      persisted = loadPersisted()
    }

    if (!persisted) {
      // 首次启动：空工作区间
      set({ root: emptyTree(), isLoading: false })
      return
    }

    set({ root: persisted, isLoading: false })
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
    persistEverywhere(newRoot)
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
        // 后端登记后只回 fileId：路径留在服务端登记表，前端不再持有
        const newNode: FileTreeNode = {
          id: uploaded.fileId,
          name: uploaded.fileName,
          type: 'file',
          category: categoryFor(uploaded.fileName, uploaded.format),
          fileId: uploaded.fileId,
          format: uploaded.format,
          size: uploaded.fileSize,
        }
        root = { ...root, children: [...(root.children || []), newNode] }
      } catch { /* ignore */ }
    }
    set({ root })
    persistEverywhere(root)
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
      persistEverywhere(newRoot)
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
    if (root) persistEverywhere(root)
  },

  // 清空工作区间的唯一入口。
  // 不要让调用方自己 setState + persist：那样每加一个删除入口，
  // 就要记得在别处同步清理附件，迟早会漏（本次 bug 正是这么产生的）。
  clearWorkspace: async () => {
    const fresh = emptyTree()
    set({ root: fresh, selectedFile: null })
    persistEverywhere(fresh)

    // 文件已全部移除，对话里残留的附件引用一并清掉。
    // 动态 import 与 chat-store 解耦其它 store 的写法保持一致，
    // 同时避免任何模块初始化顺序上的耦合。
    try {
      const { useChatStore } = await import('@/stores/chat-store')
      useChatStore.getState().pruneAttachmentsToValidIds(new Set())
    } catch {
      // 清理失败不影响清空这一主操作
    }
  },
}))

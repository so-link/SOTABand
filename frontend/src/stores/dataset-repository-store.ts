import { create } from 'zustand'

const BASE_URL = ''

export interface RepositoryDataset {
  id: string
  name: string
  version: string
  type: string
  tags: string[]
  formats: string[]
  file_count: number
  total_size: number
  created_at: string
  thumbnail?: string
  description_preview?: string
}

interface DatasetRepositoryState {
  datasets: RepositoryDataset[]
  tagStats: Record<string, number>
  selectedTags: string[]
  searchQuery: string
  sortBy: 'name' | 'latest' | 'files' | 'size'
  page: number
  pageSize: number
  loading: boolean

  fetchDatasets: () => Promise<void>
  toggleTag: (tag: string) => void
  setSearch: (query: string) => void
  setSort: (sort: 'name' | 'latest' | 'files' | 'size') => void
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

export const useDatasetRepositoryStore = create<DatasetRepositoryState>((set, get) => ({
  datasets: [],
  tagStats: {},
  selectedTags: [],
  searchQuery: '',
  sortBy: 'latest',
  page: 1,
  pageSize: 12,
  loading: false,

  fetchDatasets: async () => {
    set({ loading: true })
    try {
      const res = await fetch(`${BASE_URL}/api/data/repository`)
      const data = await res.json()
      const datasets = (data.datasets || []).map((d: Record<string, unknown>) => ({
        id: d.id || '',
        name: d.name || d.id || '',
        version: d.version || '0.1.0',
        type: d.type || 'generic',
        tags: Array.isArray(d.tags) ? d.tags : [],
        formats: Array.isArray(d.formats) ? d.formats : [],
        file_count: (d.file_count as number) || 0,
        total_size: (d.total_size as number) || 0,
        created_at: (d.created_at as string) || '',
        thumbnail: (d.thumbnail as string) || undefined,
        description_preview: (d.description_preview as string) || undefined,
      }))
      set({ datasets, tagStats: data.tag_stats || {}, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  toggleTag: (tag) => {
    const { selectedTags } = get()
    if (selectedTags.includes(tag)) {
      set({ selectedTags: selectedTags.filter(t => t !== tag), page: 1 })
    } else {
      set({ selectedTags: [...selectedTags, tag], page: 1 })
    }
  },

  setSearch: (query) => set({ searchQuery: query, page: 1 }),
  setSort: (sort) => set({ sortBy: sort, page: 1 }),
  setPage: (page) => set({ page }),
  setPageSize: (size) => set({ pageSize: size, page: 1 }),
}))

/** 格式化文件大小 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)}KB`
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)}MB`
  return `${(bytes / 1073741824).toFixed(2)}GB`
}

/** 获取筛选和排序后的数据集列表 */
export function getFilteredDatasets(state: DatasetRepositoryState): RepositoryDataset[] {
  let result = [...state.datasets]

  // 搜索
  if (state.searchQuery.trim()) {
    const q = state.searchQuery.toLowerCase()
    result = result.filter(d =>
      d.id.toLowerCase().includes(q) ||
      d.name.toLowerCase().includes(q) ||
      d.tags.some(tag => tag.toLowerCase().includes(q))
    )
  }

  // 标签筛选 (AND)
  if (state.selectedTags.length > 0) {
    result = result.filter(d =>
      state.selectedTags.every(tag => d.tags.includes(tag))
    )
  }

  // 排序
  if (state.sortBy === 'name') {
    result.sort((a, b) => a.id.localeCompare(b.id))
  } else if (state.sortBy === 'files') {
    result.sort((a, b) => b.file_count - a.file_count)
  } else if (state.sortBy === 'size') {
    result.sort((a, b) => b.total_size - a.total_size)
  } else {
    // latest: 默认按创建时间倒序
    result.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
  }

  return result
}

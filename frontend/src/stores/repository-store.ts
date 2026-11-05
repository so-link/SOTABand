import { create } from 'zustand'

import { apiGet } from '@/services/api/client'

export interface RepositoryTool {
  id: string
  name: string
  version: string
  tags: string[]
  usage_count: number
  created_at: string
  /** 后端计算的写权限标记（仅用于前端按钮门控） */
  can_edit?: boolean
  is_owner?: boolean
}

interface RepositoryState {
  tools: RepositoryTool[]
  tagStats: Record<string, number>
  selectedTags: string[]
  searchQuery: string
  sortBy: 'name' | 'latest' | 'usage'
  page: number
  pageSize: number
  loading: boolean

  fetchTools: () => Promise<void>
  toggleTag: (tag: string) => void
  setSearch: (query: string) => void
  setSort: (sort: 'name' | 'latest' | 'usage') => void
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

export const useRepositoryStore = create<RepositoryState>((set, get) => ({
  tools: [],
  tagStats: {},
  selectedTags: [],
  searchQuery: '',
  sortBy: 'latest',
  page: 1,
  pageSize: 12,
  loading: false,

  fetchTools: async () => {
    set({ loading: true })
    try {
      const data = await apiGet<{ tools: any[]; tag_stats?: Record<string, number> }>(
        '/api/tool/repository'
      )
      const tools = (data.tools || []).map((t: any) => ({
        id: t.id || '',
        name: t.name || t.id || '',
        version: t.version || '0.1.0',
        tags: Array.isArray(t.tags) ? t.tags : [],
        usage_count: (t.usage_count as number) || 0,
        created_at: (t.created_at as string) || '',
        // 三态保留：字段缺失时保持 undefined（视图按「有权限」渲染，后端兜底），
        // 不能写成 `=== true` —— 那会把缺失变成 false，误判为不可编辑。
        can_edit: typeof t.can_edit === 'boolean' ? t.can_edit : undefined,
        is_owner: t.is_owner === true,
      }))
      set({ tools, tagStats: data.tag_stats || {}, loading: false })
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

/** 获取筛选和排序后的工具列表 */
export function getFilteredTools(state: RepositoryState): RepositoryTool[] {
  let result = [...state.tools]

  // 搜索
  if (state.searchQuery.trim()) {
    const q = state.searchQuery.toLowerCase()
    result = result.filter(t =>
      t.id.toLowerCase().includes(q) ||
      t.name.toLowerCase().includes(q) ||
      t.tags.some(tag => tag.toLowerCase().includes(q))
    )
  }

  // 标签筛选 (AND)
  if (state.selectedTags.length > 0) {
    result = result.filter(t =>
      state.selectedTags.every(tag => t.tags.includes(tag))
    )
  }

  // 排序
  if (state.sortBy === 'name') {
    result.sort((a, b) => a.id.localeCompare(b.id))
  } else if (state.sortBy === 'usage') {
    result.sort((a, b) => b.usage_count - a.usage_count)
  } else {
    // latest: 默认按创建时间倒序，没有时间的放最后
    result.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
  }

  return result
}

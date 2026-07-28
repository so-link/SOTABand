import { useEffect, useState } from 'react'
import {
  ArrowLeft, Search, ChevronLeft, ChevronRight,
  Trash2, Plus, Check, Package,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useUIStore } from '@/stores/ui-store'
import { useRepositoryStore, getFilteredTools, type RepositoryTool } from '@/stores/repository-store'

const PAGE_SIZE_OPTIONS = [8, 16, 24, 32, 48]
const BASE_URL = ''

/** 工具图标映射 */
function getToolIcon(id: string): string {
  const lower = id.toLowerCase()
  if (lower.includes('image') || lower.includes('img') || lower.includes('photo')) return '🖼️'
  if (lower.includes('csv') || lower.includes('table') || lower.includes('data') || lower.includes('excel')) return '📊'
  if (lower.includes('pdf') || lower.includes('paper') || lower.includes('doc')) return '📄'
  if (lower.includes('audio') || lower.includes('sound') || lower.includes('music') || lower.includes('voice')) return '🔊'
  if (lower.includes('video') || lower.includes('movie')) return '🎬'
  if (lower.includes('text') || lower.includes('nlp') || lower.includes('llm') || lower.includes('ai')) return '🤖'
  if (lower.includes('web') || lower.includes('http') || lower.includes('api')) return '🌐'
  if (lower.includes('file') || lower.includes('convert')) return '📁'
  return '🔧'
}

export function RepositoryView() {
  const { setActiveView } = useUIStore()
  const store = useRepositoryStore()
  const { tools, tagStats, selectedTags, searchQuery, sortBy, page, pageSize, loading, fetchTools, toggleTag, setSearch, setSort, setPage, setPageSize } = store
  const [loadedIds, setLoadedIds] = useState<Set<string>>(new Set())

  useEffect(() => { fetchTools() }, [])

  const filtered = getFilteredTools(store)
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageTools = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  // 折叠标签（超过 8 个时折叠）
  const allTags = Object.entries(tagStats)
  const [showAllTags, setShowAllTags] = useState(false)
  const visibleTags = showAllTags ? allTags : allTags.slice(0, 8)

  const handleDelete = async (tool: RepositoryTool) => {
    if (!confirm(`确定删除工具 "${tool.id}"？\n此操作不可撤销，将永久删除代码文件和注册记录。`)) return
    try {
      await fetch(`${BASE_URL}/api/tool/${tool.id}`, { method: 'DELETE' })
      fetchTools()
    } catch { /* ignore */ }
  }

  const handleImport = (tool: RepositoryTool) => {
    setLoadedIds(prev => new Set(prev).add(tool.id))
    // TODO Phase 3: 实际导入到工作空间 store
  }

  return (
    <div className="flex flex-col h-full bg-maia-surface overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]" onClick={() => setActiveView('chat')}>
            <ArrowLeft className="h-3.5 w-3.5 mr-1" />返回空间
          </Button>
          <Package className="h-4 w-4 text-amber-400" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">
            工具仓库（共 {tools.length} 个工具）
          </span>
        </div>
      </div>

      <div className="flex-1 p-4 max-w-7xl mx-auto space-y-3 w-full">
        {/* 搜索和排序 */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-maia-text-muted" />
            <input
              value={searchQuery}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索工具名称、标签..."
              className="w-full h-8 pl-7 pr-3 rounded border border-maia-border bg-maia-bg text-[12px] text-maia-text outline-none focus:border-maia-accent/40"
            />
          </div>
          <select
            value={sortBy}
            onChange={e => setSort(e.target.value as 'name' | 'latest' | 'usage')}
            className="h-8 rounded border border-maia-border bg-maia-bg text-[12px] text-maia-text px-2 outline-none focus:border-maia-accent/40"
          >
            <option value="latest">最新</option>
            <option value="name">名称</option>
            <option value="usage">最常用</option>
          </select>
        </div>

        {/* 标签筛选 */}
        {allTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              onClick={() => { /* clear all */ useRepositoryStore.setState({ selectedTags: [], page: 1 }) }}
              className={`px-2 py-0.5 rounded text-[11px] transition-colors ${selectedTags.length === 0 ? 'bg-maia-accent text-white' : 'bg-maia-bg text-maia-text-muted hover:bg-maia-sidebar-hover'}`}
            >
              全部 ({tools.length})
            </button>
            {visibleTags.map(([tag, count]) => (
              <button
                key={tag}
                onClick={() => toggleTag(tag)}
                className={`px-2 py-0.5 rounded text-[11px] transition-colors ${selectedTags.includes(tag) ? 'bg-maia-accent text-white' : 'bg-maia-bg text-maia-text-muted hover:bg-maia-sidebar-hover'}`}
              >
                #{tag} ({count})
              </button>
            ))}
            {allTags.length > 8 && (
              <button
                onClick={() => setShowAllTags(!showAllTags)}
                className="px-2 py-0.5 rounded text-[11px] bg-maia-bg text-maia-text-muted hover:bg-maia-sidebar-hover"
              >
                {showAllTags ? '收起' : `+ ${allTags.length - 8} 更多...`}
              </button>
            )}
          </div>
        )}

        {/* 工具卡片网格 */}
        {loading ? (
          <div className="flex items-center justify-center py-16 text-maia-text-muted text-sm">加载中...</div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-maia-text-muted gap-2">
            <Package className="h-8 w-8 opacity-20" />
            <p className="text-sm">{tools.length === 0 ? '工具仓库为空' : '没有匹配的工具'}</p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-4 gap-2">
              {pageTools.map(tool => {
                const isLoaded = loadedIds.has(tool.id)
                return (
                  <Card key={tool.id} className="border-maia-border hover:border-maia-accent/30 transition-colors cursor-pointer"
                    onClick={() => setActiveView('tool-detail')}>
                    <CardBody className="p-3">
                      <div className="flex items-start justify-between mb-1.5">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="text-base shrink-0">{getToolIcon(tool.id)}</span>
                          <div className="min-w-0">
                            <div className="text-[12px] font-mono font-medium text-maia-text truncate">{tool.id}</div>
                            <div className="text-[11px] text-maia-text-secondary truncate">{tool.name}</div>
                          </div>
                        </div>
                      </div>
                      {tool.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1 mb-1.5">
                          {tool.tags.map(tag => (
                            <Badge key={tag} variant="default" className="text-[9px] px-1 py-0 cursor-pointer"
                              onClick={(e) => { e.stopPropagation(); toggleTag(tag) }}>
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      )}
                      <div className="flex items-center justify-between text-[10px] text-maia-text-muted mb-2">
                        <span>v{tool.version}</span>
                        <span>已用 {tool.usage_count} 次</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Button size="sm" variant="ghost" className="h-6 px-2 text-[10px] text-red-400 hover:text-red-300 hover:bg-red-500/10"
                          onClick={(e) => { e.stopPropagation(); handleDelete(tool) }}>
                          <Trash2 className="h-3 w-3 mr-0.5" />删除
                        </Button>
                        {isLoaded ? (
                          <Button size="sm" variant="ghost" className="h-6 px-2 text-[10px] text-maia-text-muted" disabled>
                            <Check className="h-3 w-3 mr-0.5" />已加载
                          </Button>
                        ) : (
                          <Button size="sm" variant="ghost" className="h-6 px-2 text-[10px] text-maia-accent hover:text-maia-accent"
                            onClick={(e) => { e.stopPropagation(); handleImport(tool) }}>
                            <Plus className="h-3 w-3 mr-0.5" />添加
                          </Button>
                        )}
                      </div>
                    </CardBody>
                  </Card>
                )
              })}
            </div>

            {/* 分页 */}
            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 pt-2">
                <div className="flex items-center gap-1">
                  <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage <= 1}
                    onClick={() => setPage(safePage - 1)}><ChevronLeft className="h-3 w-3" /></Button>
                  {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                    let pageNum: number
                    if (totalPages <= 7) { pageNum = i + 1 }
                    else if (safePage <= 4) { pageNum = i + 1 }
                    else if (safePage >= totalPages - 3) { pageNum = totalPages - 6 + i }
                    else { pageNum = safePage - 3 + i }
                    return (
                      <Button key={pageNum} size="sm" variant={pageNum === safePage ? 'default' : 'outline'}
                        className="h-6 w-6 p-0 text-[11px]" onClick={() => setPage(pageNum)}>
                        {pageNum}
                      </Button>
                    )
                  })}
                  <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage >= totalPages}
                    onClick={() => setPage(safePage + 1)}><ChevronRight className="h-3 w-3" /></Button>
                </div>
                <select value={pageSize} onChange={e => setPageSize(Number(e.target.value))}
                  className="h-6 rounded border border-maia-border bg-maia-bg text-[11px] text-maia-text px-1 outline-none">
                  {PAGE_SIZE_OPTIONS.map(n => (<option key={n} value={n}>{n} 个/页</option>))}
                </select>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { useUIStore } from '@/stores/ui-store'
import { useResourceStore } from '@/stores/resource-store'
import { useDatasetRepositoryStore, getFilteredDatasets, formatFileSize, type RepositoryDataset } from '@/stores/dataset-repository-store'
import { useWorkspaceDatasetStore } from '@/stores/workspace-dataset-store'
import { ArrowLeft, Search, Trash2, Plus, Package, ChevronLeft, ChevronRight, ChevronDown, Loader2, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'

const BASE_URL = ''

function getDatasetIcon(formats: string[]): string {
  const has = (ext: string) => formats.some(f => f.toLowerCase().includes(ext))
  if (has('png') || has('jpg') || has('jpeg') || has('gif') || has('bmp') || has('tiff')) return '🖼️'
  if (has('csv') || has('xlsx') || has('xls') || has('tsv')) return '📊'
  if (has('pdf') || has('doc') || has('docx')) return '📄'
  if (has('md') || has('txt')) return '📝'
  if (has('wav') || has('mp3') || has('flac') || has('ogg')) return '🔊'
  if (has('edf') || has('bdf') || has('nifti') || has('nii') || has('gz')) return '🧠'
  if (has('json')) return '🏷️'
  return '📁'
}

export function DatasetRepositoryView() {
  const { setActiveView } = useUIStore()
  const selectResource = useResourceStore((s) => s.selectResource)
  const store = useDatasetRepositoryStore()
  const { addDataset, removeDataset, isLoaded } = useWorkspaceDatasetStore()

  const [showSort, setShowSort] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [showAllTags, setShowAllTags] = useState(false)

  useEffect(() => { store.fetchDatasets() }, [store.fetchDatasets])

  const filtered = getFilteredDatasets(store)
  const totalPages = Math.max(1, Math.ceil(filtered.length / store.pageSize))
  const startIdx = (store.page - 1) * store.pageSize
  const pageDatasets = filtered.slice(startIdx, startIdx + store.pageSize)

  const handleDelete = async (id: string) => {
    if (!confirm(`确定要删除数据集 "${id}" 吗？此操作不可逆，将清除所有关联文件。`)) return
    setDeleting(id)
    try {
      await fetch(`${BASE_URL}/api/data/${id}`, { method: 'DELETE' })
      removeDataset(id)
      await store.fetchDatasets()
    } catch { alert('删除失败') }
    finally { setDeleting(null) }
  }

  const handleAddToWorkspace = (ds: RepositoryDataset) => {
    addDataset({ id: ds.id, name: ds.name, tags: ds.tags, loadedAt: new Date().toISOString() })
  }

  const handleRemoveFromWorkspace = (ds: RepositoryDataset) => {
    removeDataset(ds.id)
  }

  const handleCardClick = (ds: RepositoryDataset) => {
    console.log('[DatasetRepositoryView] handleCardClick:', ds.id)
    // 先设置 selectedResource，再切换视图
    const resource = {
      id: ds.id,
      name: ds.name,
      type: 'data' as const,
      version: ds.version,
      status: 'active' as const,
      tags: ds.tags,
      description: '',
      createdAt: ds.created_at,
      updatedAt: '',
      format: '',
      filePath: '',
      fileSize: ds.total_size,
      source: 'upload' as const,
      lineage: [],
      isUserGenerated: true,
      category: 'local' as const,
      inputSpec: { formats: [] },
      outputSpec: { formats: [] },
      dependencies: [],
      runtimeEnv: 'python' as const,
      usageCount: 0,
    }
    selectResource(resource)
    console.log('[DatasetRepositoryView] selectResource done, switching to data-preview')
    setActiveView('data-preview')
  }

  const sortLabels: Record<string, string> = { latest: '最新', name: '名称', files: '文件数', size: '数据量' }

  const maxVisiblePages = 7
  let pageStart = Math.max(1, store.page - Math.floor(maxVisiblePages / 2))
  const pageEnd = Math.min(totalPages, pageStart + maxVisiblePages - 1)
  if (pageEnd - pageStart < maxVisiblePages - 1) pageStart = Math.max(1, pageEnd - maxVisiblePages + 1)
  const pages = Array.from({ length: pageEnd - pageStart + 1 }, (_, i) => pageStart + i)

  // 标签统计
  const tagEntries = Object.entries(store.tagStats).sort((a, b) => b[1] - a[1])
  const MAX_TAGS = 8
  const visibleTags = showAllTags ? tagEntries : tagEntries.slice(0, MAX_TAGS)

  return (
    <div className="flex flex-col h-full bg-maia-bg">
      {/* 头部 */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-maia-border shrink-0">
        <div className="flex items-center gap-3">
          <button onClick={() => setActiveView('chat')}
            className="p-1.5 rounded hover:bg-maia-sidebar-hover text-maia-text-muted hover:text-maia-text transition-colors"
            title="返回对话">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <Package className="h-4 w-4 text-maia-accent" />
          <span className="text-sm font-semibold text-maia-text">数据集仓库</span>
          <span className="text-[11px] text-maia-text-muted">（共 {store.datasets.length} 个数据集）</span>
        </div>
        {store.loading && <Loader2 className="h-4 w-4 animate-spin text-maia-text-muted" />}
      </div>

      {/* 搜索栏 */}
      <div className="flex items-center gap-3 px-5 py-2.5 border-b border-maia-border shrink-0">
        <div className="relative flex-1 max-w-[320px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-maia-text-muted" />
          <input value={store.searchQuery} onChange={(e) => store.setSearch(e.target.value)}
            placeholder="搜索数据集名称、标签..."
            className="w-full h-8 pl-8 pr-3 rounded border border-maia-border bg-maia-surface text-[12px] text-maia-text placeholder:text-maia-text-muted focus:outline-none focus:border-maia-accent/50" />
        </div>
        {/* 排序 */}
        <div className="relative">
          <button onClick={() => setShowSort(!showSort)}
            className="flex items-center gap-1.5 h-8 px-3 rounded border border-maia-border bg-maia-surface text-[12px] text-maia-text-secondary hover:text-maia-text transition-colors">
            {sortLabels[store.sortBy] || '最新'}
            <ChevronDown className="h-3 w-3" />
          </button>
          {showSort && (
            <div className="absolute right-0 top-full mt-1 z-20 w-28 rounded border border-maia-border bg-maia-surface shadow-lg py-1"
              onMouseLeave={() => setShowSort(false)}>
              {Object.entries(sortLabels).map(([key, label]) => (
                <button key={key} onClick={() => { store.setSort(key as typeof store.sortBy); setShowSort(false) }}
                  className={`w-full text-left px-3 py-1.5 text-[12px] transition-colors ${store.sortBy === key ? 'text-maia-accent bg-maia-sidebar-hover' : 'text-maia-text-secondary hover:text-maia-text hover:bg-maia-sidebar-hover'}`}>
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 标签筛选 */}
      {tagEntries.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-5 py-2 border-b border-maia-border shrink-0">
          {visibleTags.map(([tag, count]) => (
            <button key={tag} onClick={() => store.toggleTag(tag)}
              className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                store.selectedTags.includes(tag)
                  ? 'bg-maia-accent text-white'
                  : 'bg-maia-surface border border-maia-border text-maia-text-muted hover:text-maia-text hover:border-maia-text-muted'
              }`}>
              {tag} ({count})
            </button>
          ))}
          {tagEntries.length > MAX_TAGS && (
            <button onClick={() => setShowAllTags(!showAllTags)}
              className="px-2 py-0.5 text-[11px] text-maia-accent hover:underline">
              {showAllTags ? '收起' : `+${tagEntries.length - MAX_TAGS} 更多`}
            </button>
          )}
        </div>
      )}

      {/* 数据集卡片网格 */}
      <div className="flex-1 overflow-auto px-5 py-4">
        {pageDatasets.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-maia-text-muted gap-2">
            <Package className="h-8 w-8" />
            <p className="text-sm">没有找到匹配的数据集</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {pageDatasets.map((ds) => {
              const loaded = isLoaded(ds.id)
              const isDeleting = deleting === ds.id
              return (
                <div key={ds.id}
                  onClick={() => handleCardClick(ds)}
                  className="rounded-lg border border-maia-border bg-maia-surface hover:border-maia-accent/30 hover:shadow-sm transition-all flex flex-col cursor-pointer">
                  {/* 缩略图/预览文字/图标 + 名称 + 大文件警告 */}
                  <div className="p-3 pb-0">
                    <div className="flex items-start gap-2.5">
                      {ds.thumbnail ? (
                        <img src={ds.thumbnail}
                          alt={ds.name}
                          className="w-12 h-12 rounded object-cover shrink-0 border border-maia-border"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                        />
                      ) : (
                        <span className="text-2xl shrink-0">{getDatasetIcon(ds.formats)}</span>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <p className="text-[13px] font-semibold text-maia-text truncate">{ds.name}</p>
                          {ds.total_size > 100 * 1024 * 1024 && (
                            <span title="数据集较大（>100MB），导入时请注意磁盘空间">
                              <AlertTriangle className="h-3 w-3 text-amber-500 shrink-0" />
                            </span>
                          )}
                        </div>
                        <p className="text-[10px] text-maia-text-muted font-mono truncate">{ds.id}</p>
                      </div>
                    </div>
                    {/* 无缩略图时显示文字预览 */}
                    {!ds.thumbnail && ds.description_preview && (
                      <p className="mt-2 text-[11px] text-maia-text-muted leading-relaxed line-clamp-2">
                        {ds.description_preview}
                      </p>
                    )}
                  </div>

                  {/* 标签 */}
                  <div className="px-3 py-1.5">
                    <div className="flex flex-wrap gap-1">
                      {ds.tags.map((tag) => (
                        <button key={tag} onClick={(e) => { e.stopPropagation(); store.toggleTag(tag); }}
                          className="px-1.5 py-px rounded text-[10px] bg-maia-bg border border-maia-border text-maia-text-muted hover:text-maia-text hover:border-maia-text-muted transition-colors cursor-pointer">
                          {tag}
                        </button>
                      ))}
                      {ds.tags.length === 0 && (
                        <span className="text-[10px] text-maia-text-muted italic">无标签</span>
                      )}
                    </div>
                  </div>

                  {/* 元信息 */}
                  <div className="px-3 py-1 text-[10px] text-maia-text-muted flex items-center justify-between">
                    <span>v{ds.version}</span>
                    <span>{ds.file_count} 文件 · {formatFileSize(ds.total_size)}</span>
                  </div>

                  {/* 操作按钮 */}
                  <div className="mt-auto px-3 pb-3 pt-1.5 flex gap-2" onClick={(e) => e.stopPropagation()}>
                    {loaded ? (
                      <>
                        <Button size="sm" variant="outline" disabled
                          className="flex-1 h-7 text-[11px] opacity-50">
                          已添加
                        </Button>
                        <Button size="sm" variant="outline"
                          onClick={() => handleRemoveFromWorkspace(ds)}
                          className="flex-1 h-7 text-[11px] border-maia-border text-maia-text-muted hover:text-maia-danger hover:border-maia-danger/50 transition-colors">
                          移除
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" variant="outline"
                        onClick={() => handleAddToWorkspace(ds)}
                        className="flex-1 h-7 text-[11px] border-maia-accent/30 text-maia-accent hover:bg-maia-accent/10 transition-colors">
                        <Plus className="h-3 w-3" />添加
                      </Button>
                    )}
                    <Button size="sm" variant="ghost"
                      onClick={() => handleDelete(ds.id)} disabled={isDeleting}
                      className="h-7 w-7 p-0 text-maia-text-muted hover:text-maia-danger hover:bg-maia-danger/10 transition-colors"
                      title="删除数据集">
                      {isDeleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-5 py-2.5 border-t border-maia-border shrink-0">
          <div className="flex items-center gap-1">
            <select value={store.pageSize} onChange={(e) => store.setPageSize(Number(e.target.value))}
              className="h-7 px-2 rounded border border-maia-border bg-maia-surface text-[11px] text-maia-text-secondary focus:outline-none">
              {[8, 12, 16, 24, 32].map(n => (
                <option key={n} value={n}>{n} 个/页</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-0.5">
            <button onClick={() => store.setPage(Math.max(1, store.page - 1))} disabled={store.page <= 1}
              className="p-1 rounded text-maia-text-muted hover:text-maia-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            {pageStart > 1 && (
              <>
                <button onClick={() => store.setPage(1)}
                  className="w-7 h-7 rounded text-[11px] text-maia-text-muted hover:text-maia-text hover:bg-maia-sidebar-hover transition-colors">1</button>
                {pageStart > 2 && <span className="text-[11px] text-maia-text-muted px-0.5">…</span>}
              </>
            )}
            {pages.map(p => (
              <button key={p} onClick={() => store.setPage(p)}
                className={`w-7 h-7 rounded text-[11px] font-medium transition-colors ${
                  p === store.page
                    ? 'bg-maia-accent text-white'
                    : 'text-maia-text-muted hover:text-maia-text hover:bg-maia-sidebar-hover'
                }`}>{p}</button>
            ))}
            {pageEnd < totalPages && (
              <>
                {pageEnd < totalPages - 1 && <span className="text-[11px] text-maia-text-muted px-0.5">…</span>}
                <button onClick={() => store.setPage(totalPages)}
                  className="w-7 h-7 rounded text-[11px] text-maia-text-muted hover:text-maia-text hover:bg-maia-sidebar-hover transition-colors">{totalPages}</button>
              </>
            )}
            <button onClick={() => store.setPage(Math.min(totalPages, store.page + 1))} disabled={store.page >= totalPages}
              className="p-1 rounded text-maia-text-muted hover:text-maia-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

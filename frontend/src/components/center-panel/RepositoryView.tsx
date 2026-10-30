import { useEffect, useRef, useState } from 'react'
import {
  ArrowLeft, Search, ChevronLeft, ChevronRight,
  Trash2, Plus, Check, Package, Download, Upload, X, Loader2, AlertTriangle,
  FileText, Image as ImageIcon, Table, Bot, Globe, Wrench, Music, Film, Folder,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardBody } from '@/components/ui/card'
import { useUIStore } from '@/stores/ui-store'
import { useResourceStore } from '@/stores/resource-store'
import { useRepositoryStore, getFilteredTools, type RepositoryTool } from '@/stores/repository-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'
import { toolApi } from '@/services/api/tool'

const PAGE_SIZE_OPTIONS = [8, 12, 16, 24, 32]
const BASE_URL = ''

/** 工具图标映射：用图标组件而不是 emoji，避免服务器缺 emoji 字体时显示成方块 */
function getToolIcon(id: string) {
  const lower = id.toLowerCase()
  if (lower.includes('image') || lower.includes('img') || lower.includes('photo')) return ImageIcon
  if (lower.includes('csv') || lower.includes('table') || lower.includes('data') || lower.includes('excel')) return Table
  if (lower.includes('pdf') || lower.includes('paper') || lower.includes('doc')) return FileText
  if (lower.includes('audio') || lower.includes('sound') || lower.includes('music') || lower.includes('voice')) return Music
  if (lower.includes('video') || lower.includes('movie')) return Film
  if (lower.includes('text') || lower.includes('nlp') || lower.includes('llm') || lower.includes('ai')) return Bot
  if (lower.includes('web') || lower.includes('http') || lower.includes('api')) return Globe
  if (lower.includes('file') || lower.includes('convert')) return Folder
  return Wrench
}

export function RepositoryView() {
  const { setActiveView } = useUIStore()
  const { selectResource } = useResourceStore()
  const store = useRepositoryStore()
  const { tools, tagStats, selectedTags, searchQuery, sortBy, page, pageSize, loading, fetchTools, toggleTag, setSearch, setSort, setPage, setPageSize } = store
  const { isLoaded, addTool } = useWorkspaceToolStore()

  useEffect(() => { fetchTools() }, [])

  const filtered = getFilteredTools(store)
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageTools = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  // 折叠标签（超过 8 个时折叠）
  const allTags = Object.entries(tagStats)
  const [showAllTags, setShowAllTags] = useState(false)
  const visibleTags = showAllTags ? allTags : allTags.slice(0, 8)

  // 缺失依赖弹窗状态
  const [depDialog, setDepDialog] = useState<{
    tool: RepositoryTool
    missing: string[]      // 展示用（import 名）
    install: string[]      // 安装用（pip 包名）
    env: string
    installing: boolean
    done: boolean
    error: string
  } | null>(null)

  const handleDelete = async (tool: RepositoryTool) => {
    if (!confirm(`确定删除工具 "${tool.id}"？\n此操作不可撤销，将永久删除代码文件和注册记录。`)) return
    try {
      const res = await fetch(`${BASE_URL}/api/tool/${tool.id}`, { method: 'DELETE' })
      if (res.ok) {
        // 同步从工具空间中移除
        const { useWorkspaceToolStore } = await import('@/stores/workspace-tool-store')
        useWorkspaceToolStore.getState().removeTool(tool.id)
        fetchTools()
      }
    } catch { /* ignore */ }
  }

  // ── 依赖检查弹窗 ──────────────────────────────────────────
  // 添加工具时先检查代码依赖在当前执行环境是否齐备：
  // 有缺失就弹窗让用户一键安装，否则工具加进来后第一次调用才
  // 抛 ModuleNotFoundError，用户看到「Missing module: xxx」无从下手。
  const doImport = (tool: RepositoryTool) => {
    addTool({ id: tool.id, name: tool.name, tags: tool.tags, loadedAt: new Date().toISOString() })
  }

  const handleAddWithDepCheck = async (tool: RepositoryTool) => {
    try {
      const res = await fetch(`${BASE_URL}/api/tool/${tool.id}/check-deps`)
      if (!res.ok) { doImport(tool); return }
      const data = await res.json()
      const missing: string[] = data.missing || []
      if (missing.length === 0) {
        doImport(tool)
        return
      }
      setDepDialog({
        tool, missing,
        install: data.install?.length ? data.install : missing,
        env: data.env || '', installing: false, done: false, error: '',
      })
    } catch {
      // 检查失败不阻塞添加，按无缺失处理
      doImport(tool)
    }
  }

  const handleInstallMissing = async () => {
    if (!depDialog) return
    setDepDialog({ ...depDialog, installing: true, error: '' })
    try {
      const res = await fetch(`${BASE_URL}/api/tool/${depDialog.tool.id}/install-deps`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dependencies: depDialog.install }),
      })
      const data = await res.json().catch(() => ({}))
      const results: Array<{ dep: string; success: boolean }> = data.results || []
      const failed = results.filter(r => !r.success).map(r => r.dep)
      if (failed.length > 0 && results.length > 0) {
        setDepDialog({
          ...depDialog, installing: false, done: false,
          error: `以下依赖安装失败：${failed.join(', ')}`,
        })
      } else {
        setDepDialog({ ...depDialog, installing: false, done: true, error: '' })
      }
    } catch (e) {
      setDepDialog({ ...depDialog, installing: false, error: `安装请求失败：${String(e)}` })
    }
  }

  // ── 导入 zip 工具包 ────────────────────────────────────────
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const importInputRef = useRef<HTMLInputElement>(null)

  const handleImportFile = async (file: File) => {
    setImporting(true)
    setImportMsg(null)
    try {
      let r = await toolApi.importTool(file)
      if (r.status === 409) {
        const conflictId = r.body.imported || file.name.replace(/\.zip$/i, '')
        if (confirm(`${r.body.detail || `工具 "${conflictId}" 已存在`} 覆盖将替换本地版本，确定？`)) {
          r = await toolApi.importTool(file, true)
        } else {
          setImporting(false)
          return
        }
      }
      if (r.ok) {
        const b = r.body
        let msg = `工具 "${b.imported}" 导入成功。依赖尚未安装，运行时会自动提示安装`
        if (b.overrode_builtin) msg += '（已遮蔽内置同名工具，删除后可恢复内置版本）'
        setImportMsg({ ok: true, text: msg })
        fetchTools()
      } else {
        setImportMsg({ ok: false, text: r.body.detail || '导入失败' })
      }
    } catch (e) {
      setImportMsg({ ok: false, text: `导入请求失败：${String(e)}` })
    } finally {
      setImporting(false)
      if (importInputRef.current) importInputRef.current.value = ''
    }
  }

  return (
    <div className="flex flex-col h-full bg-maia-surface overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" className="h-7 px-2 text-[13px]" onClick={() => setActiveView('chat')}>
            <ArrowLeft className="h-3.5 w-3.5 mr-1" />返回空间
          </Button>
          <span className="text-[13px] font-semibold text-maia-text-heading">
            工具仓库（共 {tools.length} 个）
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" className="h-7 px-2 text-[13px]"
            disabled={importing}
            onClick={() => importInputRef.current?.click()}>
            {importing
              ? <><Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />导入中...</>
              : <><Upload className="h-3.5 w-3.5 mr-1" />导入工具包</>}
          </Button>
          <input
            ref={importInputRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) handleImportFile(f) }}
          />
        </div>
      </div>

      <div className="flex-1 p-4 max-w-7xl mx-auto space-y-3 w-full">
        {importMsg && (
          <div className={`flex items-center justify-between px-3 py-2 rounded border text-[13px] ${
            importMsg.ok
              ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/25'
              : 'bg-red-500/10 text-red-400 border-red-500/25'}`}>
            <span className="break-words">{importMsg.text}</span>
            <button className="shrink-0 ml-2 opacity-70 hover:opacity-100" onClick={() => setImportMsg(null)}>
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
        {/* 搜索和排序 */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-maia-text-muted" />
            <input
              value={searchQuery}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索工具名称、标签..."
              className="w-full h-8 pl-7 pr-3 rounded border border-maia-border bg-maia-bg text-[13px] text-maia-text outline-none focus:border-maia-accent/40"
            />
          </div>
          <select
            value={sortBy}
            onChange={e => setSort(e.target.value as 'name' | 'latest' | 'usage')}
            className="h-8 rounded border border-maia-border bg-maia-bg text-[13px] text-maia-text px-2 outline-none focus:border-maia-accent/40"
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
              className={`inline-flex items-center h-7 px-2.5 rounded text-[12px] transition-colors ${selectedTags.length === 0 ? 'bg-maia-accent text-white' : 'bg-maia-bg text-maia-text-muted hover:bg-maia-sidebar-hover'}`}
            >
              全部 ({tools.length})
            </button>
            {visibleTags.map(([tag, count]) => (
              <button
                key={tag}
                onClick={() => toggleTag(tag)}
                className={`inline-flex items-center h-7 px-2.5 rounded text-[12px] transition-colors ${selectedTags.includes(tag) ? 'bg-maia-accent text-white' : 'bg-maia-bg text-maia-text-muted hover:bg-maia-sidebar-hover'}`}
              >
                #{tag} ({count})
              </button>
            ))}
            {allTags.length > 8 && (
              <button
                onClick={() => setShowAllTags(!showAllTags)}
                className="inline-flex items-center h-7 px-2.5 rounded text-[12px] bg-maia-bg text-maia-text-muted hover:bg-maia-sidebar-hover"
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
            <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2">
              {pageTools.map(tool => {
                const loaded = isLoaded(tool.id)
                return (
                  <Card key={tool.id} className="border-maia-border/60 hover:border-cyan-500/30 hover:shadow-lg hover:shadow-cyan-500/5 transition-all duration-200 cursor-pointer bg-gradient-to-b from-maia-surface to-maia-bg/40 dark:from-maia-surface dark:to-maia-bg/60 h-full flex flex-col"
                    onClick={() => {
                      selectResource({
                        id: tool.id,
                        name: tool.name,
                        type: 'tool' as const,
                        version: tool.version,
                        status: 'active' as const,
                        tags: tool.tags,
                        description: '',
                        createdAt: tool.created_at,
                        updatedAt: '',
                        isUserGenerated: true,
                        category: 'local' as const,
                        inputSpec: { formats: [] },
                        outputSpec: { formats: [] },
                        dependencies: [],
                        runtimeEnv: 'python' as const,
                        usageCount: tool.usage_count,
                      })
                      setActiveView('tool-detail')
                    }}>
                    <CardBody className="p-3 flex flex-col flex-1 min-h-0">
                      <div className="flex items-start justify-between mb-1.5 shrink-0">
                        <div className="flex items-center gap-2 min-w-0">
                          {(() => {
                            const Icon = getToolIcon(tool.id)
                            return (
                              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] bg-maia-accent-light text-maia-accent">
                                <Icon className="h-3.5 w-3.5" />
                              </span>
                            )
                          })()}
                          <div className="min-w-0">
                            <div className="text-[13px] font-semibold text-maia-text-heading truncate">{tool.name}</div>
                            <div className="text-[12px] font-mono text-maia-text-muted truncate">{tool.id}</div>
                          </div>
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-1 mb-2">
                        {tool.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {tool.tags.map(tag => (
                              <span key={tag}
                                className="inline-flex items-center px-1.5 py-0.5 rounded text-[12px] font-medium cursor-pointer
                                  bg-maia-accent-light text-maia-accent border border-maia-accent-border
                                  hover:bg-maia-accent-light/80 transition-colors"
                                onClick={(e) => { e.stopPropagation(); toggleTag(tag) }}>
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center justify-between text-[12px] text-maia-text-muted mb-1.5 mt-auto shrink-0">
                        <span className="font-mono">v{tool.version}</span>
                        <span>已用 {tool.usage_count} 次</span>
                      </div>
                      <div className="flex items-center gap-1 shrink-0 border-t border-maia-border/40 pt-2 mt-0.5">
                        <a
                          href={toolApi.exportUrl(tool.id)}
                          download={`${tool.id}.zip`}
                          title="导出为 zip 工具包（含代码、文档、依赖清单，不含虚拟环境）"
                          onClick={(e) => e.stopPropagation()}
                          className="flex-1 inline-flex items-center justify-center gap-1.5 h-7 rounded text-[12px] whitespace-nowrap text-maia-text-muted hover:text-maia-accent hover:bg-maia-sidebar-hover transition-colors">
                          <Download className="h-3 w-3 shrink-0" />导出
                        </a>
                        <Button size="sm" variant="ghost" className="flex-1 h-7 px-0 text-[12px] whitespace-nowrap text-maia-danger hover:text-maia-danger hover:bg-maia-danger-bg"
                          onClick={(e) => { e.stopPropagation(); handleDelete(tool) }}>
                          <Trash2 className="h-3 w-3 shrink-0 mr-1" />删除
                        </Button>
                        {loaded ? (
                          <span className="flex-1 inline-flex items-center justify-center gap-1.5 h-7 rounded text-[12px] whitespace-nowrap text-maia-text-muted">
                            <Check className="h-3 w-3 shrink-0" />已加载
                          </span>
                        ) : (
                          <Button size="sm" variant="ghost" className="flex-1 h-7 px-0 text-[12px] whitespace-nowrap text-maia-accent hover:text-maia-accent"
                            onClick={(e) => { e.stopPropagation(); handleAddWithDepCheck(tool) }}>
                            <Plus className="h-3 w-3 shrink-0 mr-1" />添加
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
                        className="h-6 w-6 p-0 text-[12px]" onClick={() => setPage(pageNum)}>
                        {pageNum}
                      </Button>
                    )
                  })}
                  <Button size="sm" variant="outline" className="h-6 w-6 p-0" disabled={safePage >= totalPages}
                    onClick={() => setPage(safePage + 1)}><ChevronRight className="h-3 w-3" /></Button>
                </div>
                <select value={pageSize} onChange={e => setPageSize(Number(e.target.value))}
                  className="h-6 rounded border border-maia-border bg-maia-bg text-[12px] text-maia-text px-1 outline-none">
                  {PAGE_SIZE_OPTIONS.map(n => (<option key={n} value={n}>{n} 个/页</option>))}
                </select>
              </div>
            )}
          </>
        )}
      </div>

      {/* 缺失依赖安装弹窗 */}
      {depDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => { if (!depDialog.installing) setDepDialog(null) }}>
          <div className="w-[440px] max-w-[92vw] rounded-lg border border-maia-border bg-maia-surface shadow-2xl"
            onClick={e => e.stopPropagation()}>
            {/* 标题栏 */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-maia-border">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-400" />
                <span className="text-sm font-semibold text-maia-text-heading">
                  {depDialog.done ? '依赖已安装' : '检测到缺失类库'}
                </span>
              </div>
              {!depDialog.installing && (
                <button className="text-maia-text-muted hover:text-maia-text"
                  onClick={() => setDepDialog(null)}>
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>

            <div className="px-4 py-3 space-y-3">
              {depDialog.done ? (
                <div className="flex items-center gap-2 text-[13px] text-emerald-400">
                  <Check className="h-4 w-4" />
                  缺失依赖已安装完成，工具已添加到工作空间。
                </div>
              ) : (
                <>
                  <p className="text-[13px] text-maia-text-muted leading-relaxed">
                    工具 <span className="font-mono text-maia-text">{depDialog.tool.id}</span> 依赖以下
                    类库，当前执行环境
                    {depDialog.env && <span className="text-maia-text">（{depDialog.env}）</span>}
                    中尚未安装：
                  </p>
                  <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
                    {depDialog.missing.map(dep => (
                      <span key={dep}
                        className="inline-flex items-center px-2 py-0.5 rounded text-[12px] font-mono
                          bg-amber-500/10 text-amber-500 border border-amber-500/25">
                        {dep}
                      </span>
                    ))}
                  </div>
                  {depDialog.installing && (
                    <div className="flex items-center gap-2 text-[13px] text-maia-text-muted">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      正在安装，请稍候（可能需要数分钟）...
                    </div>
                  )}
                  {depDialog.error && (
                    <div className="text-[13px] text-red-400 break-words">{depDialog.error}</div>
                  )}
                </>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-maia-border">
              {depDialog.done ? (
                <Button size="sm" className="h-7 text-[12px]" onClick={() => setDepDialog(null)}>
                  完成
                </Button>
              ) : (
                <>
                  <Button size="sm" variant="ghost" className="h-7 text-[12px]"
                    disabled={depDialog.installing}
                    onClick={() => { doImport(depDialog.tool); setDepDialog(null) }}>
                    跳过
                  </Button>
                  <Button size="sm" className="h-7 text-[12px]"
                    disabled={depDialog.installing}
                    onClick={handleInstallMissing}>
                    {depDialog.installing
                      ? <><Loader2 className="h-3 w-3 mr-1 animate-spin" />安装中...</>
                      : <><Download className="h-3 w-3 mr-1" />安装缺失类库</>}
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

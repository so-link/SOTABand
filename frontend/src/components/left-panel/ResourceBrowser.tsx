import { useState, useEffect, useRef } from 'react'
import {
  Database,
  Wrench,
  BrainCircuit,
  Bot,
  ChevronRight,
  ChevronDown,
  Plus,
  Minus,
  Package,
  CalendarClock,
  Upload,
} from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore, parseAgentDetailView, parseToolDetailView, parseModelDetailView, parseDatasetPreviewView } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useAgentEditorStore } from '@/stores/agent-editor-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'
import { useAuthStore } from '@/stores/auth-store'
import { useUnsavedMarks } from '@/hooks/use-unsaved-marks'
import { confirmDialog, alertDialog } from '@/components/ui/confirm-dialog'
import { cn } from '@/lib/utils'
import type { Resource, ResourceType, ToolResource, TaskResource, AgentResource } from '@/types/resources'

interface ResourceSectionDef {
  type: ResourceType
  label: string
  icon: typeof Database
  color: string
}

const SECTIONS: ResourceSectionDef[] = [
  { type: 'data', label: '数据空间', icon: Database, color: 'text-blue-500' },
  { type: 'tool', label: '工具空间', icon: Wrench, color: 'text-amber-500' },
  { type: 'model', label: '模型空间', icon: BrainCircuit, color: 'text-emerald-500' },
  { type: 'agent', label: 'Agent 空间', icon: Bot, color: 'text-purple-500' },
]

// 资源空间各分区的展开/收起状态持久化键
const EXPANDED_SECTIONS_KEY = 'sotaband-expanded-sections'

type DepResultItem = { dep: string; success: boolean; already?: boolean; reason?: string }

interface DepDialogState {
  kind: 'tool' | 'agent'
  /** 工具 id 或 Agent id */
  targetId: string
  toolName: string
  /** 缺失的 import 名 */
  missing: string[]
  /** 与 missing 一一对应的 pip 包名（安装用） */
  install: string[]
  /** Agent 专属：它依赖但目标环境没有的工具 */
  missingTools?: string[]
  installing: boolean
  results?: DepResultItem[]
  error?: string
}

export function ResourceBrowser() {
  const {
    dataResources,
    toolResources,
    modelResources,
    agentResources,
    taskResources,
    selectedResource,
    selectResource,
    fetchAgentsFromApi,
    fetchToolsFromApi,
    fetchDatasetsFromApi,
    fetchModelsFromApi,
    fetchTasksFromApi,
  } = useResourceStore()
  const { rightPanelOpen, toggleRightPanel, setActiveView } = useUIStore()
  const activeView = useUIStore((s) => s.activeView)

  // 左栏选中跟随中栏标签：刷新恢复标签或从其他入口打开资源时，高亮对应资源项
  useEffect(() => {
    const id = parseAgentDetailView(activeView)
      ?? parseToolDetailView(activeView)
      ?? parseModelDetailView(activeView)
      ?? parseDatasetPreviewView(activeView)
    if (!id) {
      // 对话等非资源标签：不应保留资源选中态
      if (activeView === 'chat' && useResourceStore.getState().selectedResource) {
        selectResource(null)
      }
      return
    }
    const cur = useResourceStore.getState().selectedResource
    if (cur?.id === id) return
    const all = [...dataResources, ...toolResources, ...modelResources, ...agentResources]
    const r = all.find((x) => x.id === id)
    if (r) selectResource(r)
  }, [activeView, dataResources, toolResources, modelResources, agentResources, selectResource])
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    // 惰性初始化：从 localStorage 恢复上次展开的分区，避免刷新后全部重置。
    () => {
      try {
        const raw = localStorage.getItem(EXPANDED_SECTIONS_KEY)
        if (raw) {
          const arr = JSON.parse(raw)
          if (Array.isArray(arr)) return new Set(arr.filter((x) => typeof x === 'string'))
        }
      } catch { /* ignore */ }
      return new Set(['data', 'tool', 'agent'])
    }
  )
  // 各编辑器中的未保存改动，用于在资源树上打标记
  const unsaved = useUnsavedMarks()

  // ── 资源迁移：导入 .sotatool.zip / .sotaagent.zip ──
  const [importing, setImporting] = useState(false)
  const [importKind, setImportKind] = useState<'tool' | 'agent'>('tool')
  const importInputRef = useRef<HTMLInputElement>(null)
  // 导入后的依赖体检结果（缺失时弹窗，一键安装）
  const [depDialog, setDepDialog] = useState<DepDialogState | null>(null)

  const pickImportFile = (kind: 'tool' | 'agent') => {
    setImportKind(kind)
    importInputRef.current?.click()
  }

  const handleImportBundle = async (file: File) => {
    const kind = importKind
    setImporting(true)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('overwrite', 'false')
      const res = await fetch(`/api/migrate/${kind}/import-upload`, { method: 'POST', body: form })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `导入失败 (${res.status})`)

      const targetId: string = kind === 'tool' ? data.tool_id : data.agent_id
      const name: string = data.name || targetId
      if (kind === 'tool') {
        await fetchToolsFromApi()
        await useWorkspaceToolStore.getState().fetchFromApi()
      } else {
        await fetchAgentsFromApi()
      }

      const missing: string[] = data?.deps?.missing || []
      const install: string[] = data?.deps?.install || []
      const missingTools: string[] = data?.missing_tools || []
      const overrideNote = data?.overrode_builtin
        ? '\n注意：已遮蔽同名内置版本（运行时使用本次导入的私有副本）。'
        : ''
      if (missing.length > 0 || missingTools.length > 0) {
        setDepDialog({
          kind,
          targetId,
          toolName: name,
          missing,
          install: install.length ? install : missing,
          missingTools,
          installing: false,
        })
      } else {
        await alertDialog(
          `已导入${kind === 'tool' ? '工具' : 'Agent'}：${name}`,
          `版本 v${data.version || '?'}，依赖齐备，可直接使用。${overrideNote}`
        )
      }
    } catch (e) {
      await alertDialog('导入失败', (e as Error).message)
    } finally {
      setImporting(false)
      if (importInputRef.current) importInputRef.current.value = ''
    }
  }

  const handleInstallDeps = async () => {
    const dlg = depDialog
    if (!dlg || dlg.installing) return
    if (dlg.install.length === 0) {
      setDepDialog({ ...dlg, error: '没有可安装的 Python 依赖（仅缺工具，请先导入工具包）' })
      return
    }
    setDepDialog({ ...dlg, installing: true, error: '', results: undefined })
    try {
      const url = dlg.kind === 'agent'
        ? `/api/migrate/agent/${encodeURIComponent(dlg.targetId)}/install-deps`
        : `/api/tool/${encodeURIComponent(dlg.targetId)}/install-deps`
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dependencies: dlg.install }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `安装失败 (${res.status})`)
      const results: DepResultItem[] = data.results || []
      const failed = results.filter((r) => !r.success)
      setDepDialog({ ...dlg, installing: false, results })
      if (failed.length === 0) {
        if (dlg.kind === 'tool') {
          await fetchToolsFromApi()
          await useWorkspaceToolStore.getState().fetchFromApi()
        } else {
          await fetchAgentsFromApi()
        }
      }
    } catch (e) {
      setDepDialog({ ...dlg, installing: false, error: (e as Error).message })
    }
  }


  // 启动时确保从 API 加载（仅一次，MainLayout 的 fetchAllResources 已经调过）
  useEffect(() => {
    fetchAgentsFromApi()
    fetchToolsFromApi()
    fetchDatasetsFromApi()
    fetchModelsFromApi()
    // 工具空间清单以后端为准（无痕窗口 / 换浏览器时 localStorage 是空的）
    useWorkspaceToolStore.getState().fetchFromApi()
  }, []) // 空依赖，只执行一次

  const toggleSection = (type: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      // 同步持久化：刷新后保持展开/收起状态
      try {
        localStorage.setItem(EXPANDED_SECTIONS_KEY, JSON.stringify([...next]))
      } catch { /* ignore */ }
      return next
    })
  }

  // 工具空间：从 workspace store 读取已加载的工具
  const workspaceTools = useWorkspaceToolStore((s) => s.tools)
  const workspaceRemoveTool = useWorkspaceToolStore((s) => s.removeTool)
  // 公共/系统层 Agent（source≠private）仅超级管理员可见删除入口（真正删除还需
  // act-as 领取系统身份，后端 403 兜底）。
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const workspaceLoadError = useWorkspaceToolStore((s) => s.loadError)

  const getResources = (type: ResourceType): Resource[] => {
    switch (type) {
      case 'data': return dataResources
      case 'tool':
        // 工具空间只显示已加载的工具（从 workspace store）。
        // isUserGenerated 不能硬编码为 true —— workspace 只是"加载清单"，
        // 里面既有内置示例也有用户本地生成的工具。
        // 真实判定要查工具仓库（toolResources，后端按 owner 字段标注）。
        return workspaceTools.map(t => {
          // 用类型谓词收窄：仓库里混着各类型资源，只有 tool 才有 isUserGenerated
          const repoTool = toolResources.find(
            (r): r is ToolResource => r.type === 'tool' && r.id === t.id,
          )
          return {
            id: t.id,
            name: t.name,
            type: 'tool' as const,
            version: repoTool?.version || '0.1.0',
            status: 'active' as const,
            tags: t.tags,
            description: '',
            createdAt: '',
            updatedAt: '',
            isUserGenerated: repoTool?.isUserGenerated ?? false,
            category: 'local' as const,
            inputSpec: { formats: [] },
            outputSpec: { formats: [] },
            dependencies: [],
            runtimeEnv: 'python' as const,
            usageCount: 0,
          }
        })
      case 'model': return modelResources
      case 'agent': return agentResources
      case 'task': return taskResources
      default: return []
    }
  }

  const handleDelete = async (resource: Resource, e: React.MouseEvent) => {
    e.stopPropagation()
    // 工具：从工作空间移除（不删除仓库中的工具）
    if (resource.type === 'tool') {
      const ok = await confirmDialog({
        title: `将「${resource.name}」移出工具空间？`,
        description: '只从当前工具空间移出，工具本身仍保留在工具仓库中，随时可以重新添加。',
        confirmText: '移出',
        danger: true,
      })
      if (!ok) return
      workspaceRemoveTool(resource.id)
      return
    }
    // 任务历史：删除单条执行记录（id 形如 task-3，取数字主键）
    if (resource.type === 'task') {
      const ok = await confirmDialog({
        title: `删除任务记录「${resource.name}」？`,
        description: '仅删除这一条执行历史记录，不影响任务对应的 Agent 或定时任务。',
        confirmText: '删除',
        danger: true,
      })
      if (!ok) return
      try {
        const res = await fetch(`/api/task/${resource.id.replace(/^task-/, '')}`, { method: 'DELETE' })
        if (!res.ok) {
          await alertDialog('删除失败', `HTTP ${res.status}`)
          return
        }
        fetchTasksFromApi()
      } catch { /* ignore */ }
      return
    }
    // 数据：左栏「数据空间」列的是已注册数据集，删除即注销该数据集
    if (resource.type === 'data') {
      const ok = await confirmDialog({
        title: `删除数据集「${resource.name}」？`,
        description: '注册信息与引用将一并移除，托管目录内的数据文件会被清除。',
        confirmText: '删除',
        danger: true,
      })
      if (!ok) return
      try {
        await fetch(`/api/data/${resource.id}`, { method: 'DELETE' })
        fetchDatasetsFromApi()
      } catch { /* ignore */ }
      return
    }
    // 模型：确认后彻底删除
    if (resource.type === 'model') {
      const ok = await confirmDialog({
        title: `删除模型「${resource.name}」？`,
        description: '此操作不可恢复。',
        confirmText: '删除',
        danger: true,
      })
      if (!ok) return
      const BASE_URL = ''
      try {
        await fetch(`${BASE_URL}/api/model/${resource.id}`, { method: 'DELETE' })
        fetchModelsFromApi()
      } catch { /* ignore */ }
      return
    }
    // Agent：确认后删除（数据/模型已在上方分支处理）。后端按命中层删除：
    // 我的副本删除后回落内置；内置 Agent 仅超级管理员可删（非超管看不到
    // 删除按钮，直接请求会被后端 403 拒绝并经下方提示透出）。
    if (resource.type !== 'agent') return
    const confirmed = await confirmDialog({
      title: `删除 Agent「${resource.name}」？`,
      description: '仅作用于个人定制版：我的副本删除后回落到内置，内置原版不受影响。',
      confirmText: '删除',
      danger: true,
    })
    if (!confirmed) return
    const BASE_URL = ''
    try {
      const res = await fetch(`${BASE_URL}/api/agent/${resource.id}`, { method: 'DELETE' })
      if (!res.ok) {
        // 失败必须提示，否则用户只看到列表没变化、不知道原因
        let detail = `HTTP ${res.status}`
        try {
          const body = await res.json()
          detail = body?.detail || detail
        } catch { /* 非 JSON 响应沿用状态码 */ }
        await alertDialog('删除失败', detail)
        return
      }
      fetchAgentsFromApi()
    } catch (err) {
      await alertDialog('删除失败', err instanceof Error ? err.message : String(err))
    }
  }

  const handleResourceClick = (resource: Resource) => {
    selectResource(resource)
    if (resource.type === 'agent') {
      // 每个 Agent 使用独立视图 ID，双击打开各自独立的标签页与界面
      setActiveView(`agent-detail:${resource.id}` as never)
    } else if (resource.type === 'tool') {
      setActiveView(`tool-detail:${resource.id}` as never)
    } else if (resource.type === 'model') {
      // 模型与 Agent/工具对齐：中栏打开独立详情视图，充分利用大空间
      setActiveView(`model-detail:${resource.id}` as never)
    } else if (resource.type === 'data') {
      // 每个数据集独立标签页：可同时打开多个数据集预览，刷新按 ID 恢复
      setActiveView(`data-preview:${resource.id}` as never)
    } else if (!rightPanelOpen) {
      toggleRightPanel()
    }
  }

  return (
    <div data-testid="resource-browser" className="flex flex-col py-2 px-2 gap-0.5">
      {SECTIONS.map((section) => {
        const resources = getResources(section.type)
        const isExpanded = expandedSections.has(section.type)
        const Icon = section.icon

        return (
          <div key={section.type}>
            {/* Section header
                注意：这里必须是 div 而非 button —— header 内部还有
                「创建 Agent/工具/数据集」「工具仓库」等操作按钮，
                button 嵌套 button 是非法 DOM，会触发 React hydration
                错误并导致点击行为异常。 */}
            <div
              data-testid={`resource-section-${section.type}`}
              role="button"
              tabIndex={0}
              onClick={() => toggleSection(section.type)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  toggleSection(section.type)
                }
              }}
              className="flex items-center gap-1.5 w-full h-8 px-1.5 rounded hover:bg-maia-sidebar-hover transition-colors text-[13px] font-medium cursor-pointer select-none"
              title={
                section.type === 'tool'
                  ? '已加载到当前工作区的工具（全部可用工具见「工具仓库」）'
                  : section.type === 'data'
                    ? '已注册的数据集'
                    : undefined
              }
            >
              {isExpanded ? (
                <ChevronDown className="h-3 w-3 text-maia-text-muted" />
              ) : (
                <ChevronRight className="h-3 w-3 text-maia-text-muted" />
              )}
              <Icon className={cn('h-3.5 w-3.5', section.color)} />
              <span className="flex-1 text-left text-maia-text-secondary">
                {section.label}
              </span>
              <span
                data-testid={`resource-count-${section.type}`}
                className="text-[12px] text-maia-text-muted bg-maia-bg rounded-full px-1.5 py-0.5"
              >
                {resources.length}
              </span>
              {section.type === 'agent' && (
                <>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      pickImportFile('agent')
                    }}
                    disabled={importing}
                    className={cn(
                      'flex items-center justify-center h-4 w-4 rounded transition-colors',
                      'hover:bg-emerald-500/10 text-maia-text-muted hover:text-emerald-500',
                      importing && 'opacity-50 cursor-wait'
                    )}
                    title="导入 Agent 包（.sotaagent.zip）"
                  >
                    <Upload className={cn('h-3 w-3', importing && 'animate-pulse')} />
                  </button>
                  <button
                    onClick={async (e) => {
                      e.stopPropagation()
                      if (!useAuthStore.getState().requireLogin()) return
                      // 「+」的语义是新建：必须先把编辑器 store 复位，
                      // 否则残留的 editingAgentId 会让界面继续停留在旧 Agent 的编辑态。
                      const st = useAgentEditorStore.getState()
                      if (st.editingAgentId && st.hasUnsavedChanges()) {
                        const ok = await confirmDialog({
                          title: '丢弃未保存的改动？',
                          description: '当前 Agent 编辑器有未保存的改动，新建将丢弃这些改动。',
                          confirmText: '新建',
                          danger: true,
                        })
                        if (!ok) return
                      }
                      st.reset()
                      useUIStore.getState().setActiveView('agent-editor')
                    }}
                    className="flex items-center justify-center h-4 w-4 rounded hover:bg-maia-accent/10 text-maia-text-muted hover:text-maia-accent transition-colors"
                    title="创建新 Agent"
                  >
                    <Plus className="h-3 w-3" />
                  </button>
                </>
              )}
              {section.type === 'tool' && (
                <>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      pickImportFile('tool')
                    }}
                    disabled={importing}
                    className={cn(
                      'flex items-center justify-center h-4 w-4 rounded transition-colors',
                      'hover:bg-emerald-500/10 text-maia-text-muted hover:text-emerald-500',
                      importing && 'opacity-50 cursor-wait'
                    )}
                    title="导入工具包（.sotatool.zip）"
                  >
                    <Upload className={cn('h-3 w-3', importing && 'animate-pulse')} />
                  </button>
                  <button
                    onClick={async (e) => {
                      e.stopPropagation()
                      if (!useAuthStore.getState().requireLogin()) return
                      // 「+」的语义是新建：必须先把编辑器 store 复位，
                      // 否则残留的 editingToolId 会让界面继续停留在旧工具的编辑态。
                      const st = useToolEditorStore.getState()
                      if (st.editingToolId && st.hasUnsavedChanges()) {
                        const ok = await confirmDialog({
                          title: '丢弃未保存的改动？',
                          description: '当前工具编辑器有未保存的改动，新建将丢弃这些改动。',
                          confirmText: '新建',
                          danger: true,
                        })
                        if (!ok) return
                      }
                      st.reset()
                      useUIStore.getState().setActiveView('tool-editor')
                    }}
                    className="flex items-center justify-center h-4 w-4 rounded hover:bg-maia-accent/10 text-maia-text-muted hover:text-maia-accent transition-colors"
                    title="创建新工具"
                  >
                    <Plus className="h-3 w-3" />
                  </button>
                </>
              )}
              {section.type === 'data' && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    if (!useAuthStore.getState().requireLogin()) return
                    useUIStore.getState().setActiveView('dataset-editor')
                  }}
                  className="flex items-center justify-center h-4 w-4 rounded hover:bg-maia-accent/10 text-maia-text-muted hover:text-maia-accent transition-colors"
                  title="添加数据集"
                >
                  <Plus className="h-3 w-3" />
                </button>
              )}
            </div>

            {/* Section items — indented to align under parent label */}
            {isExpanded && (
              <div>
                {section.type === 'tool' && toolResources.length > resources.length && (
                  <button
                    onClick={() => useUIStore.getState().setActiveView('repository')}
                    className="flex items-center gap-1.5 w-full h-7 pl-7 pr-1.5 rounded text-[12px] text-maia-text-muted hover:text-maia-accent hover:bg-maia-sidebar-hover transition-colors text-left"
                    title="查看全部可用工具"
                  >
                    <Package className="h-3 w-3 shrink-0" />
                    工具仓库另有 {toolResources.length} 个可用工具
                  </button>
                )}
                {resources.map((resource) => (
                  // 条目也必须是 div —— 行内的「删除/移除」按钮若嵌在
                  // <button> 里会触发 React 的非法 DOM 嵌套报错
                  <div
                    key={resource.id}
                    data-testid={`resource-row-${resource.type}-${resource.id}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleResourceClick(resource)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        handleResourceClick(resource)
                      }
                    }}
                    className={cn(
                      'group flex items-center gap-1.5 w-full h-7 pl-7 pr-1.5 rounded text-[13px]',
                      'hover:bg-maia-sidebar-hover transition-colors text-left',
                      'text-maia-text-secondary',
                      selectedResource?.id === resource.id &&
                        'bg-maia-accent-light text-maia-accent font-medium'
                    )}
                  >
                    <span className="truncate flex-1">{resource.name}</span>
                    {/* 未保存改动标记：即使切到其他视图也能看到
                        "这个资源还有改动没保存"（类似 VSCode 标签页的圆点） */}
                    {((resource.type === 'tool' && unsaved.tools.has(resource.id)) ||
                      (resource.type === 'agent' && unsaved.agents.has(resource.id))) && (
                      <span
                        className="h-1.5 w-1.5 rounded-full shrink-0 bg-maia-accent"
                        title="有未保存的改动"
                      />
                    )}
                    {resource.type === 'data' && resource.available === false && (
                      <span
                        className="h-1.5 w-1.5 rounded-full shrink-0 bg-maia-danger"
                        title="数据不在本机（可能注册于其他机器）"
                      />
                    )}
                    {resource.type === 'tool' &&
                      (resource as ToolResource).isUserGenerated && (
                        <span
                          className="h-1.5 w-1.5 rounded-full shrink-0 bg-amber-500"
                          title="用户本地工具"
                        />
                      )}
                    {resource.type === 'agent' &&
                      (resource as AgentResource).source === 'private' && (
                        <span
                          className="h-1.5 w-1.5 rounded-full shrink-0 bg-purple-500"
                          title="我的 Agent（自建或副本）"
                        />
                      )}
                    {resource.type === 'task' && (
                      <span
                        className={
                          'h-1.5 w-1.5 rounded-full shrink-0 ' +
                          ((resource as TaskResource).state === 'running'
                            ? 'bg-amber-400 animate-pulse'
                            : (resource as TaskResource).state === 'failed'
                            ? 'bg-maia-danger'
                            : 'bg-maia-success')
                        }
                        title={(resource as TaskResource).state === 'failed'
                          ? (resource as TaskResource).executionLog[0] || '执行失败' : undefined}
                      />
                    )}
                    {/* Remove button — tool: 从空间移除, data/agent: 删除。
                        内置 Agent 对非超管不渲染删除（内置版仅超级管理员可删） */}
                    {!(resource.type === 'agent' && (resource as AgentResource).source !== 'private' && !isAdmin) && (
                    <button
                      onClick={(e) => handleDelete(resource, e)}
                      className="opacity-0 group-hover:opacity-100 hover:!opacity-100 flex items-center justify-center h-4 w-4 rounded hover:bg-maia-danger-bg text-maia-text-muted hover:text-maia-danger transition-all shrink-0"
                      title={section.type === 'tool' ? '从空间移除' : '删除'}
                    >
                      <Minus className="h-3 w-3" />
                    </button>
                    )}
                  </div>
                ))}
                {resources.length === 0 && (
                  <p className="text-[12px] text-maia-text-muted py-1 px-1.5">
                    {section.type === 'tool' && workspaceLoadError
                      ? '清单加载失败（后端不可达）'
                      : '暂无资源'}
                  </p>
                )}
              </div>
            )}
          </div>
        )
      })}

      {/* 定时任务入口：任务量小、非资源实体，不进 SECTIONS 列表，单独一行 */}
      <div
        data-testid="cron-jobs-entry"
        role="button"
        tabIndex={0}
        onClick={() => setActiveView('cron-jobs')}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setActiveView('cron-jobs')
          }
        }}
        className={cn(
          'flex items-center gap-1.5 w-full h-8 px-1.5 mt-1 rounded hover:bg-maia-sidebar-hover transition-colors text-[13px] font-medium cursor-pointer select-none',
          'border-t border-maia-border pt-1'
        )}
        title="按 cron 计划周期执行工具 / Agent"
      >
        <CalendarClock className="h-3.5 w-3.5 text-cyan-500" />
        <span className="flex-1 text-left text-maia-text-secondary">定时任务</span>
        <ChevronRight className="h-3 w-3 text-maia-text-muted" />
      </div>

      {/* 导入后依赖体检：缺失时弹窗，一键安装 */}
      {depDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-[460px] max-w-[92vw] rounded-lg border border-maia-border bg-maia-surface p-4 shadow-xl">
            <div className="text-sm font-semibold text-maia-text-heading mb-2">
              导入完成，但依赖未齐备
            </div>
            <p className="text-[11px] text-maia-text-secondary mb-2">
              「{depDialog.toolName}」（{depDialog.kind === 'tool' ? '工具' : 'Agent'}）缺少以下内容：
            </p>

            {depDialog.missing.length > 0 && (
              <>
                <div className="text-[11px] text-maia-text-muted mb-1">Python 包</div>
                <ul className="mb-3 max-h-32 overflow-auto text-[11px] font-mono">
                  {depDialog.missing.map((m, i) => (
                    <li key={m} className="flex items-center justify-between gap-2 py-0.5">
                      <span className="text-maia-text-secondary">{m}</span>
                      <span className="text-maia-text-muted">
                        {depDialog.install[i] && depDialog.install[i] !== m ? `→ ${depDialog.install[i]}` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {depDialog.missingTools && depDialog.missingTools.length > 0 && (
              <div className="mb-3 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[11px]">
                <div className="text-amber-600 font-medium mb-1">依赖的工具未安装</div>
                <div className="font-mono text-maia-text-secondary break-all">
                  {depDialog.missingTools.join('、')}
                </div>
                <div className="mt-1 text-[10px] text-maia-text-muted">
                  请先在「工具空间」用 ⬆ 导入对应的 .sotatool.zip，再运行该 Agent。
                </div>
              </div>
            )}

            {depDialog.error && (
              <div className="mb-2 max-h-28 overflow-auto rounded bg-maia-bg/60 p-2 text-[11px] text-maia-danger break-all whitespace-pre-wrap">
                {depDialog.error}
              </div>
            )}

            {depDialog.results && depDialog.results.length > 0 && (
              <div className="mb-2 max-h-32 overflow-auto rounded bg-maia-bg/60 p-2 text-[11px]">
                {depDialog.results.map((r) => (
                  <div key={r.dep} className={r.success ? 'text-emerald-500' : 'text-maia-danger'}>
                    {r.success ? '✓' : '✗'} {r.dep}
                    {r.already ? '（已存在）' : ''}
                    {!r.success && r.reason ? ` — ${String(r.reason).slice(-300)}` : ''}
                  </div>
                ))}
              </div>
            )}

            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setDepDialog(null)}
                className="px-3 py-1 rounded text-[11px] text-maia-text-secondary hover:bg-maia-sidebar-hover"
              >
                {depDialog.results &&
                depDialog.results.every((r) => r.success) &&
                (depDialog.missingTools || []).length === 0
                  ? '完成'
                  : '稍后'}
              </button>
              {depDialog.install.length > 0 && (
                <button
                  onClick={handleInstallDeps}
                  disabled={depDialog.installing}
                  className="px-3 py-1 rounded text-[11px] bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50"
                >
                  {depDialog.installing ? '安装中…' : '一键安装'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 导入文件选择器（隐藏的 input，由两处的 ⬆ 按钮共享） */}
      <input
        ref={importInputRef}
        type="file"
        accept=".zip"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) handleImportBundle(f)
        }}
      />
    </div>
  )
}

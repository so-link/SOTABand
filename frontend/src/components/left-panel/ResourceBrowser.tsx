import { useState, useEffect } from 'react'
import {
  Database,
  Wrench,
  BrainCircuit,
  Bot,
  ListTodo,
  ChevronRight,
  ChevronDown,
  Star,
  Plus,
  Minus,
  Package,
} from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'
import { useUnsavedMarks } from '@/hooks/use-unsaved-marks'
import { cn } from '@/lib/utils'
import type { Resource, ResourceType, ToolResource } from '@/types/resources'

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
  { type: 'task', label: '任务历史', icon: ListTodo, color: 'text-slate-500' },
]

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
  } = useResourceStore()
  const { rightPanelOpen, toggleRightPanel, setActiveView } = useUIStore()
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(['data', 'tool', 'agent'])
  )
  // 各编辑器中的未保存改动，用于在资源树上打标记
  const unsaved = useUnsavedMarks()

  // 启动时确保从 API 加载（仅一次，MainLayout 的 fetchAllResources 已经调过）
  useEffect(() => {
    fetchAgentsFromApi()
    fetchToolsFromApi()
    fetchDatasetsFromApi()
    // 工具空间清单以后端为准（无痕窗口 / 换浏览器时 localStorage 是空的）
    useWorkspaceToolStore.getState().fetchFromApi()
  }, []) // 空依赖，只执行一次

  const toggleSection = (type: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  // 工具空间：从 workspace store 读取已加载的工具
  const workspaceTools = useWorkspaceToolStore((s) => s.tools)
  const workspaceRemoveTool = useWorkspaceToolStore((s) => s.removeTool)

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
      workspaceRemoveTool(resource.id)
      return
    }
    // 数据和 Agent：确认后彻底删除
    if (!confirm(`确定删除 "${resource.name}"？此操作不可恢复。`)) return
    const BASE_URL = ''
    const typePath = resource.type === 'data' ? 'data' : 'agent'
    try {
      await fetch(`${BASE_URL}/api/${typePath}/${resource.id}`, { method: 'DELETE' })
      if (resource.type === 'data') fetchDatasetsFromApi()
      else if (resource.type === 'agent') fetchAgentsFromApi()
    } catch { /* ignore */ }
  }

  const handleResourceClick = (resource: Resource) => {
    selectResource(resource)
    if (resource.type === 'agent') {
      setActiveView('agent-detail')
    } else if (resource.type === 'tool') {
      setActiveView('tool-detail')
    } else if (resource.type === 'data') {
      setActiveView('data-preview')
    } else if (!rightPanelOpen) {
      toggleRightPanel()
    }
  }

  return (
    <div className="flex flex-col py-1.5 px-1 gap-0">
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
              role="button"
              tabIndex={0}
              onClick={() => toggleSection(section.type)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  toggleSection(section.type)
                }
              }}
              className="flex items-center gap-1.5 w-full py-1 px-2 rounded hover:bg-maia-sidebar-hover transition-colors text-[11px] font-medium tracking-wider uppercase cursor-pointer select-none"
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
              <span className="text-[10px] text-maia-text-muted bg-maia-bg rounded-full px-1.5 py-0.5 tracking-tight">
                {resources.length}
              </span>
              {section.type === 'agent' && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    useUIStore.getState().setActiveView('agent-editor')
                  }}
                  className="flex items-center justify-center h-4 w-4 rounded hover:bg-maia-accent/10 text-maia-text-muted hover:text-maia-accent transition-colors"
                  title="创建新 Agent"
                >
                  <Plus className="h-3 w-3" />
                </button>
              )}
              {section.type === 'tool' && (
                <>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      useUIStore.getState().setActiveView('repository')
                    }}
                    className="flex items-center justify-center h-4 w-4 rounded hover:bg-amber-500/10 text-maia-text-muted hover:text-amber-500 transition-colors"
                    title="工具仓库"
                  >
                    <Package className="h-3 w-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      useUIStore.getState().setActiveView('tool-editor')
                    }}
                    className="flex items-center justify-center h-4 w-4 rounded hover:bg-amber-500/10 text-maia-text-muted hover:text-amber-500 transition-colors"
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
                    useUIStore.getState().setActiveView('dataset-editor')
                  }}
                  className="flex items-center justify-center h-4 w-4 rounded hover:bg-blue-500/10 text-maia-text-muted hover:text-blue-500 transition-colors"
                  title="添加数据集"
                >
                  <Plus className="h-3 w-3" />
                </button>
              )}
            </div>

            {/* Section items — indented to align under parent label */}
            {isExpanded && (
              <div>
                {resources.map((resource) => (
                  // 条目也必须是 div —— 行内的「删除/移除」按钮若嵌在
                  // <button> 里会触发 React 的非法 DOM 嵌套报错
                  <div
                    key={resource.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleResourceClick(resource)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        handleResourceClick(resource)
                      }
                    }}
                    style={{ paddingLeft: '52px' }}
                    className={cn(
                      'flex items-center gap-1.5 w-full py-[3px] pr-1.5 rounded text-[11px] tracking-wide',
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
                        className="h-1.5 w-1.5 rounded-full shrink-0 bg-amber-500"
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
                        <span title="用户本地工具">
                          <Star className="h-3 w-3 shrink-0 text-amber-500" />
                        </span>
                      )}
                    {resource.type === 'agent' && (
                      <span className="h-1.5 w-1.5 rounded-full shrink-0 bg-maia-success" />
                    )}
                    {resource.type === 'task' && (
                      <span className="text-[10px] text-maia-text-muted shrink-0 tracking-tight">
                        v{resource.version}
                      </span>
                    )}
                    {/* Remove button — tool: 从空间移除, data/agent: 删除 */}
                    <button
                      onClick={(e) => handleDelete(resource, e)}
                      className="opacity-0 group-hover:opacity-100 hover:!opacity-100 flex items-center justify-center h-4 w-4 rounded hover:bg-red-100 text-maia-text-muted hover:text-red-500 transition-all shrink-0"
                      title={section.type === 'tool' ? '从空间移除' : '删除'}
                    >
                      <Minus className="h-3 w-3" />
                    </button>
                  </div>
                ))}
                {resources.length === 0 && (
                  <p className="text-[10px] text-maia-text-muted py-1 px-1.5 tracking-wide">
                    暂无资源
                  </p>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

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
  FolderPlus,
} from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'
import { useWorkspaceDatasetStore } from '@/stores/workspace-dataset-store'
import { useUnsavedMarks } from '@/hooks/use-unsaved-marks'
import { cn } from '@/lib/utils'
import type { ModelCategory, ModelResource, Resource, ResourceType, ToolResource } from '@/types/resources'

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

const MODEL_CATEGORIES: Array<{ id: ModelCategory; label: string }> = [
  { id: 'trained', label: '已训练模型' },
  { id: 'architecture', label: '模型架构与配置' },
]

export function ResourceBrowser() {
  const {
    dataResources,
    toolResources,
    modelResources,
    modelGroups,
    agentResources,
    taskResources,
    selectedResource,
    selectResource,
    fetchAgentsFromApi,
    fetchToolsFromApi,
    fetchDatasetsFromApi,
    fetchModelsFromApi,
    fetchModelGroups,
    createModelGroup,
    deleteModelGroup,
  } = useResourceStore()
  const { rightPanelOpen, toggleRightPanel, setActiveView } = useUIStore()
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(['data', 'tool', 'agent'])
  )
  const [expandedModelCategories, setExpandedModelCategories] = useState<Set<ModelCategory>>(
    new Set(['trained', 'architecture'])
  )
  const [collapsedModelGroups, setCollapsedModelGroups] = useState<Set<string>>(new Set())
  // 各编辑器中的未保存改动，用于在资源树上打标记
  const unsaved = useUnsavedMarks()

  // 启动时确保从 API 加载（仅一次，MainLayout 的 fetchAllResources 已经调过）
  useEffect(() => {
    fetchAgentsFromApi()
    fetchToolsFromApi()
    fetchDatasetsFromApi()
    fetchModelsFromApi()
    fetchModelGroups()
    // 工具空间清单以后端为准（无痕窗口 / 换浏览器时 localStorage 是空的）
    useWorkspaceToolStore.getState().fetchFromApi()
  }, []) // 空依赖，只执行一次

  const modelSectionOpen = expandedSections.has('model')
  useEffect(() => {
    if (!modelSectionOpen) return
    void fetchModelsFromApi()
    void fetchModelGroups()
    const interval = window.setInterval(() => {
      void fetchModelsFromApi()
      void fetchModelGroups()
    }, 10000)
    return () => window.clearInterval(interval)
  }, [modelSectionOpen, fetchModelsFromApi, fetchModelGroups])

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
  const workspaceRemoveDataset = useWorkspaceDatasetStore((s) => s.removeDataset)

  const handleCreateModelGroup = async (category: ModelCategory, e: React.MouseEvent) => {
    e.stopPropagation()
    const label = MODEL_CATEGORIES.find((item) => item.id === category)?.label
    const name = window.prompt(`在${label}中新建分组`)?.trim()
    if (name && !await createModelGroup(name, category)) window.alert('创建分组失败')
  }

  const handleDeleteModelGroup = async (id: string, name: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!window.confirm(`确定删除空分组“${name}”？`)) return
    const error = await deleteModelGroup(id)
    if (error) window.alert(error)
  }

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
    // 数据：从工作空间移除（不删除仓库中的数据集）
    if (resource.type === 'data') {
      workspaceRemoveDataset(resource.id)
      return
    }
    // 模型：确认后彻底删除
    if (resource.type === 'model') {
      if (!confirm(`确定删除模型 "${resource.name}"？此操作不可恢复。`)) return
      const BASE_URL = ''
      try {
        await fetch(`${BASE_URL}/api/model/${resource.id}`, { method: 'DELETE' })
        fetchModelsFromApi()
      } catch { /* ignore */ }
      return
    }
    // Agent：确认后彻底删除（数据/模型已在上方分支处理）
    if (resource.type !== 'agent') return
    if (!confirm(`确定删除 "${resource.name}"？此操作不可恢复。`)) return
    const BASE_URL = ''
    try {
      await fetch(`${BASE_URL}/api/agent/${resource.id}`, { method: 'DELETE' })
      fetchAgentsFromApi()
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

  const renderResourceRow = (resource: Resource) => (
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
      className={cn(
        'group flex items-center gap-1.5 w-full py-[3px] pr-1.5 pl-[52px] rounded text-[11px] tracking-wide',
        'hover:bg-maia-sidebar-hover transition-colors text-left text-maia-text-secondary',
        selectedResource?.id === resource.id && 'bg-maia-accent-light text-maia-accent font-medium',
      )}
    >
      <span className="truncate flex-1" title={resource.name}>{resource.name}</span>
      {((resource.type === 'tool' && unsaved.tools.has(resource.id)) ||
        (resource.type === 'agent' && unsaved.agents.has(resource.id))) && (
        <span className="h-1.5 w-1.5 rounded-full shrink-0 bg-amber-500" title="有未保存的改动" />
      )}
      {resource.type === 'data' && resource.available === false && (
        <span className="h-1.5 w-1.5 rounded-full shrink-0 bg-maia-danger" title="数据不在本机" />
      )}
      {resource.type === 'model' && resource.runtimeStatus === 'trained' && !resource.artifactAvailable && (
        <span className="h-1.5 w-1.5 rounded-full shrink-0 bg-maia-danger" title="训练产物不完整或不可用" />
      )}
      {resource.type === 'tool' && (resource as ToolResource).isUserGenerated && (
        <span title="用户本地工具"><Star className="h-3 w-3 shrink-0 text-amber-500" /></span>
      )}
      {resource.type === 'agent' && (
        <span className="h-1.5 w-1.5 rounded-full shrink-0 bg-maia-success" />
      )}
      {resource.type === 'task' && (
        <span className="text-[10px] text-maia-text-muted shrink-0 tracking-tight">v{resource.version}</span>
      )}
      <button
        onClick={(e) => handleDelete(resource, e)}
        className="opacity-0 group-hover:opacity-100 focus:opacity-100 flex items-center justify-center h-4 w-4 rounded hover:bg-red-100 text-maia-text-muted hover:text-red-500 transition-all shrink-0"
        title={resource.type === 'tool' ? '从空间移除' : '删除'}
        aria-label={`${resource.type === 'tool' ? '移除' : '删除'}${resource.name}`}
      >
        <Minus className="h-3 w-3" />
      </button>
    </div>
  )

  const renderModelCategory = (category: ModelCategory, label: string) => {
    const resources = (modelResources as ModelResource[]).filter((item) =>
      (item.modelCategory || (item.runtimeStatus === 'trained' && item.artifactAvailable !== false ? 'trained' : 'architecture')) === category,
    )
    const groups = modelGroups.filter((item) => item.category === category)
    const groupNames = new Set([...groups.map((item) => item.name), ...resources.map((item) => item.groupPath || '其它模型')])
    groupNames.add('其它模型')
    const names = [...groupNames].sort((a, b) => a === '其它模型' ? -1 : b === '其它模型' ? 1 : a.localeCompare(b, 'zh-CN'))
    const expanded = expandedModelCategories.has(category)
    return (
      <div key={category}>
        <div
          role="button"
          tabIndex={0}
          onClick={() => setExpandedModelCategories((previous) => {
            const next = new Set(previous)
            if (next.has(category)) next.delete(category)
            else next.add(category)
            return next
          })}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click() } }}
          className="flex items-center gap-1.5 pl-5 pr-2 py-1.5 text-[11px] text-maia-text-secondary hover:bg-maia-sidebar-hover cursor-pointer"
        >
          {expanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
          <span className="flex-1 min-w-0 truncate" title={label}>{label}</span>
          <span className="text-[10px] text-maia-text-muted">{resources.length}</span>
          <button
            onClick={(e) => void handleCreateModelGroup(category, e)}
            className="flex items-center justify-center h-5 w-5 rounded hover:bg-emerald-500/10 text-maia-text-muted hover:text-emerald-500"
            title={`在${label}中新建分组`}
            aria-label={`在${label}中新建分组`}
          >
            <FolderPlus className="h-3.5 w-3.5" />
          </button>
        </div>
        {expanded && names.map((name) => {
          const group = groups.find((item) => item.name === name)
          const groupKey = `${category}:${name}`
          const collapsed = collapsedModelGroups.has(groupKey)
          const items = resources.filter((item) => (item.groupPath || '其它模型') === name)
          return (
            <div key={groupKey}>
              <div
                role="button"
                tabIndex={0}
                onClick={() => setCollapsedModelGroups((previous) => {
                  const next = new Set(previous)
                  if (next.has(groupKey)) next.delete(groupKey)
                  else next.add(groupKey)
                  return next
                })}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click() } }}
                className="group flex items-center gap-1 pl-8 pr-2 py-1 text-[10px] text-maia-text-secondary hover:bg-maia-sidebar-hover cursor-pointer"
              >
                {collapsed ? <ChevronRight className="h-3 w-3 shrink-0" /> : <ChevronDown className="h-3 w-3 shrink-0" />}
                <span className="flex-1 min-w-0 truncate" title={name}>{name}</span>
                <span className="text-maia-text-muted">{items.length}</span>
                {group && !group.system && (
                  <button
                    onClick={(e) => void handleDeleteModelGroup(group.id, name, e)}
                    className="opacity-0 group-hover:opacity-100 focus:opacity-100 flex items-center justify-center h-4 w-4 rounded text-maia-text-muted hover:text-red-500"
                    title={`删除分组 ${name}`}
                    aria-label={`删除分组 ${name}`}
                  >
                    <Minus className="h-3 w-3" />
                  </button>
                )}
              </div>
              {!collapsed && items.map(renderResourceRow)}
            </div>
          )
        })}
      </div>
    )
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
                {section.type === 'model'
                  ? MODEL_CATEGORIES.map(({ id, label }) => renderModelCategory(id, label))
                  : resources.map(renderResourceRow)}
                {section.type !== 'model' && resources.length === 0 && (
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

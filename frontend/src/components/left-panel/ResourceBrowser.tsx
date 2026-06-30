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
} from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
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
  } = useResourceStore()
  const { rightPanelOpen, toggleRightPanel, setActiveView } = useUIStore()
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(['data', 'tool', 'agent'])
  )

  // 启动时从 API 加载真实 Agent 列表
  useEffect(() => {
    fetchAgentsFromApi()
    fetchToolsFromApi()
  }, [fetchAgentsFromApi, fetchToolsFromApi])

  const toggleSection = (type: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  const getResources = (type: ResourceType): Resource[] => {
    switch (type) {
      case 'data': return dataResources
      case 'tool': return toolResources
      case 'model': return modelResources
      case 'agent': return agentResources
      case 'task': return taskResources
      default: return []
    }
  }

  const handleResourceClick = (resource: Resource) => {
    selectResource(resource)
    if (resource.type === 'agent') {
      setActiveView('agent-detail')
    } else if (resource.type === 'tool') {
      setActiveView('tool-detail')
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
            {/* Section header */}
            <button
              onClick={() => toggleSection(section.type)}
              className="flex items-center gap-1.5 w-full py-1 px-2 rounded hover:bg-maia-sidebar-hover transition-colors text-[11px] font-medium tracking-wider uppercase"
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
              )}
            </button>

            {/* Section items — indented to align under parent label */}
            {isExpanded && (
              <div>
                {resources.map((resource) => (
                  <button
                    key={resource.id}
                    onClick={() => handleResourceClick(resource)}
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
                  </button>
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

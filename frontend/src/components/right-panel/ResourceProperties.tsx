import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import type { Resource, DataResource, ToolResource, ModelResource, AgentResource, TaskResource } from '@/types/resources'
import { Star, Activity } from 'lucide-react'

interface ResourcePropertiesProps {
  resource: Resource
}

export function ResourceProperties({ resource }: ResourcePropertiesProps) {
  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <h3 className="text-xs font-semibold text-gray-700">资源详情</h3>
      </div>

      <div className="p-3 space-y-3 text-sm">
        {/* Common fields */}
        <Property label="名称" value={resource.name} />
        <Property label="类型">
          <Badge variant="accent">{typeLabel(resource.type)}</Badge>
        </Property>
        <Property label="版本" value={`v${resource.version}`} />
        <Property label="状态">
          <StatusBadge status={resource.status} />
        </Property>
        <Property label="描述" value={resource.description} />

        <Separator />

        <Property label="ID" value={resource.id} mono />
        <Property label="创建时间" value={formatDate(resource.createdAt)} />
        <Property label="更新时间" value={formatDate(resource.updatedAt)} />

        {resource.tags.length > 0 && (
          <>
            <Separator />
            <div>
              <span className="text-[10px] text-gray-400 uppercase block mb-1">标签</span>
              <div className="flex flex-wrap gap-1">
                {resource.tags.map((tag) => (
                  <Badge key={tag} variant="default" className="text-[10px]">{tag}</Badge>
                ))}
              </div>
            </div>
          </>
        )}

        {/* Type-specific fields */}
        {resource.type === 'data' && <DataFields resource={resource as DataResource} />}
        {resource.type === 'tool' && <ToolFields resource={resource as ToolResource} />}
        {resource.type === 'model' && <ModelFields resource={resource as ModelResource} />}
        {resource.type === 'agent' && <AgentFields resource={resource as AgentResource} />}
        {resource.type === 'task' && <TaskFields resource={resource as TaskResource} />}
      </div>
    </div>
  )
}

function Property({ label, value, mono, children }: { label: string; value?: string; mono?: boolean; children?: React.ReactNode }) {
  return (
    <div>
      <span className="text-[10px] text-gray-400 uppercase">{label}</span>
      {children || (
        <p className={`text-sm ${mono ? 'font-mono text-xs text-gray-500' : 'text-gray-800'}`}>
          {value || '—'}
        </p>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'default'> = {
    active: 'success',
    deprecated: 'warning',
    archived: 'default',
    building: 'warning',
    deployed: 'success',
    offline: 'danger',
    registered: 'default',
    done: 'success',
    running: 'warning',
    failed: 'danger',
    pending: 'default',
    healthy: 'success',
    degraded: 'warning',
  }
  return <Badge variant={map[status] || 'default'}>{status}</Badge>
}

function DataFields({ resource }: { resource: DataResource }) {
  return (
    <>
      <Separator />
      <Property label="格式" value={resource.format.toUpperCase()} />
      <Property label="文件大小" value={resource.fileSize > 1048576 ? `${(resource.fileSize / 1048576).toFixed(1)} MB` : `${(resource.fileSize / 1024).toFixed(1)} KB`} />
      <Property label="来源" value={resource.source === 'upload' ? '上传' : resource.source === 'generated' ? '生成' : '外部'} />
      {resource.qualityScore && <Property label="质量评分" value={`${resource.qualityScore}/100`} />}
      {resource.lineage.length > 0 && (
        <Property label="血缘" value={`${resource.lineage.length} 个上游数据`} />
      )}
    </>
  )
}

function ToolFields({ resource }: { resource: ToolResource }) {
  return (
    <>
      <Separator />
      <div className="flex items-center gap-2">
        <Property label="分类" value={resource.category === 'builtin' ? '内置工具' : resource.category === 'local' ? '本地工具' : '模型封装'} />
        {resource.isUserGenerated && <span title="用户探索生成"><Star className="h-3.5 w-3.5 text-amber-500" /></span>}
      </div>
      <Property label="运行环境" value={resource.runtimeEnv} />
      <Property label="使用次数" value={String(resource.usageCount)} />
      {resource.dependencies.length > 0 && (
        <Property label="依赖" value={resource.dependencies.join(', ')} />
      )}
    </>
  )
}

function ModelFields({ resource }: { resource: ModelResource }) {
  return (
    <>
      <Separator />
      <Property label="框架" value={resource.framework} />
      <Property label="模型类型" value={resource.modelType} />
      {resource.paramCount && (
        <Property label="参数量" value={resource.paramCount > 1e9 ? `${(resource.paramCount / 1e9).toFixed(1)}B` : `${(resource.paramCount / 1e6).toFixed(0)}M`} />
      )}
      {resource.accuracy && <Property label="精度" value={`${(resource.accuracy * 100).toFixed(1)}%`} />}
      <Property label="部署状态" value={resource.deploymentStatus === 'deployed' ? '已部署' : resource.deploymentStatus === 'offline' ? '离线' : '已注册'} />
    </>
  )
}

function AgentFields({ resource }: { resource: AgentResource }) {
  return (
    <>
      <Separator />
      <Property label="角色" value={resource.role === 'interactive' ? '交互Agent' : resource.role === 'task' ? '任务Agent' : resource.role === 'orchestrator' ? '编排Agent' : '观测Agent'} />
      <Property label="健康状态">
        <div className="flex items-center gap-1">
          <Activity className="h-3 w-3 text-emerald-500" />
          <span className="text-sm text-gray-800">{resource.healthStatus}</span>
        </div>
      </Property>
      {resource.requiredTools.length > 0 && (
        <Property label="依赖工具" value={`${resource.requiredTools.length} 个`} />
      )}
    </>
  )
}

function TaskFields({ resource }: { resource: TaskResource }) {
  const stateLabels: Record<string, string> = { pending: '等待中', running: '运行中', done: '已完成', failed: '失败', cancelled: '已取消' }
  return (
    <>
      <Separator />
      <Property label="任务状态" value={stateLabels[resource.state] || resource.state} />
      <Property label="进度" value={`${resource.progress}% (${resource.currentStep}/${resource.totalSteps})`} />
      {resource.startedAt && <Property label="开始时间" value={formatDate(resource.startedAt)} />}
      {resource.finishedAt && <Property label="完成时间" value={formatDate(resource.finishedAt)} />}
      <Property label="关联资源" value={`${resource.relatedResourceIds.length} 个`} />
    </>
  )
}

function typeLabel(type: string): string {
  const map: Record<string, string> = {
    data: '数据',
    tool: '工具',
    model: '模型',
    agent: 'Agent',
    user: '用户',
    task: '任务',
  }
  return map[type] || type
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Clock, Activity } from 'lucide-react'
import type { TaskResource } from '@/types/resources'

interface TaskStatusProps {
  task: TaskResource
}

export function TaskStatus({ task }: TaskStatusProps) {
  const stateLabels: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    done: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  const stateColors: Record<string, 'warning' | 'success' | 'danger' | 'default'> = {
    pending: 'default',
    running: 'warning',
    done: 'success',
    failed: 'danger',
    cancelled: 'default',
  }

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <h3 className="text-xs font-semibold text-gray-700">任务状态</h3>
      </div>

      <div className="p-3 space-y-3 text-sm">
        <div>
          <span className="text-[10px] text-gray-400 uppercase">任务</span>
          <p className="text-sm font-medium">{task.name}</p>
        </div>

        <div>
          <span className="text-[10px] text-gray-400 uppercase">状态</span>
          <div className="flex items-center gap-1.5 mt-0.5">
            <Badge variant={stateColors[task.state]}>{stateLabels[task.state] || task.state}</Badge>
            {task.state === 'running' && <Activity className="h-3 w-3 text-amber-500 animate-pulse" />}
          </div>
        </div>

        {/* Progress bar */}
        <div>
          <span className="text-[10px] text-gray-400 uppercase">
            进度 {task.progress}%
          </span>
          <div className="mt-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                task.state === 'done' ? 'bg-emerald-500' :
                task.state === 'failed' ? 'bg-red-500' :
                'bg-blue-500'
              }`}
              style={{ width: `${task.progress}%` }}
            />
          </div>
          <p className="text-xs text-gray-400 mt-0.5">
            步骤 {task.currentStep}/{task.totalSteps}
          </p>
        </div>

        <Separator />

        {task.startedAt && (
          <div className="flex items-center gap-1.5">
            <Clock className="h-3 w-3 text-gray-400" />
            <span className="text-xs text-gray-500">
              开始: {new Date(task.startedAt).toLocaleTimeString('zh-CN')}
            </span>
          </div>
        )}

        {task.finishedAt && (
          <div className="flex items-center gap-1.5">
            <Clock className="h-3 w-3 text-gray-400" />
            <span className="text-xs text-gray-500">
              完成: {new Date(task.finishedAt).toLocaleTimeString('zh-CN')}
            </span>
          </div>
        )}

        <Separator />

        <div>
          <span className="text-[10px] text-gray-400 uppercase">关联资源</span>
          <p className="text-xs text-gray-500">{task.relatedResourceIds.length} 个资源</p>
        </div>

        {/* Execution log */}
        {task.executionLog.length > 0 && (
          <>
            <Separator />
            <div>
              <span className="text-[10px] text-gray-400 uppercase block mb-1">执行日志</span>
              <div className="bg-gray-900 rounded-md p-2 max-h-40 overflow-auto">
                {task.executionLog.map((line, i) => (
                  <p key={i} className="text-[10px] text-emerald-400 font-mono leading-relaxed">
                    {line}
                  </p>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

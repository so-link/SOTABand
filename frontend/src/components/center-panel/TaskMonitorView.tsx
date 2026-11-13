import { Radio } from 'lucide-react'
import { ViewPlaceholder } from './ViewPlaceholder'

export function TaskMonitorView() {
  return (
    <div data-testid="task-monitor-view" className="h-full">
      <ViewPlaceholder
        icon={Radio}
        title="任务监控"
        desc="任务提交执行后，此处实时展示 DAG 执行状态、日志流和资源占用情况。"
        steps={[
          { title: 'DAG 状态', desc: '节点状态实时更新' },
          { title: '日志流', desc: '滚动展示执行日志' },
          { title: '资源占用', desc: 'GPU / CPU / 内存' },
        ]}
      />
    </div>
  )
}

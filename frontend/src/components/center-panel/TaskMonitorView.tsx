import { Activity } from 'lucide-react'

export function TaskMonitorView() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-6">
      <Activity className="h-12 w-12 text-gray-300 mb-4" />
      <h2 className="text-lg font-semibold text-gray-600 mb-2">任务监控</h2>
      <p className="text-sm text-gray-400 max-w-md">
        任务提交执行后，此处将实时展示 DAG 执行状态、日志流和资源占用情况。
      </p>
      <div className="mt-4 grid grid-cols-3 gap-3 text-xs text-gray-400">
        <div className="bg-gray-50 rounded p-2">
          <div className="font-medium text-gray-600 mb-1">📊 DAG 状态</div>
          节点状态实时更新
        </div>
        <div className="bg-gray-50 rounded p-2">
          <div className="font-medium text-gray-600 mb-1">📋 日志流</div>
          实时滚动执行日志
        </div>
        <div className="bg-gray-50 rounded p-2">
          <div className="font-medium text-gray-600 mb-1">💻 资源占用</div>
          GPU/CPU/内存监控
        </div>
      </div>
    </div>
  )
}

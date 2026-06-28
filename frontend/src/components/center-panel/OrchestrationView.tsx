import { GitBranch } from 'lucide-react'

export function OrchestrationView() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-6">
      <GitBranch className="h-12 w-12 text-gray-300 mb-4" />
      <h2 className="text-lg font-semibold text-gray-600 mb-2">编排编辑器</h2>
      <p className="text-sm text-gray-400 max-w-md">
        此处将展示任务编排的 Markdown 描述与 DAG 可视化图。
        你可以拖拽节点调整流程、修改参数、替换 Agent。
      </p>
      <div className="mt-4 flex gap-8 text-xs text-gray-400">
        <div className="text-center">
          <div className="text-2xl mb-1">📝</div>
          <div>Markdown 编辑</div>
        </div>
        <div className="text-center">
          <div className="text-2xl mb-1">🔗</div>
          <div>DAG 可视化</div>
        </div>
        <div className="text-center">
          <div className="text-2xl mb-1">🔄</div>
          <div>双向同步</div>
        </div>
      </div>
      <p className="text-xs text-gray-400 mt-4 bg-gray-100 rounded px-3 py-1.5">
        💡 提示：在对话视图中描述复杂任务，编排 Agent 会自动生成编排文件
      </p>
    </div>
  )
}

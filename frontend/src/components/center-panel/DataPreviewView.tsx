import { BarChart3 } from 'lucide-react'

export function DataPreviewView() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-6">
      <BarChart3 className="h-12 w-12 text-gray-300 mb-4" />
      <h2 className="text-lg font-semibold text-gray-600 mb-2">数据预览</h2>
      <p className="text-sm text-gray-400 max-w-md">
        在左侧面板中选择数据文件后，此处将展示数据波形图、频谱图、表格等交互式预览。
      </p>
      <p className="text-xs text-gray-400 mt-4 bg-gray-100 rounded px-3 py-1.5">
        💡 提示：在对话视图中点击数据预览卡片也可以跳转到此视图
      </p>
    </div>
  )
}

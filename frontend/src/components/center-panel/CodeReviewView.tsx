import { FileCode } from 'lucide-react'

export function CodeReviewView() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-6">
      <FileCode className="h-12 w-12 text-gray-300 mb-4" />
      <h2 className="text-lg font-semibold text-gray-600 mb-2">代码核验</h2>
      <p className="text-sm text-gray-400 max-w-md">
        当资源构建器自动生成新工具代码后，此处将展示代码编辑器与沙箱测试结果，
        供你审查、修改和批准后注册为本地工具。
      </p>
      <div className="mt-4 grid grid-cols-3 gap-3 text-xs text-gray-400">
        <div className="bg-gray-50 rounded p-2">
          <div className="font-medium text-gray-600 mb-1">1. 语法检查</div>
          自动验证语法和依赖
        </div>
        <div className="bg-gray-50 rounded p-2">
          <div className="font-medium text-gray-600 mb-1">2. 沙箱测试</div>
          安全环境预跑代码
        </div>
        <div className="bg-gray-50 rounded p-2">
          <div className="font-medium text-gray-600 mb-1">3. 批准注册</div>
          核验通过后入库
        </div>
      </div>
    </div>
  )
}

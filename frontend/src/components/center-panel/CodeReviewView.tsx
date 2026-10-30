import { FileCode } from 'lucide-react'
import { ViewPlaceholder } from './ViewPlaceholder'

export function CodeReviewView() {
  return (
    <ViewPlaceholder
      icon={FileCode}
      title="代码核验"
      desc="资源构建器生成新工具代码后，此处展示代码编辑器与沙箱测试结果，供你审查、修改并批准注册为本地工具。"
      steps={[
        { title: '语法检查', desc: '自动验证语法与依赖' },
        { title: '沙箱测试', desc: '隔离环境预跑代码' },
        { title: '批准注册', desc: '核验通过后入库' },
      ]}
    />
  )
}

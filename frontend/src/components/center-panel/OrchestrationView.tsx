import { GitBranch } from 'lucide-react'
import { ViewPlaceholder } from './ViewPlaceholder'

export function OrchestrationView() {
  return (
    <ViewPlaceholder
      icon={GitBranch}
      title="编排编辑器"
      desc="此处展示任务编排的 Markdown 描述与 DAG 可视化图。你可以拖拽节点调整流程、修改参数、替换 Agent。"
      steps={[
        { title: 'Markdown 编辑', desc: '以文本描述编排流程' },
        { title: 'DAG 可视化', desc: '节点与依赖关系图谱' },
        { title: '双向同步', desc: '文本与图实时联动' },
      ]}
      hint="在对话视图里描述复杂任务，编排 Agent 会自动生成编排文件"
    />
  )
}

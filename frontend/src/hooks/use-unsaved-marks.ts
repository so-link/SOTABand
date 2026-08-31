import { useMemo } from 'react'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useAgentEditorStore } from '@/stores/agent-editor-store'

export interface UnsavedMarks {
  /** 有未保存改动的工具 id 集合 */
  tools: Set<string>
  /** 有未保存改动的 Agent id 集合 */
  agents: Set<string>
  /** 合计数量，便于在标题栏显示 */
  count: number
}

/**
 * 汇总当前各编辑器中的「未保存改动」状态。
 *
 * 编辑器可以关闭、可以切到别的视图，但编辑内容仍保留在 store 中。
 * 若只在编辑器头部显示状态，使用者切走后就看不到"这里还有东西没保存"，
 * 容易误以为已经保存。这里把状态提取出来，供左侧资源树打圆点标记。
 *
 * 说明：zustand 返回的对象每次渲染都是新引用，因此依赖内部的
 * 具体字段（saveState / editingXxxId）而非整个对象，避免无限重渲染。
 */
export function useUnsavedMarks(): UnsavedMarks {
  const toolSaveState = useToolEditorStore((s) => s.saveState)
  const toolEditingId = useToolEditorStore((s) => s.editingToolId)
  const agentSaveState = useAgentEditorStore((s) => s.saveState)
  const agentEditingId = useAgentEditorStore((s) => s.editingAgentId)

  return useMemo(() => {
    const tools = new Set<string>()
    const agents = new Set<string>()
    if (toolEditingId && toolSaveState === 'dirty') tools.add(toolEditingId)
    if (agentEditingId && agentSaveState === 'dirty') agents.add(agentEditingId)
    return { tools, agents, count: tools.size + agents.size }
  }, [toolSaveState, toolEditingId, agentSaveState, agentEditingId])
}

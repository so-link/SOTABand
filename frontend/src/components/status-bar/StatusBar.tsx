import { useEffect, useState } from 'react'
import { Wrench, Database, Terminal } from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'
import { useUIStore, parseAgentDetailView } from '@/stores/ui-store'
import { APP_VERSION } from '@/lib/utils'

const ETF_TIPS = [
  '直接输入 ETF 代码（如 510300）或名称（如沪深300）分析目标价位',
  '支持指定预测周期（5-60 个交易日），如「分析 510500 未来 30 天」',
  '工作流程步骤会实时显示：解析参数 → 调用工具 → 整理数据',
  '置信度由信号一致性、数据充分性、波动率、预测周期综合计算',
  '目标价位分保守 / 中性 / 乐观三档，仅供参考，不构成投资建议',
]

const AGENT_TIPS = [
  '点击上方「需求 / MD 文档 / 代码」可查看 Agent 的完整定义与源码',
  '对话历史会自动保存，刷新页面后自动恢复',
  '首次使用请先点击右上角「启动」运行 Agent 进程',
]

const GENERAL_TIPS = [
  '左侧「资源空间」可管理 Agent / 工具 / 数据 / 模型',
  '双击资源可在中栏打开独立标签页',
  '刷新页面后，打开的标签与对话历史会自动恢复',
]

export function StatusBar() {
  const toolCount = useWorkspaceToolStore((s) => s.tools.length)
  const dataCount = useResourceStore((s) => s.dataResources.length)
  const activeView = useUIStore((s) => s.activeView)

  const agentId = parseAgentDetailView(activeView)
  const isEtf = !!agentId && agentId.includes('etf')
  const tips = isEtf ? ETF_TIPS : agentId ? AGENT_TIPS : GENERAL_TIPS
  const [tipIndex, setTipIndex] = useState(0)

  // 切换视图时重置索引，避免越界
  useEffect(() => { setTipIndex(0) }, [isEtf, !!agentId])
  useEffect(() => {
    const t = setInterval(() => setTipIndex((i) => (i + 1) % tips.length), 30000)
    return () => clearInterval(t)
  }, [tips.length])

  return (
    <footer className="flex h-7 items-center justify-between gap-4 px-3 text-[12px] shrink-0 select-none glass border-t border-white/[0.03]">
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="h-1.5 w-1.5 rounded-full bg-maia-success shrink-0" />
        <span className="text-maia-text-muted truncate" title="系统运行状态：绿色表示服务在线可用">
          就绪
        </span>
      </div>

      {/* 中间：动态 tip 轮播条 */}
      <div className="flex items-center gap-1.5 text-maia-text-muted truncate">
        <button
          onClick={() => setTipIndex((i) => (i - 1 + tips.length) % tips.length)}
          className="hover:text-maia-text leading-none"
          title="上一条"
        >
          ‹
        </button>
        <span className="truncate" key={tipIndex}>{tips[tipIndex]}</span>
        <button
          onClick={() => setTipIndex((i) => (i + 1) % tips.length)}
          className="hover:text-maia-text leading-none"
          title="下一条"
        >
          ›
        </button>
      </div>

      <div className="flex items-center gap-3 shrink-0">
        <span
          className="inline-flex items-center gap-1.5 text-maia-text-muted"
          title="已加载到当前工作区的工具数（工具仓库另有全部可用工具）"
        >
          <Wrench className="h-3.5 w-3.5 text-maia-accent/70" />
          TOOLS:<span className="font-mono text-maia-text-secondary">{toolCount}</span>
        </span>
        <span
          className="inline-flex items-center gap-1.5 text-maia-text-muted"
          title="已注册的数据集数量"
        >
          <Database className="h-3.5 w-3.5 text-maia-accent/70" />
          DATA:<span className="font-mono text-maia-text-secondary">{dataCount}</span>
        </span>
      </div>
    </footer>
  )
}

// 工具规范文档的结构化视图（节点树 + 人话摘要）
//
// 用途：让非专业使用者不必通读 87 行技术文档，
// 先看懂"这个工具做什么、要什么、给什么"，并能针对某一段提出修改意见。

export type SpecNodeType =
  | 'overview'    // 功能概述
  | 'input'       // 输入规范
  | 'output'      // 输出规范
  | 'dependency'  // 依赖环境
  | 'flow'        // 执行流程
  | 'error'       // 错误处理
  | 'history'     // 版本历史
  | 'section'     // 其他

export interface SpecNode {
  id: string           // 稳定 id（标题不变则 id 不变）
  title: string
  level: number        // Markdown 标题层级
  node_type: SpecNodeType
  content_md: string   // 该节点原始 MD 片段
  line_start: number
  line_end: number
  summary: string      // 人话摘要（可能为空，此时前端回退显示 title）
  children: string[]
}

export interface SpecOutline {
  tool_id: string
  nodes: SpecNode[]
  summary: Record<string, unknown>
  version: number
  warning?: string
}

/** 文档中的 Markdown 表格结构（供表单化编辑） */
export interface SpecTable {
  has_table: boolean
  node_id?: string
  node_title?: string
  header: string[]
  rows: string[][]
  align: string[]
}

export interface TableCellUpdateResult {
  node_id: string
  node_md: string
  updated_md: string
  outline: SpecOutline
  saved: boolean
}

export interface RefineResult {
  node_id: string
  node_md: string
  updated_md: string
  outline: SpecOutline
  diff: { before: string; after: string }
  impact_hint: string
  saved: boolean
}

// 节点类型的展示配置：图标 + 配色 + 通俗说明
export const NODE_TYPE_META: Record<
  SpecNodeType,
  { label: string; color: string; hint: string }
> = {
  overview:   { label: '做什么',   color: 'text-blue-400',   hint: '这个工具的目的' },
  input:      { label: '要什么',   color: 'text-amber-400',  hint: '你需要提供的信息' },
  output:     { label: '给什么',   color: 'text-green-400',  hint: '工具会返回什么' },
  dependency: { label: '靠什么',   color: 'text-purple-400', hint: '运行所需的环境' },
  flow:       { label: '怎么算',   color: 'text-cyan-400',   hint: '具体处理步骤' },
  error:      { label: '出错时',   color: 'text-red-400',    hint: '出问题怎么办' },
  history:    { label: '变更记录', color: 'text-maia-text-muted', hint: '版本历史' },
  section:    { label: '段落',     color: 'text-maia-text-muted', hint: '' },
}

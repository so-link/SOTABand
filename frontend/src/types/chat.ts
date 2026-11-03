// ============================================================
// 对话类型定义
// ============================================================

/** 消息角色 */
export type MessageRole = 'user' | 'agent' | 'system'

/** 内联卡片类型 */
export type CardType =
  | 'data-preview'
  | 'tool-match'
  | 'tool-confirm'
  | 'mode-selection'
  | 'code-review'
  | 'execution-progress'
  | 'result-summary'
  | 'orchestration-preview'
  | 'create-tool'
  | 'add-workspace-tool'
  | 'platform-confirm'
  | 'platform-result'

/** 内联卡片数据 */
export interface InlineCard {
  id: string
  type: CardType
  title: string
  summary: string
  data: Record<string, unknown>  // 卡片类型相关的具体数据
}

/** 附件信息 */
export interface FileAttachment {
  id: string
  fileName: string
  filePath: string
  fileSize: number
  format: string
}

/** 单条消息 */
export interface Message {
  id: string
  role: MessageRole
  content: string
  timestamp: string
  cards?: InlineCard[]
  attachments?: FileAttachment[]
}

/** 创建消息的输入 */
export interface CreateMessageInput {
  content: string
  attachments?: FileAttachment[]
  workspaceToolIds?: string[]
  sessionId?: string
  /** 隐藏轮次标记："platform_receipt" = 前端代发平台操作回执，UI 不显示用户消息 */
  kind?: string
  /** 网关回执 {tool, status, summary, result, error}，供助手解读 */
  receipt?: Record<string, unknown>
}

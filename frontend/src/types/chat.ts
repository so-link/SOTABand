// ============================================================
// 对话类型定义
// ============================================================

/** 消息角色 */
export type MessageRole = 'user' | 'agent' | 'system'

/** 内联卡片类型 */
export type CardType =
  | 'data-preview'
  | 'tool-match'
  | 'code-review'
  | 'execution-progress'
  | 'result-summary'
  | 'orchestration-preview'
  | 'create-tool'

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
}

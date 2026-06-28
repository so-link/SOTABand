// ============================================================
// 服务接口定义（后端就绪后只需实现这些接口）
// ============================================================

import type { Message, CreateMessageInput } from '@/types/chat'
import type { FileTreeNode } from '@/types/workspace'
import type { Resource, ResourceType } from '@/types/resources'

/** 对话服务接口 */
export interface IChatService {
  /** 发送消息，返回异步生成器模拟流式响应 */
  sendMessage(input: CreateMessageInput): AsyncGenerator<Message>
}

/** 文件服务接口 */
export interface IFileService {
  /** 获取工作区间文件树 */
  getTree(): Promise<FileTreeNode>
  /** 搜索文件 */
  search(query: string): Promise<FileTreeNode[]>
  /** 上传文件 */
  upload(files: File[]): Promise<FileTreeNode[]>
}

/** 资源服务接口 */
export interface IResourceService {
  /** 按类型列出资源 */
  list(type: ResourceType): Promise<Resource[]>
  /** 获取单个资源详情 */
  get(id: string): Promise<Resource | null>
  /** 搜索资源 */
  search(query: string): Promise<Resource[]>
}

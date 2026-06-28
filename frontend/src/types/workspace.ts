// ============================================================
// 工作区间类型定义
// ============================================================

/** 文件树节点类型 */
export type FileNodeType = 'file' | 'directory'

/** 文件类型图标标识 */
export type FileCategory =
  | 'eeg'       // 脑电数据
  | 'image'     // 图像
  | 'table'     // 表格数据
  | 'text'      // 文本
  | 'model'     // 模型权重
  | 'archive'   // 压缩包
  | 'result'    // 结果文件
  | 'folder'
  | 'unknown'

/** 文件树节点 */
export interface FileTreeNode {
  id: string
  name: string
  type: FileNodeType
  category: FileCategory
  path: string
  format?: string         // 文件扩展名
  size?: number           // bytes, 仅文件
  children?: FileTreeNode[]
  expanded?: boolean
}

/** 工作区间状态 */
export interface WorkspaceState {
  rootPath: string
  name: string
  selectedFiles: string[]  // 选中的文件 ID 列表
}

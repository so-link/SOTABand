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
  /**
   * 文件访问凭证（服务端 file 接口按 id 解析，浏览器不再持有路径）。
   * 数据集目录节点存 'dataset:<id>'，其文件子节点用 datasetRelPath 定位。
   */
  fileId?: string
  /** 数据集内相对路径（仅 fileId 为 dataset:<id> 的子节点） */
  datasetRelPath?: string
  format?: string         // 文件扩展名
  size?: number           // bytes, 仅文件
  children?: FileTreeNode[]
  expanded?: boolean
}

/** 文件内容 URL：一切预览/下载都按 file_id 走。 */
export function fileUrl(fileId: string): string {
  return `/api/file/${encodeURIComponent(fileId)}`
}

/** 数据集原文件 URL（dataset id + 数据集内相对路径）。 */
export function datasetFileUrl(datasetId: string, relPath: string): string {
  return `/api/data/${encodeURIComponent(datasetId)}/raw?path=${encodeURIComponent(relPath)}`
}

/** 从节点解析可访问的内容 URL；不可访问（缺凭证）返回 null。 */
export function nodeFileUrl(n: FileTreeNode): string | null {
  if (n.type !== 'file') return null
  if (n.fileId?.startsWith('dataset:')) {
    return n.datasetRelPath ? datasetFileUrl(n.fileId.slice('dataset:'.length), n.datasetRelPath) : null
  }
  return n.fileId ? fileUrl(n.fileId) : null
}

/** 数据集目录节点对应的数据集 id（非数据集节点返回 null）。 */
export function datasetIdOf(n: FileTreeNode): string | null {
  return n.fileId?.startsWith('dataset:') ? n.fileId.slice('dataset:'.length) : null
}

/** 工作区间状态 */
export interface WorkspaceState {
  rootPath: string
  name: string
  selectedFiles: string[]  // 选中的文件 ID 列表
}

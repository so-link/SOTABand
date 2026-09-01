// ============================================================
// 工作区间 → 聊天输入框 的拖拽契约
// ============================================================

/**
 * 拖拽数据的专属 MIME 类型。
 *
 * 不能用通用 application/json：dataTransfer 里可能混入浏览器或其他标签页
 * 拖来的任意内容（选中文本、链接、文件...），只有专属 MIME 才能可靠判定
 * 「这是工作区间的文件节点」，避免把无关 JSON 当成附件。
 */
export const WORKSPACE_FILE_MIME = 'application/x-sotaband-workspace-file'

/** 拖拽载荷：只带附件所需字段，不塞整棵子树（children 可能很大） */
export interface WorkspaceFileDragPayload {
  id: string
  name: string
  path: string
  size: number
  format: string
}

/**
 * 拖起文件节点时写入 dataTransfer。
 *
 * 同时写一份 text/plain 作为兜底：拖到外部编辑器 / 普通文本框时
 * 至少能落下文件路径，而不是什么都不发生。
 */
export function setWorkspaceFileDragData(
  dt: DataTransfer,
  payload: WorkspaceFileDragPayload,
): void {
  dt.setData(WORKSPACE_FILE_MIME, JSON.stringify(payload))
  dt.setData('text/plain', payload.path)
  dt.effectAllowed = 'copy'
}

/**
 * dragover 阶段浏览器禁止读取 getData（保护模式），只能查 types。
 * 用于在悬停时判断是否值得高亮为「可放置」。
 */
export function canAcceptWorkspaceFileDrag(dt: DataTransfer): boolean {
  return Array.from(dt.types).includes(WORKSPACE_FILE_MIME)
}

/**
 * 读取拖拽载荷。不是工作区间文件、或数据损坏时返回 null。
 * 只能在 drop 阶段调用（dragover 阶段 getData 恒为空串）。
 */
export function readWorkspaceFileDragData(
  dt: DataTransfer,
): WorkspaceFileDragPayload | null {
  const raw = dt.getData(WORKSPACE_FILE_MIME)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<WorkspaceFileDragPayload>
    if (!parsed.id || !parsed.name || !parsed.path) return null
    return {
      id: parsed.id,
      name: parsed.name,
      path: parsed.path,
      size: typeof parsed.size === 'number' ? parsed.size : 0,
      format: parsed.format || '',
    }
  } catch {
    // dataTransfer 里的 JSON 可能被截断或串改，解析失败按「没有附件」处理
    return null
  }
}

/** 判断这次拖拽是否有可接收的内容：工作区间文件，或操作系统拖入的文件 */
export function hasDroppableContent(dt: DataTransfer): boolean {
  return canAcceptWorkspaceFileDrag(dt) || Array.from(dt.types).includes('Files')
}

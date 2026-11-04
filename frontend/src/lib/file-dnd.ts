import type { DragEvent } from 'react'
import type { FileTreeNode } from '@/types/workspace'

/** 平台文件凭证形态（f_+12hex）；数据集节点是 dataset:<id>，不能当文件参数用 */
const FILE_ID_RE = /^f_[0-9a-f]{12}$/

/** 从拖拽事件中解析平台文件节点（必须是 f_ 形态 file_id）。
 *  拖拽源是「我的文件」树：同时写入 application/json（完整节点）与
 *  text/plain（file_id），任一命中即可。 */
export function extractDraggedNode(e: DragEvent): FileTreeNode | null {
  const jsonData = e.dataTransfer.getData('application/json')
  if (jsonData) {
    try {
      const node = JSON.parse(jsonData) as FileTreeNode
      if (node?.fileId && FILE_ID_RE.test(node.fileId)) return node
    } catch {
      /* JSON 损坏时继续走文本兜底，不要直接放弃 */
    }
  }
  const textData = e.dataTransfer.getData('text/plain')?.trim()
  if (textData && FILE_ID_RE.test(textData)) {
    return { id: textData, name: textData, type: 'file', category: 'unknown', fileId: textData }
  }
  return null
}

function collectFileNodes(node: FileTreeNode | null, out: FileTreeNode[] = []): FileTreeNode[] {
  if (!node) return out
  if (node.type === 'file') out.push(node)
  node.children?.forEach((child) => collectFileNodes(child, out))
  return out
}

/** OS 文件拖入：先上传到「我的文件」，再把新增文件作为引用记入对话 */
export async function attachDroppedOsFiles(
  files: FileList,
  addPathRef: (ref: { name: string; fileId: string }) => void,
): Promise<void> {
  const { useFileTreeStore } = await import('@/stores/file-tree-store')
  const beforeIds = new Set(collectFileNodes(useFileTreeStore.getState().root).map((n) => n.id))
  await useFileTreeStore.getState().uploadFiles(files)
  // uploadFiles 只负责入库不返回新节点，故用「上传前后的差集」取回新增项
  const added = collectFileNodes(useFileTreeStore.getState().root).filter(
    (n) => !beforeIds.has(n.id) && n.fileId && FILE_ID_RE.test(n.fileId),
  )
  added.forEach((node) => addPathRef({ name: node.name, fileId: node.fileId as string }))
}

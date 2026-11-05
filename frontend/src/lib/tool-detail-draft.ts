/**
 * 工具详情页的「用户态」草稿：参数输入值 + 未保存的代码修改。
 *
 * 为什么需要：`CenterPanel` 只渲染激活视图，切换页签会把 `ToolDetailView`
 * 整个卸载，组件里的 useState 随之销毁 —— 用户刚填的参数、改到一半的代码
 * 看起来"被清空/重置"。
 *
 * 生命周期：**关闭页签即清空**（由 `ui-store.closeView` 调 `clearToolDraft`），
 * 只用来跨"页签切换 / 页面刷新"保活，不作为长期存储。
 */

export interface ToolDetailDraft {
  formValues?: Record<string, string>
  editedCode?: string
  codeModified?: boolean
  savedAt?: number
}

const PREFIX = 'sotaband_tool_draft:'
/** 超过这个长度的代码不落盘：localStorage 常只有 ~5MB，写失败会连参数一起丢 */
const CODE_MAX_CHARS = 200_000

export function loadToolDraft(toolId: string): ToolDetailDraft | null {
  try {
    const raw = localStorage.getItem(PREFIX + toolId)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as ToolDetailDraft) : null
  } catch {
    return null
  }
}

export function saveToolDraft(toolId: string, draft: ToolDetailDraft): void {
  try {
    const payload: ToolDetailDraft = { ...draft, savedAt: Date.now() }
    if (payload.editedCode && payload.editedCode.length > CODE_MAX_CHARS) {
      delete payload.editedCode
      delete payload.codeModified
    }
    localStorage.setItem(PREFIX + toolId, JSON.stringify(payload))
  } catch {
    /* 配额不足等异常不影响主流程 */
  }
}

export function clearToolDraft(toolId: string): void {
  try {
    localStorage.removeItem(PREFIX + toolId)
  } catch {
    /* ignore */
  }
}

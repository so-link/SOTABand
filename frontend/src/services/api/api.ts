/** API 列表服务 — 获取系统注册的 API（供 Agent/Tool 编辑器 @ 补全） */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

export const apiApi = {
  /** 列出所有已注册的系统 API */
  async list(): Promise<{ apis: Array<Record<string, unknown>> }> {
    const res = await fetch(`${BASE_URL}/api/apis/list`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },
}

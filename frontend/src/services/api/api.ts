/** API 列表服务 — 获取系统注册的 API（供 Agent/Tool 编辑器 @ 补全） */
import { apiGet } from './client'

export const apiApi = {
  /** 列出所有已注册的系统 API */
  async list(): Promise<{ apis: Array<Record<string, unknown>> }> {
    return apiGet<{ apis: Array<Record<string, unknown>> }>('/api/apis/list')
  },
}

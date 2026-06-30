/** Agent CRUD API 服务 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

export const agentApi = {
  /** 自然语言 → MD 规范文档 */
  async generateSpec(description: string): Promise<{ spec_md: string }> {
    const res = await fetch(`${BASE_URL}/api/agent/generate-spec`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /** MD 规范文档 → Python 代码 + 沙箱结果 */
  async generateCode(specMd: string): Promise<{
    code: string
    sandbox_results: Record<string, unknown>
  }> {
    // 简单提取 agent-id
    const idMatch = specMd.match(/^id:\s*(.+)$/m)
    const nameMatch = specMd.match(/^name:\s*(.+)$/m)
    const roleMatch = specMd.match(/^role:\s*(.+)$/m)

    const res = await fetch(`${BASE_URL}/api/agent/generate-code`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        specMd,
        agentId: idMatch?.[1]?.trim() || 'custom-agent',
        agentName: nameMatch?.[1]?.trim() || 'Custom Agent',
        role: roleMatch?.[1]?.trim() || 'task',
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /** 注册 Agent */
  async register(
    specMd: string,
    code: string
  ): Promise<{ agent_id: string; entry: Record<string, unknown> }> {
    const idMatch = specMd.match(/^id:\s*(.+)$/m)
    const nameMatch = specMd.match(/^name:\s*(.+)$/m)
    const roleMatch = specMd.match(/^role:\s*(.+)$/m)

    const res = await fetch(`${BASE_URL}/api/agent/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        specMd,
        code,
        agentId: idMatch?.[1]?.trim() || 'custom-agent',
        agentName: nameMatch?.[1]?.trim() || 'Custom Agent',
        role: roleMatch?.[1]?.trim() || 'task',
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /** 列出所有 Agent */
  async list(): Promise<{ agents: Array<Record<string, unknown>> }> {
    const res = await fetch(`${BASE_URL}/api/agent/list`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },
}

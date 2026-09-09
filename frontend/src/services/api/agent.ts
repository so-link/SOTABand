/** Agent CRUD API 服务 */
const BASE_URL = ''

export const agentApi = {
  async generateSpec(description: string): Promise<{ spec_md: string }> {
    const res = await fetch(`${BASE_URL}/api/agent/generate-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  async generateCode(specMd: string): Promise<{ code: string; sandbox_results: Record<string, unknown> }> {
    const idMatch = specMd.match(/^id:\s*(.+)$/m)
    const nameMatch = specMd.match(/^name:\s*(.+)$/m)
    const roleMatch = specMd.match(/^role:\s*(.+)$/m)
    const res = await fetch(`${BASE_URL}/api/agent/generate-code`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        specMd, agentId: idMatch?.[1]?.trim() || 'custom-agent',
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

  async register(specMd: string, code: string, demandDesc: string = ''): Promise<{ agent_id: string; entry: Record<string, unknown> }> {
    const idMatch = specMd.match(/^id:\s*(.+)$/m)
    const nameMatch = specMd.match(/^name:\s*(.+)$/m)
    const roleMatch = specMd.match(/^role:\s*(.+)$/m)
    const res = await fetch(`${BASE_URL}/api/agent/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        specMd, code, agentId: idMatch?.[1]?.trim() || 'custom-agent',
        agentName: nameMatch?.[1]?.trim() || 'Custom Agent',
        role: roleMatch?.[1]?.trim() || 'task', demandDesc,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  async list(): Promise<{ agents: Array<Record<string, unknown>> }> {
    const res = await fetch(`${BASE_URL}/api/agent/list`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },

  async get(agentId: string): Promise<{
    spec_md: string; code: string; has_demand: boolean; demand_md: string
  } & Record<string, unknown>> {
    const res = await fetch(`${BASE_URL}/api/agent/${encodeURIComponent(agentId)}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },

  /** 保存手工编辑后的 MD 规范文档 */
  async saveSpec(agentId: string, specMd: string): Promise<{ saved: string }> {
    const res = await fetch(`${BASE_URL}/api/agent/${encodeURIComponent(agentId)}/save-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_md: specMd }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /** 保存手工微调后的 Agent 代码 */
  async saveCode(agentId: string, code: string): Promise<{ saved: string }> {
    const res = await fetch(`${BASE_URL}/api/agent/${encodeURIComponent(agentId)}/save-code`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /**
   * 代码 → 文档反向同步：
   * 使用者手工微调代码后，让 AI 把改动同步回 MD 规范文档。
   */
  async syncSpecFromCode(params: {
    agent_id: string
    code: string
    original_code: string
    current_spec: string
  }): Promise<{ spec_md: string }> {
    const res = await fetch(`${BASE_URL}/api/agent/${encodeURIComponent(params.agent_id)}/sync-spec-from-code`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: params.agent_id,
        code: params.code,
        original_code: params.original_code,
        current_spec: params.current_spec,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },
}

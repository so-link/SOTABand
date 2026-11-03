/** Agent CRUD API 服务 —— 全部走统一 client（`./client`）。 */
import { apiGet, apiPost } from './client'

/** 从 MD 规范里抽取 id / name / role（三个写接口都要用）。 */
function extractAgentMeta(specMd: string) {
  const idMatch = specMd.match(/^id:\s*(.+)$/m)
  const nameMatch = specMd.match(/^name:\s*(.+)$/m)
  const roleMatch = specMd.match(/^role:\s*(.+)$/m)
  return {
    agentId: idMatch?.[1]?.trim() || 'custom-agent',
    agentName: nameMatch?.[1]?.trim() || 'Custom Agent',
    role: roleMatch?.[1]?.trim() || 'task',
  }
}

export const agentApi = {
  async generateSpec(description: string, role?: string): Promise<{ spec_md: string }> {
    return apiPost<{ spec_md: string }>('/api/agent/generate-spec', { description, role })
  },

  async generateCode(specMd: string): Promise<{ code: string; sandbox_results: Record<string, unknown> }> {
    const { agentId, agentName, role } = extractAgentMeta(specMd)
    return apiPost<{ code: string; sandbox_results: Record<string, unknown> }>(
      '/api/agent/generate-code',
      { specMd, agentId, agentName, role }
    )
  },

  /**
   * 注册 Agent。
   *
   * baseAgentId：编辑已发布 Agent 时传入原 Agent id，作为注册的权威身份。
   * 不传时退回 MD 里的 id（新建 Agent 的旧行为）——否则重新生成文档
   * 改掉了 frontmatter 的 id，就会多出一个重复 Agent。
   */
  async register(specMd: string, code: string, demandDesc: string = '', baseAgentId?: string): Promise<{ agent_id: string; entry: Record<string, unknown>; updated?: boolean; version?: string; previous_version?: string }> {
    const { agentId, agentName, role } = extractAgentMeta(specMd)
    return apiPost<{ agent_id: string; entry: Record<string, unknown>; updated?: boolean; version?: string; previous_version?: string }>(
      '/api/agent/register',
      { specMd, code, agentId, agentName, role, demandDesc, ...(baseAgentId ? { baseAgentId } : {}) }
    )
  },

  async list(): Promise<{ agents: Array<Record<string, unknown>> }> {
    return apiGet<{ agents: Array<Record<string, unknown>> }>('/api/agent/list')
  },

  async get(agentId: string): Promise<{
    spec_md: string; code: string; has_demand: boolean; demand_md: string
  } & Record<string, unknown>> {
    return apiGet(`/api/agent/${encodeURIComponent(agentId)}`)
  },

  /** 保存手工编辑后的 MD 规范文档 */
  async saveSpec(agentId: string, specMd: string): Promise<{ saved: string }> {
    return apiPost<{ saved: string }>(
      `/api/agent/${encodeURIComponent(agentId)}/save-spec`,
      { spec_md: specMd }
    )
  },

  /** 保存手工微调后的 Agent 代码 */
  async saveCode(agentId: string, code: string): Promise<{ saved: string }> {
    return apiPost<{ saved: string }>(
      `/api/agent/${encodeURIComponent(agentId)}/save-code`,
      { code }
    )
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
    return apiPost<{ spec_md: string }>(
      `/api/agent/${encodeURIComponent(params.agent_id)}/sync-spec-from-code`,
      {
        agent_id: params.agent_id,
        code: params.code,
        original_code: params.original_code,
        current_spec: params.current_spec,
      }
    )
  },
}

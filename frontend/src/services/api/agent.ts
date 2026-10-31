/** Agent CRUD API 服务 */
const BASE_URL = ''

/** 通用 SSE 解析：逐 event 分发，返回 done 事件的 data */
async function _consumeSse(
  res: Response,
  onDelta?: (token: string) => void,
  onReasoning?: (token: string) => void,
): Promise<Record<string, unknown>> {
  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')
  const decoder = new TextDecoder()
  let buffer = '', currentEvent = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n'); buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('event: ')) { currentEvent = line.slice(7).trim() }
      else if (line.startsWith('data: ')) {
        let data: Record<string, unknown>
        try { data = JSON.parse(line.slice(6)) } catch { continue }
        if (currentEvent === 'delta' && onDelta && typeof data.token === 'string') {
          onDelta(data.token)
        } else if (currentEvent === 'reasoning' && onReasoning && typeof data.token === 'string') {
          onReasoning(data.token)
        } else if (currentEvent === 'error') {
          throw new Error(String(data.detail || '生成失败'))
        } else if (currentEvent === 'done') {
          return data
        }
        currentEvent = ''
      } else if (line === '') { currentEvent = '' }
    }
  }
  throw new Error('流意外结束，未收到 done 事件')
}

export const agentApi = {
  async generateSpec(
    description: string,
    role?: string,
    onDelta?: (token: string) => void,
    onReasoning?: (token: string) => void,
  ): Promise<{ spec_md: string }> {
    const res = await fetch(`${BASE_URL}/api/agent/generate-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, role }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    const data = await _consumeSse(res, onDelta, onReasoning)
    return data as { spec_md: string }
  },

  async generateCode(
    specMd: string,
    onDelta?: (token: string) => void,
    onReasoning?: (token: string) => void,
  ): Promise<{ code: string; sandbox_results: Record<string, unknown> }> {
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
    const data = await _consumeSse(res, onDelta, onReasoning)
    return data as { code: string; sandbox_results: Record<string, unknown> }
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
   * 自动调试（SSE 流）：试运行 → LLM 分析 → 改代码 → 再试运行。
   * 事件回调与工具自动调试同构（debug_start/round_start/exec_result/
   * thinking/thinking_stream/analysis_result/llm_error/code_updated/stopped/done）。
   */
  async autoDebug(params: {
    specMd: string
    agentId: string
    agentName: string
    role: string
    code: string
    testInputs: Record<string, unknown>
    onEvent: (type: string, data: Record<string, unknown>) => void
    signal?: AbortSignal
  }): Promise<void> {
    const form = new FormData()
    form.append('spec_md', params.specMd)
    form.append('agent_id', params.agentId)
    form.append('agent_name', params.agentName)
    form.append('role', params.role)
    form.append('code', params.code)
    form.append('test_input_json', JSON.stringify(params.testInputs))

    const res = await fetch(`${BASE_URL}/api/agent/auto-debug`, { method: 'POST', body: form, signal: params.signal })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('No response body')

    // 监听 AbortSignal → 立即取消 reader，强制关闭 SSE 连接
    const onAbort = () => reader.cancel()
    params.signal?.addEventListener('abort', onAbort)

    try {
      const decoder = new TextDecoder()
      let buffer = '', currentEvent = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n'); buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('event: ')) { currentEvent = line.slice(7).trim() }
          else if (line.startsWith('data: ')) {
            try { params.onEvent(currentEvent || 'message', JSON.parse(line.slice(6))) } catch { /* skip */ }
            currentEvent = ''
          } else if (line === '') { currentEvent = '' }
        }
      }
    } finally {
      params.signal?.removeEventListener('abort', onAbort)
    }
  },

  async stopAutoDebug(agentId: string) {
    const res = await fetch(`${BASE_URL}/api/agent/auto-debug/stop`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId }),
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

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

/** 带超时的 fetch */
async function fetchWithTimeout(url: string, options: RequestInit, timeoutMs = 60000) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(url, { ...options, signal: controller.signal })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw new Error('请求超时，请重试')
    throw e
  } finally {
    clearTimeout(timer)
  }
}

function extractIdName(md: string) {
  const idMatch = md.match(/^id:\s*(.+)$/m)
  const nameMatch = md.match(/^name:\s*(.+)$/m)
  return {
    toolId: idMatch?.[1]?.trim() || 'custom-tool',
    toolName: nameMatch?.[1]?.trim() || 'Custom Tool',
  }
}

export const toolApi = {
  async generateSpec(description: string) {
    return fetchWithTimeout(`${BASE_URL}/api/tool/generate-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description }),
    }, 60000)
  },

  async generateCode(specMd: string) {
    const { toolId, toolName } = extractIdName(specMd)
    return fetchWithTimeout(`${BASE_URL}/api/tool/generate-code`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specMd, toolId, toolName }),
    }, 120000) as Promise<{ code: string; test_data: Record<string, unknown> }>
  },

  async testCode(specMd: string, code: string) {
    const { toolId, toolName } = extractIdName(specMd)
    return fetchWithTimeout(`${BASE_URL}/api/tool/test`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specMd, toolId, toolName, code }),
    }, 30000) as Promise<{ code: string; sandbox_results: Record<string, unknown>; test_data: Record<string, unknown> }>
  },

  async register(specMd: string, code: string, testData: Record<string, unknown>) {
    const { toolId, toolName } = extractIdName(specMd)
    return fetchWithTimeout(`${BASE_URL}/api/tool/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specMd, code, toolId, toolName, testData }),
    }, 30000) as Promise<{ tool_id: string; entry: Record<string, unknown>; sandbox_results: Record<string, unknown> }>
  },

  async list() {
    return fetchWithTimeout(`${BASE_URL}/api/tool/list`, {}, 10000) as Promise<{ tools: Array<Record<string, unknown>> }>
  },
}

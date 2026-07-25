/**
 * API 基础 URL — 始终指向 Vite 开发服务器（通过 Vite proxy → 后端 8001）
 */
const BASE_URL = ''

function extractIdName(md: string) {
  const idMatch = md.match(/^id:\s*(.+)$/m)
  const nameMatch = md.match(/^name:\s*(.+)$/m)
  return { toolId: idMatch?.[1]?.trim() || 'custom-tool', toolName: nameMatch?.[1]?.trim() || 'Custom Tool' }
}

export const toolApi = {
  async generateSpec(description: string, referenceCode: string = '') {
    const res = await fetch(`${BASE_URL}/api/tool/generate-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, reference_code: referenceCode }),
    })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }
    return res.json() as Promise<{ spec_md: string }>
  },

  async generateCode(specMd: string) {
    const { toolId, toolName } = extractIdName(specMd)
    const res = await fetch(`${BASE_URL}/api/tool/generate-code`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specMd, toolId, toolName }),
    })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }
    return res.json() as Promise<{ code: string; params: Array<Record<string, unknown>> }>
  },

  async testWithInput(specMd: string, code: string, testInputs: Record<string, string>, files?: File[], signal?: AbortSignal) {
    const { toolId, toolName } = extractIdName(specMd)
    const form = new FormData()
    form.append('spec_md', specMd); form.append('tool_id', toolId); form.append('tool_name', toolName)
    form.append('code', code); form.append('test_input_json', JSON.stringify(testInputs))
    if (files) files.forEach((f) => form.append('files', f))
    const res = await fetch(`${BASE_URL}/api/tool/test`, { method: 'POST', body: form, signal })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('No response body')

    const onAbort = () => reader.cancel()
    signal?.addEventListener('abort', onAbort)

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
            try {
              const data = JSON.parse(line.slice(6))
              if (currentEvent === 'result' || currentEvent === 'message') {
                return data as { stdout: string; stderr: string; exit_code: number; success: boolean }
              }
            } catch { /* skip */ }
            currentEvent = ''
          } else if (line === '') { currentEvent = '' }
        }
      }
      throw new Error('No result received')
    } finally {
      signal?.removeEventListener('abort', onAbort)
    }
  },

  async uploadTestFile(file: File) {
    const form = new FormData(); form.append('file', file)
    const res = await fetch(`${BASE_URL}/api/tool/upload-test-file`, { method: 'POST', body: form })
    if (!res.ok) throw new Error('Upload failed')
    return res.json() as Promise<{ file_path: string; file_name: string }>
  },

  async autoDebug(specMd: string, code: string, testInputs: Record<string, string>, files: File[] | undefined, onEvent: (type: string, data: Record<string, unknown>) => void, signal?: AbortSignal) {
    const { toolId, toolName } = extractIdName(specMd)
    const form = new FormData()
    form.append('spec_md', specMd); form.append('tool_id', toolId); form.append('tool_name', toolName)
    form.append('code', code); form.append('test_input_json', JSON.stringify(testInputs))
    if (files) files.forEach((f) => form.append('files', f))

    const res = await fetch(`${BASE_URL}/api/tool/auto-debug`, { method: 'POST', body: form, signal })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('No response body')

    // 监听 AbortSignal → 立即取消 reader，强制关闭 SSE 连接
    const onAbort = () => reader.cancel()
    signal?.addEventListener('abort', onAbort)

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
            try { onEvent(currentEvent || 'message', JSON.parse(line.slice(6))) } catch { /* skip */ }
            currentEvent = ''
          } else if (line === '') { currentEvent = '' }
        }
      }
    } finally {
      signal?.removeEventListener('abort', onAbort)
    }
  },

  async register(specMd: string, code: string, testInputs: Record<string, string>, demandDesc: string = '', referenceCode: string = '') {
    const { toolId, toolName } = extractIdName(specMd)
    const res = await fetch(`${BASE_URL}/api/tool/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specMd, code, toolId, toolName, testData: testInputs, demandDesc, referenceCode }),
    })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }
    return res.json() as Promise<{ tool_id: string; entry: Record<string, unknown> }>
  },

  async list() {
    const res = await fetch(`${BASE_URL}/api/tool/list`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json() as Promise<{ tools: Array<Record<string, unknown>> }>
  },
}

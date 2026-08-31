import type {
  SpecOutline, RefineResult, SpecTable, TableCellUpdateResult,
} from '@/types/spec-outline'

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
    return res.json() as Promise<{ spec_md: string; tags: string[] }>
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

  async stopAutoDebug(specMd: string) {
    const { toolId } = extractIdName(specMd)
    const res = await fetch(`${BASE_URL}/api/tool/auto-debug/stop`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool_id: toolId }),
    })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }
    return res.json()
  },

  async register(specMd: string, code: string, testInputs: Record<string, string>, demandDesc: string = '', referenceCode: string = '', tags: string[] = []) {
    const { toolId, toolName } = extractIdName(specMd)
    const res = await fetch(`${BASE_URL}/api/tool/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specMd, code, toolId, toolName, testData: testInputs, demandDesc, referenceCode, tags }),
    })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }
    return res.json() as Promise<{ tool_id: string; entry: Record<string, unknown> }>
  },

  async list() {
    const res = await fetch(`${BASE_URL}/api/tool/list`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json() as Promise<{ tools: Array<Record<string, unknown>> }>
  },

  /** 解析规范文档结构，返回节点树（withSummary=true 时同时生成人话摘要） */
  async getSpecOutline(toolId: string, specMd: string = '', withSummary: boolean = false): Promise<SpecOutline> {
    const res = await fetch(`${BASE_URL}/api/tool/${encodeURIComponent(toolId)}/spec-outline`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_md: specMd, with_summary: withSummary }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /**
   * 精化文档中的单个节点。
   * 相比整体重新生成：Token 消耗低、不会改动使用者没提到的段落。
   */
  async refineSpecNode(params: {
    toolId: string
    nodeId: string
    feedback: string
    specMd?: string
    save?: boolean
  }): Promise<RefineResult> {
    const res = await fetch(`${BASE_URL}/api/tool/${encodeURIComponent(params.toolId)}/refine-spec-node`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_id: params.nodeId,
        feedback: params.feedback,
        spec_md: params.specMd || '',
        save: params.save ?? false,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /**
   * 修改表格中的单个单元格（零延迟，不调用 LLM）。
   * 适用于「改参数默认值/类型/必填」这类确定性改动。
   */
  async updateTableCell(params: {
    toolId: string
    nodeId: string
    rowIndex: number
    column: string
    value: string
    specMd?: string
    save?: boolean
  }): Promise<TableCellUpdateResult> {
    const res = await fetch(`${BASE_URL}/api/tool/${encodeURIComponent(params.toolId)}/update-table-cell`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_id: params.nodeId,
        row_index: params.rowIndex,
        column: params.column,
        value: params.value,
        spec_md: params.specMd || '',
        save: params.save ?? false,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /** 读取文档中的表格结构，供前端渲染可编辑表单 */
  async getSpecTable(toolId: string, specMd: string = ''): Promise<SpecTable> {
    const res = await fetch(`${BASE_URL}/api/tool/${encodeURIComponent(toolId)}/spec-table`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_md: specMd, with_summary: false }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  /**
   * 流式精化文档节点（SSE）。
   *
   * 该模型思考阶段可长达 10~16 秒，期间若无任何反馈会被认为"卡住"。
   * 流式至少能在生成阶段实时显示内容，并配合计时器给出进度感。
   *
   * @param onToken 每次收到增量文本时回调
   */
  async refineSpecNodeStream(params: {
    toolId: string
    nodeId: string
    feedback: string
    specMd?: string
    save?: boolean
    signal?: AbortSignal
  }, onToken?: (t: string) => void): Promise<RefineResult> {
    const res = await fetch(`${BASE_URL}/api/tool/${encodeURIComponent(params.toolId)}/refine-spec-node-stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_id: params.nodeId,
        feedback: params.feedback,
        spec_md: params.specMd || '',
        save: params.save ?? false,
      }),
      signal: params.signal,
    })
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let done: RefineResult | null = null
    let errorMsg = ''

    // SSE 帧以空行分隔，需自行按 \n\n 切分并解析 event:/data:
    while (true) {
      const { value, done: finished } = await reader.read()
      if (finished) break
      buffer += decoder.decode(value, { stream: true })

      let idx: number
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        let event = 'message'
        let data = ''
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (!data) continue
        try {
          const payload = JSON.parse(data)
          if (event === 'token') onToken?.(payload.t ?? '')
          else if (event === 'done') done = payload as RefineResult
          else if (event === 'error') errorMsg = payload.message || '精化失败'
        } catch { /* 忽略非法帧 */ }
      }
    }

    if (errorMsg) throw new Error(errorMsg)
    if (!done) throw new Error('流式返回未收到完成事件')
    return done
  },

  /** 保存手工编辑后的 MD 规范文档 */
  async saveSpec(toolId: string, specMd: string): Promise<{ saved: string }> {
    const res = await fetch(`${BASE_URL}/api/tool/${encodeURIComponent(toolId)}/save-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_md: specMd }),
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
    tool_id: string
    code: string
    original_code: string
    current_spec: string
  }) {
    const res = await fetch(`${BASE_URL}/api/tool/${encodeURIComponent(params.tool_id)}/sync-spec-from-code`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tool_id: params.tool_id,
        code: params.code,
        original_code: params.original_code,
        current_spec: params.current_spec,
      }),
    })
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail) }
    return res.json() as Promise<{ spec_md: string }>
  },
}

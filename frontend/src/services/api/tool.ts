import type {
  SpecOutline, RefineResult, SpecTable, TableCellUpdateResult,
} from '@/types/spec-outline'
import { apiFetchRaw, apiGet, apiPost, apiUpload, assertOk } from './client'

/**
 * 工具相关 API。
 *
 * ⚠️ 全部走统一 client（`./client`）—— 它统一处理 `credentials: 'include'`、
 * 401 全局回调、FormData 的 Content-Type、以及把错误转成带 HTTP 语义的
 * `ApiError`。**不要再出现裸 `fetch`**：认证落地后漏掉 `credentials` 会导致
 * 请求永远被当作未登录。
 *
 * 两个 SSE 接口（`testWithInput` / `autoDebug` / `refineSpecNodeStream`）用
 * `apiFetchRaw` —— 它们需要拿 `res.body` 的 reader，不能先解析成 JSON。
 */

/** 一个引用方（Agent 或其它工具） */
export interface ToolReferenceItem {
  id: string
  name: string
  role?: string
  /** 引用途径：flow（Agent 流程/API）、text（正文【【】】）、code（_call_tool 调用） */
  via: string[]
  /** 是否显式传了被切换的参数；null 表示无法判断（仅正文引用） */
  passes_param: boolean | null
}

export interface ToolReferences {
  tool_id: string
  param: string
  agents: ToolReferenceItem[]
  tools: ToolReferenceItem[]
  total: number
}

function extractIdName(md: string) {
  const idMatch = md.match(/^id:\s*(.+)$/m)
  const nameMatch = md.match(/^name:\s*(.+)$/m)
  return { toolId: idMatch?.[1]?.trim() || 'custom-tool', toolName: nameMatch?.[1]?.trim() || 'Custom Tool' }
}

/** 把表单要发的字段拼成 FormData（工具调试/自动修复/上传共用）。 */
function buildToolForm(
  specMd: string,
  code: string,
  testInputs: Record<string, string>,
  files?: File[]
): FormData {
  const { toolId, toolName } = extractIdName(specMd)
  const form = new FormData()
  form.append('spec_md', specMd)
  form.append('tool_id', toolId)
  form.append('tool_name', toolName)
  form.append('code', code)
  form.append('test_input_json', JSON.stringify(testInputs))
  if (files) files.forEach((f) => form.append('files', f))
  return form
}

export const toolApi = {
  async generateSpec(description: string, referenceCode: string = '') {
    return apiPost<{ spec_md: string; tags: string[] }>('/api/tool/generate-spec', {
      description,
      reference_code: referenceCode,
    })
  },

  async generateCode(specMd: string) {
    const { toolId, toolName } = extractIdName(specMd)
    return apiPost<{
      code: string
      params: Array<Record<string, unknown>>
      modes?: Array<{ id: string; name: string; desc?: string }>
    }>('/api/tool/generate-code', { specMd, toolId, toolName })
  },

  async testWithInput(
    specMd: string,
    code: string,
    testInputs: Record<string, string>,
    files?: File[],
    signal?: AbortSignal
  ) {
    const res = await apiFetchRaw('/api/tool/test', {
      method: 'POST',
      body: buildToolForm(specMd, code, testInputs, files),
      signal,
    })
    await assertOk(res)

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
            } catch { /* 跳过非法帧 */ }
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
    const form = new FormData()
    form.append('file', file)
    return apiUpload<{ file_path: string; file_name: string }>(
      '/api/tool/upload-test-file',
      form
    )
  },

  async autoDebug(
    specMd: string,
    code: string,
    testInputs: Record<string, string>,
    files: File[] | undefined,
    onEvent: (type: string, data: Record<string, unknown>) => void,
    signal?: AbortSignal
  ) {
    const res = await apiFetchRaw('/api/tool/auto-debug', {
      method: 'POST',
      body: buildToolForm(specMd, code, testInputs, files),
      signal,
    })
    await assertOk(res)

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
            try { onEvent(currentEvent || 'message', JSON.parse(line.slice(6))) } catch { /* 跳过非法帧 */ }
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
    return apiPost<unknown>('/api/tool/auto-debug/stop', { tool_id: toolId })
  },

  /**
   * 注册工具。
   *
   * baseToolId：编辑已发布工具时传入原工具 id，作为注册的权威身份。
   * 不传时退回 MD 里的 id（新建工具的旧行为）——否则重新生成文档
   * 改掉了 frontmatter 的 id，就会多出一个重复工具。
   */
  async register(specMd: string, code: string, testInputs: Record<string, string>, demandDesc: string = '', referenceCode: string = '', tags: string[] = [], baseToolId?: string): Promise<{ tool_id: string; entry: Record<string, unknown>; updated?: boolean; version?: string; previous_version?: string }> {
    const { toolId, toolName } = extractIdName(specMd)
    return apiPost<{ tool_id: string; entry: Record<string, unknown>; updated?: boolean; version?: string; previous_version?: string }>('/api/tool/register', {
      specMd, code, toolId, toolName, testData: testInputs, demandDesc, referenceCode, tags,
      ...(baseToolId ? { baseToolId } : {}),
    })
  },

  async list() {
    return apiGet<{ tools: Array<Record<string, unknown>> }>('/api/tool/list')
  },

  /** 解析规范文档结构，返回节点树（withSummary=true 时同时生成人话摘要） */
  async getSpecOutline(toolId: string, specMd: string = '', withSummary: boolean = false): Promise<SpecOutline> {
    return apiPost<SpecOutline>(
      `/api/tool/${encodeURIComponent(toolId)}/spec-outline`,
      { spec_md: specMd, with_summary: withSummary }
    )
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
    return apiPost<RefineResult>(
      `/api/tool/${encodeURIComponent(params.toolId)}/refine-spec-node`,
      {
        node_id: params.nodeId,
        feedback: params.feedback,
        spec_md: params.specMd || '',
        save: params.save ?? false,
      }
    )
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
    return apiPost<TableCellUpdateResult>(
      `/api/tool/${encodeURIComponent(params.toolId)}/update-table-cell`,
      {
        node_id: params.nodeId,
        row_index: params.rowIndex,
        column: params.column,
        value: params.value,
        spec_md: params.specMd || '',
        save: params.save ?? false,
      }
    )
  },

  /** 读取文档中的表格结构，供前端渲染可编辑表单 */
  /**
   * 切换某个参数的「必填 / 可选」。
   *
   * 确定性路径（不调 LLM）：后端只改 MD 表格里那一格，并同步 registry 的
   * param_meta —— 对话 Agent 每次请求都读 param_meta，因此切换后立即生效，
   * 既不用重新生成工具，也不用重启 Agent。
   */
  async setParamRequired(params: {
    toolId: string
    name: string
    required: boolean
    specMd?: string
    save?: boolean
  }): Promise<{
    tool_id: string
    name: string
    required: boolean
    spec_md: string
    params: Array<Record<string, unknown>>
    changed: number
    saved: boolean
    references: ToolReferences
  }> {
    return apiPost<{
      tool_id: string
      name: string
      required: boolean
      spec_md: string
      params: Array<Record<string, unknown>>
      changed: number
      saved: boolean
      references: ToolReferences
    }>(
      `/api/tool/${encodeURIComponent(params.toolId)}/param-required`,
      {
        name: params.name,
        required: params.required,
        spec_md: params.specMd || '',
        save: params.save ?? true,
      }
    )
  },

  /** 引用体检：谁在用这个工具（Agent 流程/API、其它工具），纯只读 */
  async getToolReferences(toolId: string, param: string = ''): Promise<ToolReferences> {
    const q = param ? `?param=${encodeURIComponent(param)}` : ''
    return apiGet<ToolReferences>(`/api/tool/${encodeURIComponent(toolId)}/references${q}`)
  },

  /** 开关：执行前是否按 param_meta.required 做必填拦截（默认开启） */
  async setEnforceRequired(toolId: string, enabled: boolean): Promise<{ tool_id: string; enforce_required: boolean }> {
    return apiPost<{ tool_id: string; enforce_required: boolean }>(
      `/api/tool/${encodeURIComponent(toolId)}/enforce-required`,
      { enabled }
    )
  },

  async getSpecTable(toolId: string, specMd: string = ''): Promise<SpecTable> {
    return apiPost<SpecTable>(
      `/api/tool/${encodeURIComponent(toolId)}/spec-table`,
      { spec_md: specMd, with_summary: false }
    )
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
    const res = await apiFetchRaw(
      `/api/tool/${encodeURIComponent(params.toolId)}/refine-spec-node-stream`,
      {
        method: 'POST',
        body: JSON.stringify({
          node_id: params.nodeId,
          feedback: params.feedback,
          spec_md: params.specMd || '',
          save: params.save ?? false,
        }),
        signal: params.signal,
      }
    )

    await assertOk(res)
    if (!res.body) throw new Error('No response body')
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
    return apiPost<{ saved: string }>(
      `/api/tool/${encodeURIComponent(toolId)}/save-spec`,
      { spec_md: specMd }
    )
  },

  /** 保存手工编辑后的工具代码 */
  async saveCode(toolId: string, code: string): Promise<{ saved: string }> {
    return apiPost<{ saved: string }>(
      `/api/tool/${encodeURIComponent(toolId)}/save-code`,
      { code }
    )
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
    return apiPost<{ spec_md: string }>(
      `/api/tool/${encodeURIComponent(params.tool_id)}/sync-spec-from-code`,
      {
        tool_id: params.tool_id,
        code: params.code,
        original_code: params.original_code,
        current_spec: params.current_spec,
      }
    )
  },
}

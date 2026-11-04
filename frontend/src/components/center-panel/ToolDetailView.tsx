import { useState, useEffect, useRef } from 'react'
import {
  Wrench, FileCode, FileText, X, Play, Loader2, Download,
  CheckCheck, Save, Bot, Code, Pencil, Plus, Square,
  FolderOpen, Folder, File, ChevronRight, ChevronDown,
} from 'lucide-react'
import { Highlight, themes } from 'prism-react-renderer'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useTabIndent } from '@/hooks/use-tab-indent'
import { toolApi } from '@/services/api/tool'
import type { ToolReferences } from '@/services/api/tool'
import type { ToolResource } from '@/types/resources'
import { clearToolDraft, loadToolDraft, saveToolDraft, type ToolDetailDraft } from '@/lib/tool-detail-draft'
import { apiFetchRaw, apiGet, apiPost, assertOk } from '@/services/api/client'

interface InputField { name: string; type: string; required: boolean; default: string | null; desc: string }

/** 判断参数是否为"文件路径"类（名称/类型/描述含 path/file/目录/路径 关键词，且类型不是数值/布尔/列表） */
function isFilePathParam(f: InputField): boolean {
  if (/(int|float|number|bool|list|array|json|dict)/i.test(f.type)) return false
  const haystack = `${f.name} ${f.type} ${f.desc || ''}`.toLowerCase()
  return /(path|file|dir|folder|目录|路径|文件)/.test(haystack)
}

function parseInputs(md: string): InputField[] {
  const fields: InputField[] = []
  let inTable = false
  for (const line of md.split('\n')) {
    if (line.includes('输入规范')) { inTable = true; continue }
    if (inTable && line.startsWith('##')) break
    if (inTable && line.startsWith('|') && !line.includes('参数名') && !line.includes('---')) {
      const parts = line.split('|').slice(1, -1).map(s => s.trim())
      if (parts.length >= 4 && parts[0]) {
        fields.push({
          name: parts[0], type: parts[1] || 'string',
          // 与后端 _parse_spec_inputs 对齐（用 includes 而非全等）：
          // 手写成「是（必填）」「仅模式1」这类文本时也应视为必填。
          required: (parts[2] || '').includes('是'),
          default: parts[3] === '-' || parts[3] === '—' || !parts[3] ? null : parts[3],
          desc: parts[4] || '',
        })
      }
    }
  }
  return fields
}

export function ToolDetailView({ toolId }: { toolId?: string } = {}) {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const cachedToolForDetail = useResourceStore((s) => s.cachedToolForDetail)
  const tools = useResourceStore((s) => s.toolResources)
  const setActiveView = useUIStore((s) => s.setActiveView)
  // 指定 toolId 时（独立标签页）从列表中定位；否则回退到选中资源/缓存。
  const tool = toolId
    ? ((tools.find((t: { id: string }) => t.id === toolId) as ToolResource | undefined) || cachedToolForDetail)
    : (selectedResource?.type === 'tool' ? (selectedResource as ToolResource) : cachedToolForDetail)
  // Python 代码用 4 空格缩进
  const onCodeTabIndent = useTabIndent('    ')

  const [specMd, setSpecMd] = useState('')
  const [code, setCode] = useState('')
  const [editedCode, setEditedCode] = useState('')
  const [showSpec, setShowSpec] = useState(false)
  const [showCode, setShowCode] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [showDemand, setShowDemand] = useState(true)
  const [showReference, setShowReference] = useState(false)
  const [demandText, setDemandText] = useState('')
  const [referenceCode, setReferenceCode] = useState('')
  const [hasReference, setHasReference] = useState(false)
  const [formValues, setFormValues] = useState<Record<string, string>>({})
  const [pickerFor, setPickerFor] = useState<string | null>(null)  // 正在选择文件的参数名
  // ── 必填/可选切换（确定性：后端只改 MD 一格 + 同步 param_meta，不调 LLM）──
  const [enforceRequired, setEnforceRequired] = useState(true)
  const [togglingParam, setTogglingParam] = useState('')
  const [refHint, setRefHint] = useState<{ param: string; refs: ToolReferences } | null>(null)
  // output 只被渲染消费（沙箱执行走的是 modify-and-debug 流），不再有独立 setter
  const [output] = useState<Record<string, unknown> | null>(null)
  const [isTesting, setIsTesting] = useState(false)
  const [isUpdating, setIsUpdating] = useState(false)
  const [rightPanelWidth, setRightPanelWidth] = useState(288)  // w-72 = 288px
  const rightPanelRef = useRef(rightPanelWidth)
  rightPanelRef.current = rightPanelWidth
  const [testResults, setTestResults] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [codeModified, setCodeModified] = useState(false)

  // AI 修改 + 自动调试状态
  const [modifyRequest, setModifyRequest] = useState('')
  const [isDebugging, setIsDebugging] = useState(false)
  const [debugRounds, setDebugRounds] = useState<Array<{
    round: number; stdout: string; stderr: string; success: boolean; analysis: string
  }>>([])
  const [debugStream, setDebugStream] = useState('')
  const [showDebugLog, setShowDebugLog] = useState(false)
  const [modifyHistory, setModifyHistory] = useState<string[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const logEndRef = useRef<HTMLDivElement>(null)
  /** 已按哪个 toolId 初始化过表单（用于区分"换工具"与"同一工具重新拉取"） */
  const loadedToolIdRef = useRef('')
  // 导出迁移包（.sotatool.zip）的状态。必须声明在 `if (!tool) return` 之前，
  // 否则 tool 由空变有会让 hooks 数量变化（React 会直接报错）。
  const [exporting, setExporting] = useState(false)
  const [exportInfo, setExportInfo] = useState<{ name: string; path: string; size: number } | null>(null)

  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'auto' }) }, [debugRounds, debugStream])

  useEffect(() => {
    const id = tool?.id
    if (!id) return
    const sameTool = loadedToolIdRef.current === id

    apiGet<any>(`/api/tool/${id}`)
      .then(data => {
        const md = data.spec_md || ''
        const c = data.code || ''
        setSpecMd(md)
        setCode(c)
        // 老条目没有该字段时按「开启」处理（与后端 param_guard 一致）
        setEnforceRequired(data.enforce_required !== false)
        // 切换工具时必须无条件重置：若只在"有值"时 set，上一个工具的
        // 需求描述/参考代码会残留到没有这些内容的工具上（内容错位）
        setDemandText(data.has_demand && data.demand_md ? data.demand_md : '')
        if (data.has_reference && data.reference_code) {
          setReferenceCode(data.reference_code)
          setHasReference(true)
        } else {
          setReferenceCode('')
          setHasReference(false)
        }

        // 同一工具被重新拉取（列表刷新/注册后 tools 换新数组）：保留用户
        // 已输入的参数与未保存的代码，否则会出现"边填边被清空"。
        if (sameTool) return

        loadedToolIdRef.current = id
        const draft = loadToolDraft(id)
        const fields = parseInputs(md)
        const defaultForm: Record<string, string> = {}
        fields.forEach(f => { defaultForm[f.name] = f.default || '' })
        setFormValues(draft?.formValues ? { ...defaultForm, ...draft.formValues } : defaultForm)

        const draftCode = typeof draft?.editedCode === 'string' ? draft.editedCode : ''
        const nextCode = draftCode && draftCode !== c ? draftCode : c
        setEditedCode(nextCode)
        setCodeModified(nextCode !== c)
      })
      .catch(() => {})
    // 依赖 tool.id 而不是 tool 对象：后者在每次 toolResources 刷新后都是新引用，
    // 会让 effect 重跑并重置表单（历史 bug：参数输入后偶发被清空）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool?.id])

  // 草稿持久化：参数与未保存代码按 toolId 落到 localStorage（防抖 300ms）。
  // 切页签会导致组件卸载，重新挂载时由上面的 effect 从草稿恢复。
  useEffect(() => {
    const id = tool?.id
    if (!id || loadedToolIdRef.current !== id) return
    const timer = window.setTimeout(() => {
      // 只在"有未保存修改"时记代码，保存成功后自动不写（草稿不会越积越大）
      saveToolDraft(id, codeModified
        ? { formValues, editedCode, codeModified: true }
        : { formValues })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [tool?.id, formValues, editedCode, codeModified])

  // 卸载时决定「落盘」还是「清空」：
  // - 切到别的页签（本视图仍在 openViews 里）→ 落盘，切回来能恢复
  // - 关闭本页签（已不在 openViews 里）→ 清空草稿（关闭即丢弃）
  // 同时补上 300ms 防抖未触发的最后一次输入。
  const latestDraftRef = useRef<ToolDetailDraft>({})
  latestDraftRef.current = { formValues, editedCode, codeModified }
  useEffect(() => {
    const id = tool?.id
    return () => {
      if (!id || loadedToolIdRef.current !== id) return
      const stillOpen = useUIStore.getState().openViews.includes(`tool-detail:${id}`)
      if (!stillOpen) {
        clearToolDraft(id)
        return
      }
      const d = latestDraftRef.current
      if (!d.formValues) return
      saveToolDraft(id, d.codeModified
        ? { formValues: d.formValues, editedCode: d.editedCode, codeModified: true }
        : { formValues: d.formValues })
    }
  }, [tool?.id])

  if (!tool) {
    return (
      <div className="flex items-center justify-center h-full text-maia-text-muted text-sm">
        请在左侧工具空间选择一个工具
      </div>
    )
  }

  const inputs = parseInputs(specMd)

  // ── 导出为迁移包（.sotatool.zip）：可在另一套部署里导入 ──
  const handleExport = async () => {
    setExporting(true)
    try {
      const data = await apiPost<{ file_name: string; bundle_path: string; size: number }>(
        '/api/migrate/tool/export',
        { tool_id: tool.id }
      )
      setExportInfo({ name: data.file_name, path: data.bundle_path, size: data.size })
    } catch (e) {
      window.alert(`导出失败：${(e as Error).message}`)
    } finally {
      setExporting(false)
    }
  }

  const toggleParamRequired = async (field: InputField) => {
    if (!tool) return
    const next = !field.required
    setTogglingParam(field.name)
    setError(null)
    try {
      const res = await toolApi.setParamRequired({
        toolId: tool.id, name: field.name, required: next, specMd, save: true,
      })
      // 后端只改了 MD 表格里那一格并同步了 param_meta，这里同步本地副本
      setSpecMd(res.spec_md)
      setRefHint(res.references && res.references.total > 0 ? { param: field.name, refs: res.references } : null)
    } catch (e) { setError(String(e)) }
    setTogglingParam('')
  }

  const toggleEnforceRequired = async () => {
    if (!tool) return
    const next = !enforceRequired
    setEnforceRequired(next)
    try {
      await toolApi.setEnforceRequired(tool.id, next)
    } catch (e) {
      setError(String(e))
      setEnforceRequired(!next)
    }
  }

  const handleCodeChange = (val: string) => {
    setEditedCode(val)
    setCodeModified(val !== code)
    setTestResults(null)
  }

  const runTests = async () => {
    if (!editedCode.trim()) return
    setIsTesting(true)
    setError(null)
    setTestResults(null)
    try {
      // 使用 /execute 路由，与对话界面环境一致
      const params: Record<string, unknown> = {}
      inputs.forEach(f => {
        const val = formValues[f.name]
        if (!val && f.required) return
        if (f.type.includes('int')) params[f.name] = parseInt(val) || 0
        else if (f.type.includes('float')) params[f.name] = parseFloat(val) || 0
        else if (f.type.includes('list')) {
          try { params[f.name] = JSON.parse(val) } catch { params[f.name] = val.split(',').map(s => s.trim()) }
        } else params[f.name] = val
      })
      const data = await apiPost<any>(`/api/tool/${tool.id}/execute`, { params })
      setTestResults({ passed: [JSON.stringify(data, null, 2)], failed: data.status === 'failed' ? [data.result?.message || '执行失败'] : [] })
    } catch (e) { setError(String(e)) }
    setIsTesting(false)
  }

  const updateCode = async () => {
    if (!editedCode.trim()) return
    setIsUpdating(true)
    setError(null)
    try {
      await apiPost(`/api/tool/${tool.id}/save-code`, { code: editedCode })
      setCode(editedCode)
      setCodeModified(false)
      setTestResults(null)
    } catch (e) { setError(String(e)) }
    setIsUpdating(false)
  }

  const passed = (testResults?.passed as string[]) || []
  const failed = (testResults?.failed as string[]) || []

  // AI 辅助修改 + 自动调试
  // 注意：这里刻意用普通函数而不是 useCallback —— 它位于 `if (!tool)` 提前返回之后，
  // 若写成 hook，tool 在挂载期间由空变有值时 hook 数量会变，React 会抛
  // "Rendered more hooks than during the previous render"（整块详情面板白屏）。
  const handleModify = async () => {
    const req = modifyRequest.trim()
    if (!req || isDebugging || !tool) return
    setModifyHistory(prev => [...prev, req])
    setModifyRequest('')
    setIsDebugging(true)
    setError(null)
    setShowDebugLog(true)
    setDebugStream('')
    setDebugRounds([])  // 清空调试日志

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await apiFetchRaw(`/api/tool/${tool.id}/modify-and-debug`, {
        method: 'POST',
        body: JSON.stringify({
          current_code: editedCode,
          request: req,
          spec_md: specMd,
          tool_id: tool.id,
          test_params: formValues,
          modify_history: modifyHistory,
        }),
        signal: controller.signal,
      })
      // SSE 流必须先校验 HTTP 状态：否则 4xx/5xx 的 JSON 错误体会被当成 SSE 解析，
      // 最终只报一句"没有结果"，排查时看不出真实原因
      await assertOk(res)

      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')
      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const raw = line.slice(6).trim()
            if (!raw) { currentEvent = ''; continue }
            try {
              const payload = JSON.parse(raw)
              switch (currentEvent) {
                case 'debug_start':
                  // 调试开始，清空旧日志
                  break
                case 'round_start':
                  setDebugRounds(prev => [...prev, {
                    round: payload.round, stdout: '', stderr: '', success: false, analysis: ''
                  }])
                  break
                case 'code_updated':
                  setEditedCode(payload.code)
                  setCodeModified(true)
                  break
                case 'exec_result':
                  setDebugRounds(prev => prev.map((r, i) =>
                    i === prev.length - 1 ? { ...r, stdout: payload.stdout || '', stderr: payload.stderr || '', success: payload.success } : r
                  ))
                  break
                case 'thinking_stream':
                  setDebugRounds(prev => prev.map((r, i) =>
                    i === prev.length - 1 ? { ...r, analysis: r.analysis + (payload.token || '') } : r
                  ))
                  break
                case 'done':
                  if (payload.code && payload.success) {
                    setEditedCode(payload.code)
                    setCodeModified(true)
                    setIsDebugging(false)
                    // 调试通过后自动保存代码到文件
                    const finalCode = payload.code
                    try {
                      await apiPost(`/api/tool/${tool.id}/save-code`, { code: finalCode })
                      setCodeModified(false)
                      setTestResults(null)
                    } catch { /* ignore */ }
                  } else {
                    if (payload.code) { setEditedCode(payload.code); setCodeModified(true) }
                    setIsDebugging(false)
                  }
                  break
                case 'error':
                  setError(payload.message || '调试出错')
                  setIsDebugging(false)
                  break
                case 'stopped':
                  setIsDebugging(false)
                  break
                case 'debug_blocked':
                  // 依赖装不上 → 后端已终止自动调试，把"该手动装什么"直接摆出来
                  setError(payload.message || '依赖安装失败，已停止自动调试')
                  setIsDebugging(false)
                  break
                case 'dep_conflict':
                  // 模块被另一个同名包占用（需先卸载再重试）
                  setError(payload.message || '模块被另一个同名包占用')
                  break
              }
              currentEvent = ''
            } catch { /* skip */ }
          }
        }
      }
    } catch (e: unknown) {
      if ((e as Error).name !== 'AbortError') setError(String(e))
      setIsDebugging(false)
    }
  }

  const handleStopDebug = () => {
    abortRef.current?.abort()
    setIsDebugging(false)
    // 通知后端停止
    apiPost('/api/tool/auto-debug/stop', { tool_id: tool?.id }).catch(() => {})
  }

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0 gap-2">
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">{tool.name}</span>
          <Badge variant="success">v{tool.version}</Badge>
          <ToolTagsEditor toolId={tool.id} tags={tool.tags || []} />
        </div>
        <div className="flex items-center gap-2">
          {demandText && (
            <button onClick={() => { setShowDemand(!showDemand); setShowSpec(false); setShowCode(false); setShowReference(false) }}
              className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline">
              <FileText className="h-3 w-3" />{showDemand ? '收起需求' : '查看需求描述'}
            </button>
          )}
          {hasReference && (
            <button onClick={() => { setShowReference(!showReference); setShowSpec(false); setShowCode(false); setShowDemand(false) }}
              className="flex items-center gap-1 text-[11px] text-purple-500 hover:underline">
              <Code className="h-3 w-3" />{showReference ? '收起参考' : '查看参考代码'}
            </button>
          )}
          <button onClick={() => { setShowSpec(!showSpec); setShowCode(false); setShowDemand(false); setShowReference(false) }}
            className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline">
            <FileText className="h-3 w-3" />{showSpec ? '收起 MD' : '查看 MD 文档'}
          </button>
          <button onClick={() => { setShowCode(!showCode); setShowSpec(false); setShowDemand(false); setShowReference(false) }}
            className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline">
            <FileCode className="h-3 w-3" />{showCode ? '收起代码' : '查看代码'}
          </button>
          <button
            onClick={() => {
              // 编辑已有工具：带齐当前 MD/代码，从「审阅」步进入，
              // 避免被迫从「重新描述需求」走一遍全流程。
              const params = (inputs || []).map((f: { name: string; type?: string; required?: boolean; default?: string | null; desc?: string }) => ({
                name: f.name,
                type: f.type || 'string',
                required: !!f.required,
                default: f.default ?? null,
                desc: f.desc || '',
              }))
              useToolEditorStore.getState().prefillFromTool({
                toolId: tool.id,
                toolName: tool.name,
                description: demandText || tool.name,
                specMd: specMd || '',
                code: editedCode || code || '',
                tags: (tool as { tags?: string[] }).tags || [],
                params,
              })
              setActiveView('tool-editor')
            }}
            className="flex items-center gap-1 text-[11px] text-amber-500 hover:underline ml-2"
          >
            <Pencil className="h-3 w-3" />编辑
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-1 text-[11px] text-emerald-500 hover:underline ml-2 disabled:opacity-50"
            title="导出为 .sotatool.zip，可在其它环境导入"
          >
            {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
            {exporting ? '导出中…' : '导出'}
          </button>
        </div>
      </div>

      {exportInfo && (
        <div className="flex items-center gap-2 px-4 py-1.5 text-[11px] border-b border-maia-border bg-emerald-500/10 shrink-0">
          <span className="text-emerald-600 shrink-0">
            已导出 {Math.max(1, Math.round(exportInfo.size / 1024))} KB：
          </span>
          <a
            className="text-maia-accent underline truncate"
            href={`/api/migrate/bundles/${encodeURIComponent(exportInfo.name)}`}
            download
            title={exportInfo.path}
          >
            {exportInfo.name}
          </a>
          <button
            onClick={() => setExportInfo(null)}
            className="ml-auto text-maia-text-muted hover:underline shrink-0"
          >
            关闭
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto p-4">
        {showCode ? (
          /* 双栏：代码左 + 参数右 */
          <div className="flex h-full min-h-0">
            {/* 左：代码 */}
            <div className="flex-1 min-w-0 flex flex-col min-h-0">
              <Card className="border-maia-border flex-1 flex flex-col min-h-0">
                <CardBody className="flex-1 flex flex-col min-h-0">
                  <div className="flex items-center justify-between mb-2 shrink-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-maia-text-secondary tracking-wide">工具代码</span>
                      {codeModified && <Badge variant="warning">已修改</Badge>}
                    </div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => setIsEditing(!isEditing)} className="flex items-center gap-1 text-[10px] text-maia-text-muted hover:text-maia-accent px-1.5 py-0.5 rounded border border-maia-border">
                        <Pencil className="h-2.5 w-2.5" />{isEditing ? '预览' : '编辑'}
                      </button>
                      <button onClick={() => setShowCode(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                    </div>
                  </div>
                  {isEditing ? (
                    <textarea value={editedCode} onChange={(e) => handleCodeChange(e.target.value)}
                      onKeyDown={onCodeTabIndent}
                      className="flex-1 min-h-0 rounded border border-maia-border bg-maia-bg px-3 py-2 text-[11px] font-mono leading-relaxed outline-none resize-none focus:border-maia-accent/40"
                      spellCheck={false} />
                  ) : (
                    <div className="flex-1 min-h-0 rounded-lg border border-maia-border bg-[#1e1e1e] overflow-auto">
                      <Highlight theme={themes.vsDark} code={editedCode || '# 暂无代码'} language="python">
                        {({ style, tokens, getLineProps, getTokenProps }) => (
                          <pre style={style} className="px-3 py-2 text-[11px] font-mono leading-relaxed m-0">
                            {tokens.map((line, i) => (<div key={i} {...getLineProps({ line })}>
                              <span className="inline-block w-8 text-right mr-3 text-white/20 select-none text-[10px]">{i + 1}</span>
                              {line.map((token, key) => (<span key={key} {...getTokenProps({ token })} />))}
                            </div>))}
                          </pre>
                        )}
                      </Highlight>
                    </div>
                  )}
                  <div className="flex items-center gap-2 mt-3 shrink-0">
                    <Button size="sm" variant="outline" onClick={runTests} disabled={isTesting || !editedCode.trim()}>
                      {isTesting ? <><Loader2 className="h-3 w-3 animate-spin" />测试中</> : <><Play className="h-3 w-3" />沙箱测试</>}</Button>
                    <Button size="sm" onClick={updateCode} disabled={isUpdating || !codeModified || failed.length > 0}>
                      {isUpdating ? <><Loader2 className="h-3 w-3 animate-spin" />更新中</> : <><Save className="h-3 w-3" />更新代码</>}</Button>
                  </div>
                  {testResults && (<div className="mt-2 space-y-1 shrink-0">
                    {passed.map((m, i) => <div key={i} className="text-[10px] text-maia-success flex items-center gap-1"><CheckCheck className="h-3 w-3" />{m}</div>)}
                    {failed.map((m, i) => <div key={i} className="text-[10px] text-maia-danger flex items-center gap-1"><X className="h-3 w-3" />{m}</div>)}
                  </div>)}
                </CardBody>
              </Card>
            </div>
            {/* 可拖拽分隔条 */}
            <div
              onMouseDown={(e) => {
                e.preventDefault()
                const startX = e.clientX
                const startWidth = rightPanelRef.current
                const handleMove = (ev: MouseEvent) => {
                  setRightPanelWidth(Math.max(200, Math.min(500, startWidth - (ev.clientX - startX))))
                }
                const handleUp = () => {
                  document.removeEventListener('mousemove', handleMove)
                  document.removeEventListener('mouseup', handleUp)
                }
                document.addEventListener('mousemove', handleMove)
                document.addEventListener('mouseup', handleUp)
              }}
              className="w-1.5 shrink-0 cursor-col-resize bg-maia-border hover:bg-maia-accent/40 transition-colors group relative mx-1"
            >
              <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <div className="w-0.5 h-6 rounded-full bg-maia-accent/50" />
              </div>
            </div>
            {/* 右：参数 + 执行 + AI修改 */}
            <div className="shrink-0 space-y-4 overflow-auto" style={{ width: rightPanelWidth }}>
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-maia-text-heading tracking-wide">输入参数</h3>
                  <button onClick={toggleEnforceRequired}
                    title="执行前是否按「必填」拦截（默认开启）。关闭后缺参不再由平台拦截，交由工具自身处理。"
                    className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${enforceRequired ? 'border-maia-accent/40 text-maia-accent' : 'border-maia-border text-maia-text-muted'}`}>
                    必填校验：{enforceRequired ? '开' : '关'}
                  </button>
                </div>
                {refHint && (
                  <div className="mb-2 rounded border border-amber-300/60 bg-amber-50/60 px-2 py-1.5 text-[10px] text-amber-700 leading-relaxed">
                    「{refHint.param}」被 {refHint.refs.agents.length} 个 Agent、{refHint.refs.tools.length} 个工具引用
                    {[...refHint.refs.agents, ...refHint.refs.tools].some(r => r.passes_param === false)
                      ? '，其中部分调用未显式传该参数' : ''}
                    。必填化不会中断已有的调用，但可能整条链路拿不到值。
                    <button onClick={() => setRefHint(null)} className="ml-1 underline">知道了</button>
                  </div>
                )}
                {inputs.length === 0 ? (<p className="text-xs text-maia-text-muted">此工具无需输入参数</p>) : (
                  <div className="space-y-3">
                    {inputs.map(f => (<div key={f.name}>
                      <div className="flex items-center gap-1">
                        <label className="text-[11px] font-medium text-maia-text-secondary tracking-wide">
                          {f.name}{f.required && <span className="text-maia-danger ml-0.5">*</span>}<span className="text-maia-text-muted font-normal ml-1">({f.type})</span></label>
                        <button onClick={() => void toggleParamRequired(f)}
                          disabled={togglingParam === f.name}
                          title="切换必填/可选：只改 MD 表格一格并同步 param_meta，对话 Agent 下一轮即生效"
                          className="ml-auto text-[9px] px-1.5 py-0.5 rounded border border-maia-border text-maia-text-muted hover:text-maia-accent hover:border-maia-accent/40 disabled:opacity-50 transition-colors">
                          {togglingParam === f.name ? '切换中…' : f.required ? '必填 → 可选' : '可选 → 必填'}
                        </button>
                      </div>
                      {f.desc && <p className="text-[10px] text-maia-text-muted mb-1">{f.desc}</p>}
                      <div className="flex items-center gap-1.5">
                        <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                          placeholder={f.required ? '必填' : f.default || '可选'}
                          className="flex-1 min-w-0 h-8 rounded border border-maia-border bg-maia-surface px-3 text-[12px] text-maia-text outline-none focus:border-maia-accent/40" />
                        {isFilePathParam(f) && (
                          <button type="button" onClick={() => setPickerFor(f.name)} title="从工作区间选择数据文件"
                            className="shrink-0 h-8 px-2 rounded border border-maia-border bg-maia-surface hover:border-maia-accent/50 hover:text-maia-accent text-maia-text-muted transition-colors">
                            <FolderOpen className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </div>))}
                  </div>)}
              </div>

              {/* AI 辅助修改 */}
              <div className="p-3 rounded-lg border border-purple-200 bg-purple-50/50">
                <div className="flex items-center gap-1.5 mb-2">
                  <Bot className="h-3.5 w-3.5 text-purple-500" />
                  <span className="text-[11px] font-medium text-purple-700 tracking-wide">AI 辅助修改</span>
                  {isDebugging && <Loader2 className="h-3 w-3 animate-spin text-purple-500" />}
                </div>
                <div className="flex flex-col gap-1.5">
                  <textarea
                    value={modifyRequest}
                    onChange={e => {
                      setModifyRequest(e.target.value)
                      // auto-resize
                      const el = e.target
                      el.style.height = 'auto'
                      el.style.height = Math.min(el.scrollHeight, 120) + 'px'
                    }}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleModify() }
                    }}
                    placeholder={modifyHistory.length > 0 ? '继续修改建议... (Enter提交，Shift+Enter换行)' : '描述修改需求，Enter提交，Shift+Enter换行'}
                    disabled={isDebugging}
                    rows={2}
                    className="w-full rounded border border-maia-border bg-maia-surface px-2 py-1.5 text-[11px] text-maia-text outline-none focus:border-maia-accent/40 disabled:opacity-50 resize-none"
                  />
                  {isDebugging ? (
                    <Button size="sm" variant="danger" className="h-7 text-[11px] w-full" onClick={handleStopDebug}>
                      <Square className="h-3 w-3" />停止调试
                    </Button>
                  ) : (
                    <Button size="sm" className="bg-purple-500 hover:bg-purple-600 text-white h-7 text-[11px] w-full" onClick={handleModify} disabled={!modifyRequest.trim()}>
                      修改
                    </Button>
                  )}
                </div>
              </div>

              {/* 调试日志 */}
              {showDebugLog && debugRounds.length > 0 && (
                <div className="rounded-lg border border-maia-border bg-maia-bg/50 overflow-auto" style={{ maxHeight: '200px' }}>
                  <div className="px-3 py-1.5 text-[10px] font-semibold text-maia-text-secondary tracking-wider border-b border-maia-border sticky top-0 bg-maia-bg/90 z-10 flex items-center justify-between">
                    <span>🐛 调试日志</span>
                    <button onClick={() => { setShowDebugLog(false); setDebugRounds([]); setDebugStream('') }} className="text-maia-text-muted hover:text-maia-text">
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                  <div className="p-2 space-y-2 font-mono text-[10px] text-maia-text-secondary">
                    {debugStream && (
                      <pre className="whitespace-pre-wrap break-all leading-relaxed text-[10px] text-maia-text-muted">{debugStream}</pre>
                    )}
                    {debugRounds.map((round, i) => (
                      <div key={i} className="border-b border-maia-border/30 pb-1.5 last:border-0">
                        {round.round === -1 ? (
                          <div className="text-[10px] text-purple-400 font-semibold py-1">{round.analysis}</div>
                        ) : (
                          <div>
                            <div className="flex items-center gap-1 text-[10px] font-semibold">
                              <span className="text-maia-text-muted">[第{round.round}轮]</span>
                              <span className={round.success ? 'text-maia-success' : 'text-maia-danger'}>
                                {round.success ? '✅ 通过' : '❌ 失败'}
                              </span>
                            </div>
                            {round.stdout && (
                              <pre className="whitespace-pre-wrap break-all leading-relaxed mt-0.5 text-maia-text">{round.stdout}</pre>
                            )}
                            {round.stderr && (
                              <pre className="whitespace-pre-wrap break-all leading-relaxed mt-0.5 text-maia-danger">{round.stderr}</pre>
                            )}
                            {round.analysis && (
                              <pre className="text-green-400 whitespace-pre-wrap break-all mt-0.5 leading-relaxed">{round.analysis}</pre>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                    <div ref={logEndRef} />
                  </div>
                </div>
              )}

              {error && <div className="p-3 rounded-lg border border-red-200 bg-red-50 text-xs text-maia-danger">{error}</div>}
              {output && (<Card className="border-emerald-200 bg-emerald-50/30"><CardBody>
                <div className="flex items-center gap-1.5 mb-2">
                  <div className={`h-2 w-2 rounded-full ${output.status === 'success' ? 'bg-maia-success' : 'bg-maia-danger'}`} />
                  <span className="text-xs font-medium text-maia-text-secondary tracking-wide">{output.status === 'success' ? '执行成功' : '执行失败'}</span>
                </div>
                <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[300px] overflow-auto bg-maia-bg rounded p-3 border border-maia-border">{JSON.stringify(output, null, 2)}</pre>
              </CardBody></Card>)}
            </div>
          </div>
        ) : (
          /* 单栏 */
          <div className="max-w-2xl mx-auto space-y-4">
          {showSpec && (<Card className="border-maia-border"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-xs font-medium text-maia-text-secondary tracking-wide">MD 规范文档</span><button onClick={() => setShowSpec(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">{specMd || '加载中...'}</pre>
          </CardBody></Card>)}
          {showDemand && demandText && (<Card className="border-maia-border"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-xs font-medium text-maia-text-secondary tracking-wide">用户需求描述</span><button onClick={() => setShowDemand(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">{demandText}</pre>
          </CardBody></Card>)}
          {showReference && referenceCode && (<Card className="border-purple-200 bg-purple-50/30"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-xs font-medium text-purple-700 tracking-wide">参考代码</span><button onClick={() => setShowReference(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3 border border-maia-border">{referenceCode}</pre>
          </CardBody></Card>)}
          <div>
            <h3 className="text-sm font-semibold text-maia-text-heading mb-3 tracking-wide">输入参数</h3>
            {inputs.length === 0 ? (<p className="text-xs text-maia-text-muted">此工具无需输入参数</p>) : (
              <div className="space-y-3">
                {inputs.map(f => (<div key={f.name}>
                  <label className="text-[11px] font-medium text-maia-text-secondary tracking-wide">{f.name}{f.required && <span className="text-maia-danger ml-0.5">*</span>}<span className="text-maia-text-muted font-normal ml-1">({f.type})</span></label>
                  {f.desc && <p className="text-[10px] text-maia-text-muted mb-1">{f.desc}</p>}
                  <div className="flex items-center gap-1.5">
                    <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                      placeholder={f.required ? '必填' : f.default || '可选'} className="flex-1 min-w-0 h-8 rounded border border-maia-border bg-maia-surface px-3 text-[12px] text-maia-text outline-none focus:border-maia-accent/40" />
                    {isFilePathParam(f) && (
                      <button type="button" onClick={() => setPickerFor(f.name)} title="从工作区间选择数据文件"
                        className="shrink-0 h-8 px-2 rounded border border-maia-border bg-maia-surface hover:border-maia-accent/50 hover:text-maia-accent text-maia-text-muted transition-colors">
                        <FolderOpen className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>))}
              </div>)}
          </div>
          {error && <div className="p-3 rounded-lg border border-red-200 bg-red-50 text-xs text-maia-danger">{error}</div>}
          {output && (<Card className="border-emerald-200 bg-emerald-50/30"><CardBody>
            <div className="flex items-center gap-1.5 mb-2">
              <div className={`h-2 w-2 rounded-full ${output.status === 'success' ? 'bg-maia-success' : 'bg-maia-danger'}`} />
              <span className="text-xs font-medium text-maia-text-secondary tracking-wide">{output.status === 'success' ? '执行成功' : '执行失败'}</span>
            </div>
            <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[300px] overflow-auto bg-maia-bg rounded p-3 border border-maia-border">{JSON.stringify(output, null, 2)}</pre>
          </CardBody></Card>)}
          </div>
        )}
      </div>
      {/* 工作区间文件选择弹窗 */}
      {pickerFor && (
        <WorkspaceFilePicker
          paramName={pickerFor}
          currentValue={formValues[pickerFor] || ''}
          onClose={() => setPickerFor(null)}
          onSelect={(path) => {
            setFormValues(p => ({ ...p, [pickerFor]: path }))
            setPickerFor(null)
          }}
        />
      )}
    </div>
  )
}

/** 工作区间文件选择弹窗：列出已加载数据集及其内部文件，选中后返回完整路径 */
/** 可选的数据集条目（来自 /api/data/repository，带可用性标记） */
interface PickableDataset {
  id: string
  name: string
  tags?: string[]
  file_count?: number
  data_path?: string
  /** 数据目录在本机存在且有内容 */
  is_local?: boolean
  /** 条目归属当前用户 */
  owner_match?: boolean
}

const RECENT_DATASETS_KEY = 'sotaband_picker_recent_datasets'

function loadRecentDatasets(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_DATASETS_KEY)
    const arr = raw ? JSON.parse(raw) : []
    return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : []
  } catch { return [] }
}

function saveRecentDatasets(ids: string[]) {
  try { localStorage.setItem(RECENT_DATASETS_KEY, JSON.stringify(ids.slice(0, 5))) } catch { /* ignore */ }
}

function WorkspaceFilePicker({ paramName, currentValue, onClose, onSelect }: {
  paramName: string
  currentValue: string
  onClose: () => void
  onSelect: (path: string) => void
}) {
  // 数据源：数据集仓库 + 可用性标记。
  // 曾经读的是 localStorage 里「已加入工作区间」的白名单，但它的写入入口
  // （dataset-repository 卡片上的「添加」）在当前 UI 里没有入口，导致选择框永远为空。
  // 现在直接列仓库条目，并用 is_local 区分「本机有数据」与「注册于其他机器的幽灵条目」。
  const [datasets, setDatasets] = useState<PickableDataset[]>([])
  const [loadingList, setLoadingList] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [recentIds, setRecentIds] = useState<string[]>(loadRecentDatasets)
  const [showUnavailable, setShowUnavailable] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [filesMap, setFilesMap] = useState<Record<string, { name: string; path: string; size?: number }[]>>({})
  const [fileError, setFileError] = useState<Record<string, string>>({})
  const [loadingFiles, setLoadingFiles] = useState<Record<string, boolean>>({})

  useEffect(() => { void loadDatasets() }, [])

  const loadDatasets = async () => {
    setLoadingList(true); setLoadError('')
    try {
      const data = await apiGet<{ datasets: PickableDataset[] }>('/api/data/repository')
      setDatasets(Array.isArray(data.datasets) ? data.datasets : [])
    } catch (e) { setLoadError(String((e as Error).message || e)) }
    setLoadingList(false)
  }

  const remember = (id: string) => {
    const next = [id, ...recentIds.filter(x => x !== id)].slice(0, 5)
    setRecentIds(next)
    saveRecentDatasets(next)
  }

  const dataRoot = (ds: PickableDataset): string => (ds.data_path || '').replace(/[\\/]+$/, '')

  const toggleFiles = async (ds: PickableDataset) => {
    if (expandedId === ds.id) { setExpandedId(null); return }
    setExpandedId(ds.id)
    if (filesMap[ds.id]) return
    setLoadingFiles(p => ({ ...p, [ds.id]: true }))
    setFileError(p => ({ ...p, [ds.id]: '' }))
    try {
      let root = dataRoot(ds)
      if (!root) {
        // 详情拿不到时不阻断文件列表（根目录只用于拼完整路径）
        try {
          const detail = await apiGet<{ data_path?: string }>(`/api/data/${ds.id}`)
          root = (detail && detail.data_path) || ''
        } catch {
          root = ''
        }
      }
      const data = await apiGet<{ files: { name: string; path: string; size?: number }[] }>(
        `/api/data/${ds.id}/files`
      )
      const files = (data.files || []).map((f: { name: string; path: string; size?: number }) => ({
        ...f,
        // 后端返回相对路径；拼上数据集根目录得到可执行用的完整路径
        path: f.path.startsWith('/') || /^[A-Za-z]:/.test(f.path) ? f.path : `${root}/${f.path}`,
      }))
      setFilesMap(p => ({ ...p, [ds.id]: files }))
    } catch (e) {
      setFilesMap(p => ({ ...p, [ds.id]: [] }))
      setFileError(p => ({ ...p, [ds.id]: String((e as Error).message || e) }))
    }
    setLoadingFiles(p => ({ ...p, [ds.id]: false }))
  }

  const kw = keyword.trim().toLowerCase()
  const matches = (ds: PickableDataset) =>
    !kw || [ds.name, ds.id, ...(ds.tags || [])].some(x => String(x || '').toLowerCase().includes(kw))
  const available = datasets.filter(d => d.is_local && matches(d))
  const unavailable = datasets.filter(d => !d.is_local && matches(d))
  const recentSet = new Set(recentIds)
  const recentList = recentIds
    .map(id => available.find(d => d.id === id))
    .filter((d): d is PickableDataset => Boolean(d))
  const restList = available.filter(d => !recentSet.has(d.id))

  const renderDataset = (ds: PickableDataset) => {
    const files = filesMap[ds.id] || []
    const isExpanded = expandedId === ds.id
    const root = dataRoot(ds)
    return (
      <div key={ds.id} className="rounded border border-maia-border bg-maia-surface overflow-hidden">
        <button
          className="w-full flex items-center gap-2 px-2.5 py-2 hover:bg-maia-bg transition-colors text-left"
          onClick={() => void toggleFiles(ds)}
        >
          {loadingFiles[ds.id] ? <Loader2 className="h-3.5 w-3.5 animate-spin text-maia-accent shrink-0" />
            : isExpanded ? <ChevronDown className="h-3.5 w-3.5 text-maia-text-muted shrink-0" />
            : <ChevronRight className="h-3.5 w-3.5 text-maia-text-muted shrink-0" />}
          <Folder className="h-3.5 w-3.5 text-amber-500 shrink-0" />
          <span className="text-[12px] text-maia-text truncate flex-1">{ds.name}</span>
          {root && <span className="text-[10px] text-maia-text-muted truncate max-w-[150px]" title={root}>{root}</span>}
        </button>
        {isExpanded && (
          <div className="border-t border-maia-border max-h-[220px] overflow-auto">
            <button
              className={`w-full flex items-center gap-2 px-3 py-1.5 text-left ${root ? 'hover:bg-maia-accent/10' : 'opacity-50 cursor-not-allowed'}`}
              onClick={() => { if (root) { remember(ds.id); onSelect(root) } }}
              title={root ? '选择数据集根目录' : '该数据集没有可用的目录路径'}
            >
              <Folder className="h-3 w-3 text-maia-accent shrink-0" />
              <span className="text-[11px] text-maia-text-secondary">使用数据集目录路径</span>
            </button>
            {fileError[ds.id] && (
              <div className="px-3 py-2 text-[10px] text-maia-danger leading-relaxed">{fileError[ds.id]}</div>
            )}
            {!fileError[ds.id] && files.length === 0 && !loadingFiles[ds.id] && (
              <div className="px-3 py-2 text-[10px] text-maia-text-muted">该数据集内没有文件</div>
            )}
            {files.map((f, i) => (
              <button
                key={i}
                className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-maia-accent/10 text-left"
                onClick={() => { remember(ds.id); onSelect(f.path) }}
                title={f.path}
              >
                <File className="h-3 w-3 text-maia-text-muted shrink-0" />
                <span className="text-[11px] text-maia-text truncate flex-1">{f.name}</span>
                {f.size != null && <span className="text-[10px] text-maia-text-muted shrink-0">{(f.size / 1024).toFixed(1)}KB</span>}
              </button>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="w-[480px] max-h-[70vh] flex flex-col rounded-lg border border-maia-border bg-maia-bg shadow-xl overflow-hidden" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-maia-border shrink-0">
          <span className="text-sm font-semibold text-maia-text-heading flex items-center gap-1.5">
            <FolderOpen className="h-4 w-4 text-maia-accent" />选择数据文件 — {paramName}
          </span>
          <button onClick={onClose} className="text-maia-text-muted hover:text-maia-text"><X className="h-4 w-4" /></button>
        </div>
        <div className="px-3 pt-2 shrink-0">
          <input
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            placeholder="按名称 / ID / 标签筛选数据集"
            className="w-full rounded border border-maia-border bg-maia-surface px-2 py-1 text-[11px] outline-none focus:border-maia-accent/40"
          />
        </div>
        <div className="flex-1 overflow-auto p-3 space-y-1.5">
          {currentValue && (
            <div className="mb-2 px-2 py-1 rounded bg-maia-surface border border-maia-border text-[11px] text-maia-text-muted">
              当前值：<span className="text-maia-text font-mono break-all">{currentValue}</span>
            </div>
          )}
          {loadingList && (
            <div className="flex items-center justify-center py-10 text-xs text-maia-text-muted gap-2">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />正在读取数据集…
            </div>
          )}
          {!loadingList && loadError && (
            <div className="text-center py-8 text-xs text-maia-danger">
              读取数据集失败：{loadError}
              <button onClick={() => void loadDatasets()} className="block mx-auto mt-2 underline text-maia-text-muted">重试</button>
            </div>
          )}
          {!loadingList && !loadError && datasets.length === 0 && (
            <div className="text-center py-10 text-xs text-maia-text-muted leading-relaxed">
              数据空间还没有数据集<br />
              请先在「资源空间」上传或注册数据，也可以直接在输入框里手填文件绝对路径
            </div>
          )}
          {!loadingList && !loadError && datasets.length > 0 && available.length === 0 && (
            <div className="py-6 px-2 text-xs text-maia-text-muted leading-relaxed text-center">
              本机没有可用数据集
              <div className="mt-2 text-[10px] text-maia-text-muted/90">
                注册表里有 {datasets.length} 个数据集，但数据目录都不在本机
                {unavailable[0]?.data_path ? `（例如 ${unavailable[0].data_path}）` : ''}。
                这类条目通常是随项目目录一起拷贝过来的，文件仍在原机器上。
                <br />请在「资源空间」重新上传数据，或直接在输入框里手填文件绝对路径。
              </div>
            </div>
          )}
          {recentList.length > 0 && (
            <>
              <p className="text-[10px] text-maia-text-muted px-1">最近使用</p>
              {recentList.map(renderDataset)}
            </>
          )}
          {restList.length > 0 && (
            <>
              <p className="text-[10px] text-maia-text-muted px-1 pt-1">
                本机可用数据集（{available.length}）— 点击展开查看文件
              </p>
              {restList.map(renderDataset)}
            </>
          )}
          {!loadingList && unavailable.length > 0 && (
            <div className="pt-2">
              <button
                onClick={() => setShowUnavailable(v => !v)}
                className="w-full flex items-center gap-1 px-1 text-[10px] text-maia-text-muted hover:text-maia-text"
              >
                {showUnavailable ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                不可用（{unavailable.length}）：数据目录不在本机
              </button>
              {showUnavailable && (
                <div className="mt-1 space-y-1">
                  {unavailable.map(ds => (
                    <div key={ds.id} className="rounded border border-maia-border/70 bg-maia-surface/50 px-2.5 py-1.5 opacity-60">
                      <div className="flex items-center gap-2">
                        <Folder className="h-3 w-3 text-maia-text-muted shrink-0" />
                        <span className="text-[11px] text-maia-text-muted truncate flex-1">{ds.name}</span>
                        <span className="text-[9px] text-maia-text-muted shrink-0">不可用</span>
                      </div>
                      {ds.data_path && <p className="mt-0.5 text-[9px] text-maia-text-muted font-mono truncate" title={ds.data_path}>{ds.data_path}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="px-4 py-2 border-t border-maia-border text-[10px] text-maia-text-muted shrink-0">
          点击文件填入完整路径；点击"使用数据集目录路径"填入数据集根目录
        </div>
      </div>
    </div>
  )
}

/** 工具标签编辑器（内联组件） */
function ToolTagsEditor({ toolId, tags: initialTags }: { toolId: string; tags: string[] }) {
  const [tags, setTags] = useState<string[]>(initialTags || [])
  const [editing, setEditing] = useState(false)
  const [newTag, setNewTag] = useState('')
  const [, setSaving] = useState(false)

  const saveTags = async (updated: string[]) => {
    setSaving(true)
    try {
      await apiPost(`/api/tool/${toolId}/tags`, { tags: updated })
      setTags(updated)
    } catch { /* ignore */ }
    setSaving(false)
  }

  return (
    <div className="flex items-center gap-1">
      {tags.map(tag => (
        <span key={tag} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-amber-100 text-amber-700 border border-amber-300 font-medium">
          {tag}
          {editing && (
            <button onClick={() => saveTags(tags.filter(t => t !== tag))} className="hover:text-red-400">
              <X className="h-2.5 w-2.5" />
            </button>
          )}
        </span>
      ))}
      {editing && (
        <form onSubmit={e => { e.preventDefault(); if (newTag.trim()) { const updated = [...tags, newTag.trim()]; saveTags(updated); setNewTag(''); } }}>
          <input
            value={newTag}
            onChange={e => setNewTag(e.target.value)}
            placeholder="+标签"
            className="w-16 h-5 px-1 rounded border border-maia-accent/40 bg-maia-bg text-[10px] text-maia-text outline-none"
            autoFocus
            onBlur={() => { if (!newTag.trim()) setEditing(false) }}
          />
        </form>
      )}
      {!editing && (
        <button
          onClick={() => setEditing(true)}
          className="inline-flex items-center justify-center h-5 w-5 rounded hover:bg-maia-bg text-maia-text-muted hover:text-maia-accent"
          title="编辑标签"
        >
          <Plus className="h-3 w-3" />
        </button>
      )}
      {editing && (
        <button
          onClick={() => setEditing(false)}
          className="text-[10px] text-maia-text-muted hover:text-maia-text ml-1"
        >
          完成
        </button>
      )}
    </div>
  )
}

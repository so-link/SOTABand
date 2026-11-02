import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Wrench, FileCode, FileText, X, Play, Loader2,
  CheckCheck, Save, Bot, Code, Pencil, Plus, Square,
  FolderOpen, Folder, File, ChevronRight, ChevronDown, Download,
} from 'lucide-react'
import { Highlight, themes } from 'prism-react-renderer'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import { useWorkspaceDatasetStore } from '@/stores/workspace-dataset-store'
import { useUIStore } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useTabIndent } from '@/hooks/use-tab-indent'
import { cn } from '@/lib/utils'
import type { ToolResource } from '@/types/resources'

const BASE_URL = ''

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
          required: parts[2] === '是',
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

  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'auto' }) }, [debugRounds, debugStream])

  useEffect(() => {
    if (!tool) return
    fetch(`${BASE_URL}/api/tool/${tool.id}`)
      .then(r => r.json())
      .then(data => {
        setSpecMd(data.spec_md || '')
        const c = data.code || ''
        setCode(c)
        setEditedCode(c)
        const fields = parseInputs(data.spec_md || '')
        const init: Record<string, string> = {}
        fields.forEach(f => { init[f.name] = f.default || '' })
        setFormValues(init)
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
      })
      .catch(() => {})
  }, [tool])

  const inputs = parseInputs(specMd)

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
      const res = await fetch(`${BASE_URL}/api/tool/${tool.id}/execute`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params }),
      })
      if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`)
      const data = await res.json()
      setTestResults({ passed: [JSON.stringify(data, null, 2)], failed: data.status === 'failed' ? [data.result?.message || '执行失败'] : [] })
    } catch (e) { setError(String(e)) }
    setIsTesting(false)
  }

  const updateCode = async () => {
    if (!editedCode.trim()) return
    setIsUpdating(true)
    setError(null)
    try {
      const res = await fetch(`${BASE_URL}/api/tool/${tool.id}/save-code`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: editedCode }),
      })
      if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`)
      setCode(editedCode)
      setCodeModified(false)
      setTestResults(null)
    } catch (e) { setError(String(e)) }
    setIsUpdating(false)
  }

  const passed = (testResults?.passed as string[]) || []
  const failed = (testResults?.failed as string[]) || []

  // AI 辅助修改 + 自动调试
  const handleModify = useCallback(async () => {
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
      const res = await fetch(`${BASE_URL}/api/tool/${tool.id}/modify-and-debug`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
                      const saveRes = await fetch(`${BASE_URL}/api/tool/${tool.id}/save-code`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ code: finalCode, toolId: tool.id }),
                      })
                      if (saveRes.ok) {
                        setCodeModified(false)
                        setTestResults(null)
                      }
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
  }, [modifyRequest, isDebugging, tool, editedCode, specMd, formValues, modifyHistory])

  const handleStopDebug = useCallback(() => {
    abortRef.current?.abort()
    setIsDebugging(false)
    // 通知后端停止
    fetch(`${BASE_URL}/api/tool/auto-debug/stop`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool_id: tool?.id }),
    }).catch(() => {})
  }, [tool])

  // 所有 hooks 必须在提前 return 之前声明，否则 tool 从 null 变为有值时
  // hooks 调用数量变化会触发 React "Rendered more hooks than during the previous render"
  if (!tool) {
    return (
      <div className="flex items-center justify-center h-full text-maia-text-muted text-sm">
        请在左侧工具空间选择一个工具
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header：第一行身份与操作，第二行文档/代码工具条（与 Agent 详情页对齐） */}
      <div className="shrink-0 border-b border-maia-border bg-maia-bg/40">
        <div className="flex items-center gap-2 px-4 h-11 min-w-0">
          <Wrench className="h-4 w-4 text-amber-500 shrink-0" />
          <span className="text-[14px] font-semibold text-maia-text-heading truncate min-w-0">
            {tool.name}
          </span>
          <Badge variant="success" className="shrink-0">v{tool.version}</Badge>

          <div className="ml-auto flex items-center gap-2 shrink-0">
            <a
              href={`${BASE_URL}/api/tool/${encodeURIComponent(tool.id)}/export`}
              download={`${tool.id}.zip`}
              title="导出为 zip 工具包（含代码、文档、依赖清单，不含虚拟环境）"
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-transparent text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text transition-colors shrink-0"
            >
              <Download className="h-3.5 w-3.5" />
              导出
            </a>
          </div>
        </div>

        <div className="flex items-center gap-1 px-3 h-9 border-t border-maia-border/60 overflow-x-auto">
          {demandText && (
            <button
              onClick={() => setShowDemand(!showDemand)}
              className={cn(
                'inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border transition-colors shrink-0',
                showDemand
                  ? 'bg-maia-accent-light text-maia-accent border-maia-accent-border'
                  : 'text-maia-text-muted border-transparent hover:bg-maia-sidebar-hover hover:text-maia-text',
              )}
              title="需求描述"
            >
              <FileText className="h-3.5 w-3.5" />
              {showDemand ? '收起需求' : '需求描述'}
            </button>
          )}
          <button
            onClick={() => setShowSpec(!showSpec)}
            className={cn(
              'inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border transition-colors shrink-0',
              showSpec
                ? 'bg-maia-accent-light text-maia-accent border-maia-accent-border'
                : 'text-maia-text-muted border-transparent hover:bg-maia-sidebar-hover hover:text-maia-text',
            )}
          >
            <FileText className="h-3.5 w-3.5" />
            {showSpec ? '收起 MD 文档' : 'MD 文档'}
          </button>
          {hasReference && (
            <button
              onClick={() => setShowReference(!showReference)}
              className={cn(
                'inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border transition-colors shrink-0',
                showReference
                  ? 'bg-maia-accent-light text-maia-accent border-maia-accent-border'
                  : 'text-maia-text-muted border-transparent hover:bg-maia-sidebar-hover hover:text-maia-text',
              )}
              title="参考代码"
            >
              <Code className="h-3.5 w-3.5" />
              {showReference ? '收起参考' : '参考代码'}
            </button>
          )}
          <button
            onClick={() => setShowCode(!showCode)}
            className={cn(
              'inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border transition-colors shrink-0',
              showCode
                ? 'bg-maia-accent-light text-maia-accent border-maia-accent-border'
                : 'text-maia-text-muted border-transparent hover:bg-maia-sidebar-hover hover:text-maia-text',
            )}
          >
            <FileCode className="h-3.5 w-3.5" />
            {showCode ? '收起代码' : '代码'}
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
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-transparent text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text transition-colors shrink-0 ml-auto"
            title="带齐现有文档与代码，从审阅步进入编辑"
          >
            <Pencil className="h-3.5 w-3.5" />
            编辑
          </button>
        </div>
      </div>

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
                      <span className="text-[13px] font-medium text-maia-text-secondary">工具代码</span>
                      {codeModified && <Badge variant="warning">已修改</Badge>}
                    </div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => setIsEditing(!isEditing)} className="flex items-center gap-1 text-[12px] text-maia-text-muted hover:text-maia-accent px-1.5 py-0.5 rounded border border-maia-border">
                        <Pencil className="h-2.5 w-2.5" />{isEditing ? '预览' : '编辑'}
                      </button>
                      <button onClick={() => setShowCode(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                    </div>
                  </div>
                  {isEditing ? (
                    <textarea value={editedCode} onChange={(e) => handleCodeChange(e.target.value)}
                      onKeyDown={onCodeTabIndent}
                      className="flex-1 min-h-0 rounded border border-maia-border bg-maia-code-bg text-maia-code-text px-3 py-2 text-[13px] font-mono leading-[22px] outline-none resize-none focus:border-maia-accent/40"
                      spellCheck={false} />
                  ) : (
                    <div className="flex-1 min-h-0 rounded-lg border border-maia-border bg-maia-code-bg overflow-auto">
                      <Highlight theme={themes.vsDark} code={editedCode || '# 暂无代码'} language="python">
                        {({ style, tokens, getLineProps, getTokenProps }) => (
                          <pre style={style} className="px-3 py-2 text-[13px] font-mono leading-[22px] m-0">
                            {tokens.map((line, i) => (<div key={i} {...getLineProps({ line })}>
                              <span className="inline-block w-8 text-right mr-3 text-white/20 select-none text-[12px]">{i + 1}</span>
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
                    {passed.map((m, i) => <div key={i} className="text-[12px] text-maia-success flex items-center gap-1"><CheckCheck className="h-3 w-3" />{m}</div>)}
                    {failed.map((m, i) => <div key={i} className="text-[12px] text-maia-danger flex items-center gap-1"><X className="h-3 w-3" />{m}</div>)}
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
                <h3 className="text-sm font-semibold text-maia-text-heading mb-3">输入参数</h3>
                {inputs.length === 0 ? (<p className="text-[13px] text-maia-text-muted">此工具无需输入参数</p>) : (
                  <div className="space-y-3">
                    {inputs.map(f => (<div key={f.name}>
                      <label className="text-[12px] font-medium text-maia-text-secondary">
                        {f.name}{f.required && <span className="text-maia-danger ml-0.5">*</span>}<span className="text-maia-text-muted font-normal ml-1">({f.type})</span></label>
                      {f.desc && <p className="text-[12px] text-maia-text-muted mb-1">{f.desc}</p>}
                      <div className="flex items-center gap-1.5">
                        <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                          placeholder={f.required ? '必填' : f.default || '可选'}
                          className="flex-1 min-w-0 h-8 rounded border border-maia-border bg-maia-surface px-3 text-[13px] text-maia-text outline-none focus:border-maia-accent/40" />
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
                  <Bot className="h-3.5 w-3.5 text-maia-accent" />
                  <span className="text-[12px] font-medium text-maia-accent">AI 辅助修改</span>
                  {isDebugging && <Loader2 className="h-3 w-3 animate-spin text-maia-accent" />}
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
                    className="w-full rounded border border-maia-border bg-maia-surface px-2 py-1.5 text-[12px] text-maia-text outline-none focus:border-maia-accent/40 disabled:opacity-50 resize-none"
                  />
                  {isDebugging ? (
                    <Button size="sm" variant="danger" className="h-7 text-[12px] w-full" onClick={handleStopDebug}>
                      <Square className="h-3 w-3" />停止调试
                    </Button>
                  ) : (
                    <Button size="sm" className="bg-maia-accent hover:bg-maia-accent-hover text-white h-7 text-[12px] w-full" onClick={handleModify} disabled={!modifyRequest.trim()}>
                      修改
                    </Button>
                  )}
                </div>
              </div>

              {/* 调试日志 */}
              {showDebugLog && debugRounds.length > 0 && (
                <div className="rounded-lg border border-maia-border bg-maia-bg/50 overflow-auto" style={{ maxHeight: '200px' }}>
                  <div className="px-3 py-1.5 text-[12px] font-semibold text-maia-text-secondary border-b border-maia-border sticky top-0 bg-maia-bg/90 z-10 flex items-center justify-between">
                    <span>🐛 调试日志</span>
                    <button onClick={() => { setShowDebugLog(false); setDebugRounds([]); setDebugStream('') }} className="text-maia-text-muted hover:text-maia-text">
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                  <div className="p-2 space-y-2 font-mono text-[12px] text-maia-text-secondary">
                    {debugStream && (
                      <pre className="whitespace-pre-wrap break-all leading-relaxed text-[12px] text-maia-text-muted">{debugStream}</pre>
                    )}
                    {debugRounds.map((round, i) => (
                      <div key={i} className="border-b border-maia-border/30 pb-1.5 last:border-0">
                        {round.round === -1 ? (
                          <div className="text-[12px] text-maia-accent font-semibold py-1">{round.analysis}</div>
                        ) : (
                          <div>
                            <div className="flex items-center gap-1 text-[12px] font-semibold">
                              <span className="text-maia-text-muted">[第{round.round}轮]</span>
                              <span className={round.success ? 'text-maia-success' : 'text-maia-danger'}>
                                {round.success ? '通过' : '失败'}
                              </span>
                            </div>
                            {round.stdout && (
                              <pre className="whitespace-pre-wrap break-all leading-relaxed mt-0.5 text-maia-text">{round.stdout}</pre>
                            )}
                            {round.stderr && (
                              <pre className="whitespace-pre-wrap break-all leading-relaxed mt-0.5 text-maia-danger">{round.stderr}</pre>
                            )}
                            {round.analysis && (
                              <pre className="text-maia-success whitespace-pre-wrap break-all mt-0.5 leading-relaxed">{round.analysis}</pre>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                    <div ref={logEndRef} />
                  </div>
                </div>
              )}

              {error && <div className="p-3 rounded-lg border border-maia-danger/30 bg-maia-danger-bg text-[13px] text-maia-danger">{error}</div>}
              {output && (<Card className="border-emerald-200 bg-emerald-50/30"><CardBody>
                <div className="flex items-center gap-1.5 mb-2">
                  <div className={`h-2 w-2 rounded-full ${output.status === 'success' ? 'bg-maia-success' : 'bg-maia-danger'}`} />
                  <span className="text-[13px] font-medium text-maia-text-secondary">{output.status === 'success' ? '执行成功' : '执行失败'}</span>
                </div>
                <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap max-h-[300px] overflow-auto bg-maia-bg rounded p-3 border border-maia-border">{JSON.stringify(output, null, 2)}</pre>
              </CardBody></Card>)}
            </div>
          </div>
        ) : (
          /* 单栏 */
          <div className="max-w-2xl mx-auto space-y-4">
          {/* 标签行：从标题栏移到这里，避免与工具名/版本号/操作按钮挤在一起 */}
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-maia-text-muted shrink-0">标签</span>
            <ToolTagsEditor toolId={tool.id} tags={tool.tags || []} />
          </div>
          {showSpec && (<Card className="border-maia-border"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-[13px] font-medium text-maia-text-secondary">MD 规范文档</span><button onClick={() => setShowSpec(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">{specMd || '加载中...'}</pre>
          </CardBody></Card>)}
          {showDemand && demandText && (<Card className="border-maia-border"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-[13px] font-medium text-maia-text-secondary">用户需求描述</span><button onClick={() => setShowDemand(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">{demandText}</pre>
          </CardBody></Card>)}
          {showReference && referenceCode && (<Card className="border-purple-200 bg-purple-50/30"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-[13px] font-medium text-maia-accent">参考代码</span><button onClick={() => setShowReference(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3 border border-maia-border">{referenceCode}</pre>
          </CardBody></Card>)}
          <div>
            <h3 className="text-sm font-semibold text-maia-text-heading mb-3">输入参数</h3>
            {inputs.length === 0 ? (<p className="text-[13px] text-maia-text-muted">此工具无需输入参数</p>) : (
              <div className="space-y-3">
                {inputs.map(f => (<div key={f.name}>
                  <label className="text-[12px] font-medium text-maia-text-secondary">{f.name}{f.required && <span className="text-maia-danger ml-0.5">*</span>}<span className="text-maia-text-muted font-normal ml-1">({f.type})</span></label>
                  {f.desc && <p className="text-[12px] text-maia-text-muted mb-1">{f.desc}</p>}
                  <div className="flex items-center gap-1.5">
                    <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                      placeholder={f.required ? '必填' : f.default || '可选'} className="flex-1 min-w-0 h-8 rounded border border-maia-border bg-maia-surface px-3 text-[13px] text-maia-text outline-none focus:border-maia-accent/40" />
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
          {error && <div className="p-3 rounded-lg border border-red-200 bg-red-50 text-[13px] text-maia-danger">{error}</div>}
          {output && (<Card className="border-emerald-200 bg-emerald-50/30"><CardBody>
            <div className="flex items-center gap-1.5 mb-2">
              <div className={`h-2 w-2 rounded-full ${output.status === 'success' ? 'bg-maia-success' : 'bg-maia-danger'}`} />
              <span className="text-[13px] font-medium text-maia-text-secondary">{output.status === 'success' ? '执行成功' : '执行失败'}</span>
            </div>
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap max-h-[300px] overflow-auto bg-maia-bg rounded p-3 border border-maia-border">{JSON.stringify(output, null, 2)}</pre>
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
function WorkspaceFilePicker({ paramName, currentValue, onClose, onSelect }: {
  paramName: string
  currentValue: string
  onClose: () => void
  onSelect: (path: string) => void
}) {
  const workspaceDatasets = useWorkspaceDatasetStore(s => s.datasets)
  const dataResources = useResourceStore(s => s.dataResources)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [filesMap, setFilesMap] = useState<Record<string, { name: string; path: string; size?: number }[]>>({})
  const [loadingFiles, setLoadingFiles] = useState<Record<string, boolean>>({})

  // 解析工作区数据集的 data_path：优先从 dataResources 匹配，否则调用后端详情接口
  const resolveDataPath = (id: string): string => {
    const hit = dataResources.find(d => d.id === id)
    return (hit && 'filePath' in hit && hit.filePath) ? hit.filePath : ''
  }

  const toggleFiles = async (id: string) => {
    if (expandedId === id) { setExpandedId(null); return }
    setExpandedId(id)
    if (!filesMap[id]) {
      setLoadingFiles(p => ({ ...p, [id]: true }))
      try {
        let dataPath = resolveDataPath(id)
        if (!dataPath) {
          const detail = await (await fetch(`/api/data/${id}`)).json()
          dataPath = (detail && detail.data_path) || ''
        }
        const res = await fetch(`/api/data/${id}/files`)
        const data = await res.json()
        const files = (data.files || []).map((f: { name: string; path: string; size?: number }) => ({
          ...f,
          path: f.path.startsWith(dataPath) ? f.path : `${dataPath}/${f.path}`,
        }))
        setFilesMap(p => ({ ...p, [id]: files }))
      } catch { setFilesMap(p => ({ ...p, [id]: [] })) }
      setLoadingFiles(p => ({ ...p, [id]: false }))
    }
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
        <div className="flex-1 overflow-auto p-3 space-y-1.5">
          {currentValue && (
            <div className="mb-2 px-2 py-1 rounded bg-maia-surface border border-maia-border text-[12px] text-maia-text-muted">
              当前值：<span className="text-maia-text font-mono">{currentValue}</span>
            </div>
          )}
          {workspaceDatasets.length === 0 ? (
            <div className="text-center py-10 text-[13px] text-maia-text-muted">
              工作区间暂无数据文件<br />请先在数据空间加载数据集
            </div>
          ) : (
            <>
              <p className="text-[12px] text-maia-text-muted px-1">工作区间数据（点击展开查看文件）</p>
              {workspaceDatasets.map(ds => {
                const files = filesMap[ds.id] || []
                const isExpanded = expandedId === ds.id
                return (
                  <div key={ds.id} className="rounded border border-maia-border bg-maia-surface overflow-hidden">
                    <button
                      className="w-full flex items-center gap-2 px-2.5 py-2 hover:bg-maia-bg transition-colors text-left"
                      onClick={() => toggleFiles(ds.id)}
                    >
                      {loadingFiles[ds.id] ? <Loader2 className="h-3.5 w-3.5 animate-spin text-maia-accent shrink-0" />
                        : isExpanded ? <ChevronDown className="h-3.5 w-3.5 text-maia-text-muted shrink-0" />
                        : <ChevronRight className="h-3.5 w-3.5 text-maia-text-muted shrink-0" />}
                      <Folder className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                      <span className="text-[13px] text-maia-text truncate flex-1">{ds.name}</span>
                      {resolveDataPath(ds.id) && <span className="text-[12px] text-maia-text-muted truncate max-w-[140px]">{resolveDataPath(ds.id)}</span>}
                    </button>
                    {isExpanded && (
                      <div className="border-t border-maia-border max-h-[220px] overflow-auto">
                        <button
                          className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-maia-accent/10 text-left"
                          onClick={() => onSelect(resolveDataPath(ds.id) || ds.name)}
                          title="选择数据集根目录"
                        >
                          <Folder className="h-3 w-3 text-maia-accent shrink-0" />
                          <span className="text-[12px] text-maia-text-secondary">使用数据集目录路径</span>
                        </button>
                        {files.length === 0 && !loadingFiles[ds.id] && (
                          <div className="px-3 py-2 text-[12px] text-maia-text-muted">该数据集内没有文件</div>
                        )}
                        {files.map((f, i) => (
                          <button
                            key={i}
                            className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-maia-accent/10 text-left"
                            onClick={() => onSelect(f.path)}
                            title={f.path}
                          >
                            <File className="h-3 w-3 text-maia-text-muted shrink-0" />
                            <span className="text-[12px] text-maia-text truncate flex-1">{f.name}</span>
                            {f.size != null && <span className="text-[12px] text-maia-text-muted shrink-0">{(f.size / 1024).toFixed(1)}KB</span>}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </>
          )}
        </div>
        <div className="px-4 py-2 border-t border-maia-border text-[12px] text-maia-text-muted shrink-0">
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
      await fetch(`/api/tool/${toolId}/tags`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tags: updated }),
      })
      setTags(updated)
    } catch { /* ignore */ }
    setSaving(false)
  }

  return (
    <div className="flex items-center gap-1">
      {tags.map(tag => (
        <span key={tag} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[12px] bg-maia-warning-bg text-maia-warning border border-maia-warning/30 font-medium">
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
            className="w-16 h-5 px-1 rounded border border-maia-accent/40 bg-maia-bg text-[12px] text-maia-text outline-none"
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
          className="text-[12px] text-maia-text-muted hover:text-maia-text ml-1"
        >
          完成
        </button>
      )}
    </div>
  )
}

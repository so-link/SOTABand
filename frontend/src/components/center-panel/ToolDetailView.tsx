import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Wrench, FileCode, FileText, X, Play, Loader2,
  CheckCheck, Save, Bot, Code, Pencil, Tag, Plus, Square,
} from 'lucide-react'
import { Highlight, themes } from 'prism-react-renderer'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useTabIndent } from '@/hooks/use-tab-indent'
import type { ToolResource } from '@/types/resources'

const BASE_URL = ''

interface InputField { name: string; type: string; required: boolean; default: string | null; desc: string }

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

export function ToolDetailView() {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const cachedToolForDetail = useResourceStore((s) => s.cachedToolForDetail)
  const setActiveView = useUIStore((s) => s.setActiveView)
  const tool = selectedResource?.type === 'tool' ? (selectedResource as ToolResource) : cachedToolForDetail
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
  const [output, setOutput] = useState<Record<string, unknown> | null>(null)
  const [isExecuting, setIsExecuting] = useState(false)
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

  if (!tool) {
    return (
      <div className="flex items-center justify-center h-full text-maia-text-muted text-sm">
        请在左侧工具空间选择一个工具
      </div>
    )
  }

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

  const handleSubmit = async () => {
    setIsExecuting(true)
    setError(null)
    setOutput(null)
    try {
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
      setOutput(await res.json())
    } catch (e) { setError(String(e)) }
    setIsExecuting(false)
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
                <h3 className="text-sm font-semibold text-maia-text-heading mb-3 tracking-wide">输入参数</h3>
                {inputs.length === 0 ? (<p className="text-xs text-maia-text-muted">此工具无需输入参数</p>) : (
                  <div className="space-y-3">
                    {inputs.map(f => (<div key={f.name}>
                      <label className="text-[11px] font-medium text-maia-text-secondary tracking-wide">
                        {f.name}{f.required && <span className="text-maia-danger ml-0.5">*</span>}<span className="text-maia-text-muted font-normal ml-1">({f.type})</span></label>
                      {f.desc && <p className="text-[10px] text-maia-text-muted mb-1">{f.desc}</p>}
                      <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                        placeholder={f.required ? '必填' : f.default || '可选'}
                        className="w-full h-8 rounded border border-maia-border bg-maia-surface px-3 text-[12px] text-maia-text outline-none focus:border-maia-accent/40" />
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
                  <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                    placeholder={f.required ? '必填' : f.default || '可选'} className="w-full h-8 rounded border border-maia-border bg-maia-surface px-3 text-[12px] text-maia-text outline-none focus:border-maia-accent/40" />
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
    </div>
  )
}

/** 工具标签编辑器（内联组件） */
function ToolTagsEditor({ toolId, tags: initialTags }: { toolId: string; tags: string[] }) {
  const [tags, setTags] = useState<string[]>(initialTags || [])
  const [editing, setEditing] = useState(false)
  const [newTag, setNewTag] = useState('')
  const [saving, setSaving] = useState(false)

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

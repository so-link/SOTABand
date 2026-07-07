import { useState, useEffect } from 'react'
import {
  Wrench, FileCode, FileText, X, Play, Loader2,
  CheckCheck, Save, Bot,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import type { ToolResource } from '@/types/resources'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

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
  const tool = selectedResource as ToolResource | null

  const [specMd, setSpecMd] = useState('')
  const [code, setCode] = useState('')
  const [editedCode, setEditedCode] = useState('')
  const [showSpec, setShowSpec] = useState(false)
  const [showCode, setShowCode] = useState(false)
  const [showDemand, setShowDemand] = useState(false)
  const [demandText, setDemandText] = useState('')
  const [formValues, setFormValues] = useState<Record<string, string>>({})
  const [output, setOutput] = useState<Record<string, unknown> | null>(null)
  const [isExecuting, setIsExecuting] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const [isUpdating, setIsUpdating] = useState(false)
  const [testResults, setTestResults] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [codeModified, setCodeModified] = useState(false)

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
        if (data.has_demand && data.demand_md) {
          setDemandText(data.demand_md)
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
      const res = await fetch(`${BASE_URL}/api/tool/test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specMd, toolId: tool.id, toolName: tool.name, code: editedCode }),
      })
      if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`)
      const data = await res.json()
      setTestResults(data.sandbox_results || {})
    } catch (e) { setError(String(e)) }
    setIsTesting(false)
  }

  const updateCode = async () => {
    if (!editedCode.trim()) return
    setIsUpdating(true)
    setError(null)
    try {
      const res = await fetch(`${BASE_URL}/api/tool/${tool.id}/update-code`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specMd, code: editedCode, toolId: tool.id, toolName: tool.name }),
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

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0 gap-2">
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">{tool.name}</span>
          <Badge variant="success">v{tool.version}</Badge>
        </div>
        <div className="flex items-center gap-2">
          {demandText && (
            <button onClick={() => { setShowDemand(!showDemand); if (showSpec) setShowSpec(false); if (showCode) setShowCode(false) }}
              className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline">
              <FileText className="h-3 w-3" />{showDemand ? '收起需求' : '查看需求描述'}
            </button>
          )}
          <button onClick={() => { setShowSpec(!showSpec); if (showCode) setShowCode(false); if (showDemand) setShowDemand(false) }}
            className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline">
            <FileText className="h-3 w-3" />{showSpec ? '收起 MD' : '查看 MD 文档'}
          </button>
          <button onClick={() => { setShowCode(!showCode); if (showSpec) setShowSpec(false); if (showDemand) setShowDemand(false) }}
            className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline">
            <FileCode className="h-3 w-3" />{showCode ? '收起代码' : '查看代码'}
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-4">
        <div className="max-w-2xl mx-auto space-y-4">

          {/* MD Spec panel */}
          {showSpec && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-maia-text-secondary tracking-wide">MD 规范文档</span>
                  <button onClick={() => setShowSpec(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                </div>
                <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">{specMd || '加载中...'}</pre>
              </CardBody>
            </Card>
          )}

          {/* Demand panel */}
          {showDemand && demandText && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-maia-text-secondary tracking-wide">用户需求描述</span>
                  <button onClick={() => setShowDemand(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                </div>
                <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">{demandText}</pre>
              </CardBody>
            </Card>
          )}

          {/* Code panel */}
          {showCode && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-maia-text-secondary tracking-wide">工具代码</span>
                    {codeModified && <Badge variant="warning">已修改</Badge>}
                  </div>
                  <button onClick={() => setShowCode(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                </div>
                <textarea
                  value={editedCode}
                  onChange={(e) => handleCodeChange(e.target.value)}
                  rows={16}
                  className="w-full rounded border border-maia-border bg-maia-bg px-3 py-2 text-[11px] font-mono leading-relaxed outline-none resize-y focus:border-maia-accent/40"
                  spellCheck={false}
                />

                {/* AI 修改区 */}
                <div className="mt-3 p-3 rounded-lg border border-purple-200 bg-purple-50/50">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Bot className="h-3.5 w-3.5 text-purple-500" />
                    <span className="text-[11px] font-medium text-purple-700 tracking-wide">AI 辅助修改</span>
                    <span className="text-[10px] text-purple-400">用自然语言描述修改需求，AI 保持接口不变自动修改代码</span>
                  </div>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder={'例如: "增加参数校验，空输入时返回友好提示"'}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          const btn = (e.target as HTMLInputElement).nextElementSibling as HTMLButtonElement
                          btn?.click()
                        }
                      }}
                      className="flex-1 h-7 rounded border border-purple-200 bg-white px-2 text-[11px] tracking-wide outline-none focus:border-purple-400"
                      id={`ai-modify-input-${tool.id}`}
                    />
                    <Button
                      size="sm"
                      className="bg-purple-500 hover:bg-purple-600 text-white h-7 text-[11px]"
                      onClick={async () => {
                        const input = document.getElementById(`ai-modify-input-${tool.id}`) as HTMLInputElement
                        const req = input?.value?.trim()
                        if (!req) return
                        input.value = ''
                        setError(null)
                        setIsTesting(true)
                        try {
                          const res = await fetch(`${BASE_URL}/api/tool/${tool.id}/modify-code`, {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ currentCode: editedCode, request: req }),
                          })
                          if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`)
                          const data = await res.json()
                          setEditedCode(data.modified_code)
                          setCodeModified(true)
                          setTestResults(null)
                        } catch (e) { setError(String(e)) }
                        setIsTesting(false)
                      }}
                    >
                      修改
                    </Button>
                  </div>
                </div>

                {/* Test / Update buttons */}
                <div className="flex items-center gap-2 mt-3">
                  <Button size="sm" variant="outline" onClick={runTests} disabled={isTesting || !editedCode.trim()}>
                    {isTesting ? <><Loader2 className="h-3 w-3 animate-spin" />测试中</> : <><Play className="h-3 w-3" />沙箱测试</>}
                  </Button>
                  <Button size="sm" onClick={updateCode} disabled={isUpdating || !codeModified || failed.length > 0}>
                    {isUpdating ? <><Loader2 className="h-3 w-3 animate-spin" />更新中</> : <><Save className="h-3 w-3" />更新代码</>}
                  </Button>
                </div>

                {/* Test results */}
                {testResults && (
                  <div className="mt-2 space-y-1">
                    {passed.map((m, i) => <div key={i} className="text-[10px] text-maia-success flex items-center gap-1"><CheckCheck className="h-3 w-3" />{m}</div>)}
                    {failed.map((m, i) => <div key={i} className="text-[10px] text-maia-danger flex items-center gap-1"><X className="h-3 w-3" />{m}</div>)}
                  </div>
                )}
              </CardBody>
            </Card>
          )}

          {/* Input form */}
          <div>
            <h3 className="text-sm font-semibold text-maia-text-heading mb-3 tracking-wide">输入参数</h3>
            {inputs.length === 0 ? (
              <p className="text-xs text-maia-text-muted">此工具无需输入参数</p>
            ) : (
              <div className="space-y-3">
                {inputs.map(f => (
                  <div key={f.name}>
                    <label className="text-[11px] font-medium text-maia-text-secondary tracking-wide">
                      {f.name}{f.required && <span className="text-maia-danger ml-0.5">*</span>}
                      <span className="text-maia-text-muted font-normal ml-1">({f.type})</span>
                    </label>
                    {f.desc && <p className="text-[10px] text-maia-text-muted mb-1">{f.desc}</p>}
                    <input type="text" value={formValues[f.name] || ''}
                      onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                      placeholder={f.required ? '必填' : f.default || '可选'}
                      className="w-full h-8 rounded border border-maia-border bg-white px-3 text-[12px] tracking-wide outline-none focus:border-maia-accent/40" />
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end mt-4">
              <Button onClick={handleSubmit} disabled={isExecuting}>
                {isExecuting ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />执行中...</> : <><Play className="h-3.5 w-3.5" />提交执行</>}
              </Button>
            </div>
          </div>

          {/* Error */}
          {error && <div className="p-3 rounded-lg border border-red-200 bg-red-50 text-xs text-maia-danger">{error}</div>}

          {/* Output */}
          {output && (
            <Card className="border-emerald-200 bg-emerald-50/30">
              <CardBody>
                <div className="flex items-center gap-1.5 mb-2">
                  <div className={`h-2 w-2 rounded-full ${output.status === 'success' ? 'bg-maia-success' : 'bg-maia-danger'}`} />
                  <span className="text-xs font-medium text-maia-text-secondary tracking-wide">
                    {output.status === 'success' ? '执行成功' : '执行失败'}
                  </span>
                </div>
                <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[300px] overflow-auto bg-white rounded p-3 border border-maia-border">
                  {JSON.stringify(output, null, 2)}
                </pre>
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

import { useState, useEffect, useRef, useCallback, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Wrench, ArrowRight, ArrowLeft, CheckCircle2, XCircle,
  Loader2, FileCode, Play, Rocket, Tag, Plus, X,
} from 'lucide-react'
import { Highlight, themes } from 'prism-react-renderer'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useUIStore } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'

// ── 可拖拽分割面板 ──

function ResizeHandle({ onResize }: { onResize: (delta: number) => void }) {
  const handleRef = useRef<HTMLDivElement>(null)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startY = e.clientY
    const handleMouseMove = (ev: MouseEvent) => {
      onResize(ev.clientY - startY)
    }
    const handleMouseUp = () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }, [onResize])

  return (
    <div
      ref={handleRef}
      onMouseDown={onMouseDown}
      className="h-1.5 cursor-row-resize bg-maia-border hover:bg-maia-accent/40 transition-colors shrink-0 group relative"
    >
      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <div className="w-6 h-0.5 rounded-full bg-maia-accent/50" />
      </div>
    </div>
  )
}

export function ToolEditorView() {
  const store = useToolEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">工具编辑器</span>
        </div>
        <button onClick={() => { store.reset(); setActiveView('chat') }} className="text-maia-text-muted hover:text-maia-text text-sm">× 关闭</button>
      </div>

      {/* Step indicators */}
      <div className="flex items-center gap-0 px-4 py-2 border-b border-maia-border bg-maia-surface shrink-0">
        {[1, 2, 3, 4].map((s, i) => (
          <div key={s} className="flex items-center gap-0">
            <div className={`flex items-center gap-1.5 text-[11px] font-medium tracking-wide px-2 py-1 rounded-full transition-colors ${
              store.step === s ? 'bg-maia-accent text-white' : store.step > s ? 'bg-maia-success/10 text-maia-success' : 'text-maia-text-muted'
            }`}>
              {store.step > s ? <CheckCircle2 className="h-3 w-3" /> : <span className="text-[10px]">{s}</span>}
              {['描述', '审阅', '核验', '注册'][i]}
            </div>
            {i < 3 && <div className="w-6 h-[1px] bg-maia-border mx-1" />}
          </div>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-4">
        {store.step === 1 && <Step1 />}
        {store.step === 2 && <Step2 />}
        {store.step === 3 && <Step3 />}
        {store.step === 4 && <Step4 />}
      </div>
    </div>
  )
}

function Step1() {
  const { description, setDescription, referenceCode, setReferenceCode, generateSpec, isGenerating, error } = useToolEditorStore()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const BASE = ''

  // Autocomplete data
  const [apiItems, setApiItems] = useState<Array<{name:string,id:string}>>([])
  const [toolItems, setToolItems] = useState<Array<{name:string,id:string}>>([])

  // Dropdown state
  const [show, setShow] = useState(false)
  const [filtered, setFiltered] = useState<Array<{name:string,id:string}>>([])
  const [selIdx, setSelIdx] = useState(0)
  const [ddPos, setDdPos] = useState({ top: 0, left: 0 })
  const [trigger, setTrigger] = useState<'@' | '$'>('@')
  const [tRange, setTRange] = useState({ start: 0, end: 0 })

  useEffect(() => {
    fetch(`${BASE}/api/apis/list`).then(r => r.json()).then(d => {
      const items = ((d.apis||[]) as Array<Record<string,unknown>>).map((a:Record<string,unknown>) => ({name:(a.name as string)||(a.id as string)||'', id:(a.id as string)||''}))
      setApiItems(items)
    }).catch(()=>{})
    fetch(`${BASE}/api/tool/list`).then(r => r.json()).then(d => {
      const items = (((d as Record<string,unknown>).tools||[]) as Array<Record<string,unknown>>).map((t:Record<string,unknown>) => ({name:(t.name as string)||(t.id as string)||'', id:(t.id as string)||''}))
      setToolItems(items)
    }).catch(()=>{})
  }, [])

  function getPos() {
    const ta = textareaRef.current
    if (!ta) return { top: 0, left: 0 }
    const r = ta.getBoundingClientRect()
    return { top: r.bottom + 4, left: r.left }
  }

  function doShow(value: string, pos: number) {
    let trig: '@' | '$' | null = null; let start = -1
    for (let i = pos - 1; i >= 0; i--) {
      if (value[i] === ' ' || value[i] === '\n') break
      if (value[i] === '@' || value[i] === '$') { trig = value[i] as '@' | '$'; start = i; break }
    }
    if (!trig) { setShow(false); return }
    const q = value.substring(start + 1, pos).toLowerCase()
    const src = trig === '@' ? apiItems : toolItems
    const f = q ? src.filter(it => it.name.toLowerCase().includes(q) || it.id.toLowerCase().includes(q)) : src
    setTrigger(trig); setFiltered(f); setSelIdx(0); setTRange({start, end: pos}); setDdPos(getPos()); setShow(true)
  }

  function doSelect(item: {name:string,id:string}) {
    const fmt = trigger === '@' ? `【${item.name}】` : `【【${item.name}】】`
    const before = description.substring(0, tRange.start)
    const after = description.substring(tRange.end)
    const newVal = before + fmt + after
    const newPos = before.length + fmt.length
    setDescription(newVal); setShow(false)
    requestAnimationFrame(() => { const ta = textareaRef.current; if (ta) { ta.focus(); ta.setSelectionRange(newPos, newPos) } })
  }

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setDescription(e.target.value)
    doShow(e.target.value, e.target.selectionStart)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (!show || filtered.length === 0) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelIdx(i => (i + 1) % filtered.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSelIdx(i => (i - 1 + filtered.length) % filtered.length) }
    else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); if (filtered[selIdx]) doSelect(filtered[selIdx]) }
    else if (e.key === 'Escape') { e.preventDefault(); setShow(false) }
  }

  const isLoading = trigger === '@' ? apiItems.length === 0 : toolItems.length === 0

  return (
    <div className="max-w-2xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 1: 描述工具需求</h3>
      <p className="text-sm text-maia-text-secondary mb-4">
        用自然语言描述你需要什么工具。输入 <code className="text-maia-accent bg-maia-accent/5 px-1 rounded">@</code> 引用系统 API，输入 <code className="text-maia-accent bg-maia-accent/5 px-1 rounded">$</code> 引用注册工具。
      </p>
      <textarea ref={textareaRef} value={description} onChange={handleChange} onKeyDown={handleKeyDown}
        placeholder='例如: "我需要一个EEG带通滤波器，支持delta/theta/alpha/beta/gamma频段，Butterworth滤波器，输入EDF文件，输出滤波后的EDF文件"'
        rows={15} className="w-full rounded-lg border border-maia-border bg-maia-surface px-4 py-3 text-[13px] tracking-wide outline-none resize-none focus:border-maia-accent/40 placeholder:text-maia-text-muted" />
      
      {/* 参考代码输入 */}
      <div className="mt-3">
        <div className="text-[10px] text-maia-text-muted uppercase tracking-wider font-semibold mb-1">参考代码（可选，将完整复制到 MD 执行流程中）</div>
        <textarea value={referenceCode} onChange={e => setReferenceCode(e.target.value)}
          placeholder="粘贴参考代码..."
          rows={8} className="w-full rounded-lg border border-maia-border bg-maia-bg px-4 py-3 text-[12px] font-mono tracking-wide outline-none resize-none focus:border-maia-accent/40 placeholder:text-maia-text-muted" />
      </div>

      {show && (isLoading || filtered.length > 0) && createPortal(
        <div className="fixed z-[9999] w-72 max-h-48 overflow-y-auto rounded-lg border border-maia-border bg-maia-surface shadow-lg py-1" style={{ top: ddPos.top, left: ddPos.left }}>
          {isLoading ? <div className="px-3 py-2 text-[12px] text-maia-text-muted">加载中...</div> :
            filtered.map((item, i) => (
              <button key={item.id} className={`w-full text-left px-3 py-1.5 flex flex-col gap-0 transition-colors ${i === selIdx ? 'bg-maia-accent/10 text-maia-accent' : 'hover:bg-maia-bg text-maia-text'}`}
                onMouseDown={e => { e.preventDefault(); doSelect(item) }}>
                <span className="text-[12px] font-medium tracking-wide truncate">{trigger === '@' ? `【${item.name}】` : `【【${item.name}】】`}</span>
                <span className="text-[10px] text-maia-text-muted truncate">{item.id}</span>
              </button>
            ))}
        </div>, document.body)}
      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger"><XCircle className="h-3 w-3" />{error}</div>}
      <div className="flex justify-end mt-4">
        <Button onClick={generateSpec} disabled={!description.trim() || isGenerating}>
          {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />生成中...</> : <>生成 MD 文档<ArrowRight className="h-3.5 w-3.5" /></>}
        </Button>
      </div>
    </div>
  )
}

function Step2() {
  const { generatedMd, setGeneratedMd, tags, addTag, removeTag, generateCode, setStep, isGenerating, error } = useToolEditorStore()
  const [editing, setEditing] = useState(false)
  const [newTag, setNewTag] = useState('')

  const handleAddTag = () => {
    const t = newTag.trim()
    if (t) { addTag(t); setNewTag(''); setEditing(false) }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 2: 审阅 & 编辑 MD 规范文档</h3>
      <p className="text-sm text-maia-text-secondary mb-3">以下是 AI 生成的工具规范文档，你可以直接编辑修改。</p>

      {/* 标签 — 与 ToolDetailView 保持一致的内联风格 */}
      <div className="flex items-center gap-1 mb-3">
        <Tag className="h-3.5 w-3.5 text-amber-400 shrink-0" />
        {tags.map(tag => (
          <span key={tag} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-amber-100 text-amber-700 border border-amber-300 font-medium">
            {tag}
            <button onClick={() => removeTag(tag)} className="hover:text-red-400"><X className="h-2.5 w-2.5" /></button>
          </span>
        ))}
        {editing ? (
          <form onSubmit={e => { e.preventDefault(); handleAddTag() }} className="inline-flex">
            <input
              value={newTag}
              onChange={e => setNewTag(e.target.value)}
              placeholder="+标签"
              className="w-16 h-5 px-1 rounded border border-amber-300 bg-maia-bg text-[10px] text-maia-text outline-none"
              autoFocus
              onBlur={() => { if (!newTag.trim()) setEditing(false) }}
            />
          </form>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="inline-flex items-center justify-center h-5 w-5 rounded border border-dashed border-maia-border text-maia-text-muted hover:text-amber-500 hover:border-amber-400"
            title="添加标签"
          >
            <Plus className="h-3 w-3" />
          </button>
        )}
      </div>

      <textarea value={generatedMd} onChange={(e) => setGeneratedMd(e.target.value)} rows={18}
        className="w-full rounded-lg border border-maia-border bg-maia-bg/50 px-4 py-3 text-[12px] font-mono outline-none resize-y focus:border-maia-accent/40" spellCheck={false} />
      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger"><XCircle className="h-3 w-3" />{error}</div>}
      <div className="flex justify-between mt-4">
        <Button variant="outline" onClick={() => setStep(1)}><ArrowLeft className="h-3.5 w-3.5" />返回修改需求</Button>
        <Button onClick={generateCode} disabled={!generatedMd.trim() || isGenerating}>
          {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />生成中...</> : <>生成代码和测试<ArrowRight className="h-3.5 w-3.5" /></>}
        </Button>
      </div>
    </div>
  )
}

function Step3() {
  const { generatedCode, params, testInputs, testOutput, registerTool, runTest, stopTest, autoDebug, stopAutoDebug,
          setStep, isGenerating, isTesting, isAutoDebugging, error, debugRounds, debugStream, setTestInput } = useToolEditorStore()
  const logEndRef = useRef<HTMLDivElement>(null)
  const fileInputRefs = useRef<Map<string, HTMLInputElement>>(new Map())
  const [uploadedFiles, setUploadedFiles] = useState<Map<string, File>>(new Map())
  // 面板高度（px），初始值
  const [codeHeight, setCodeHeight] = useState(300)
  const [logHeight, setLogHeight] = useState(180)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'auto' }) }, [debugRounds])

  const handleFileUpload = (paramName: string, e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      const next = new Map(uploadedFiles); next.set(paramName, file)
      setUploadedFiles(next)
      setTestInput(paramName, file.name)
    }
  }

  const handleRunTest = () => {
    const files = uploadedFiles.size > 0 ? Array.from(uploadedFiles.values()) : undefined
    runTest(files)
  }

  const handleAutoDebug = () => {
    const files = uploadedFiles.size > 0 ? Array.from(uploadedFiles.values()) : undefined
    autoDebug(files)
  }

  const isPathParam = (name: string, type: string) =>
    type.toLowerCase().includes('path') || name.toLowerCase().includes('path') || name.toLowerCase().includes('file')

  // 可拖拽分隔线处理
  const handleCodeResize = useCallback((delta: number) => {
    setCodeHeight(h => Math.max(120, Math.min(600, h + delta)))
  }, [])

  const handleLogResize = useCallback((delta: number) => {
    setLogHeight(h => Math.max(80, Math.min(500, h - delta)))
  }, [])

  return (
    <div className="max-w-5xl mx-auto h-full flex flex-col" ref={containerRef}>
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide shrink-0">Step 3: 代码预览 & 沙箱测试</h3>

      {/* 上半部分：代码 + 测试区（水平分割） */}
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="grid grid-cols-5 gap-4 flex-1 min-h-0">
          {/* 左：代码预览 */}
          <div className="col-span-3 flex flex-col min-h-0">
            <div className="flex items-center gap-1.5 mb-2 shrink-0">
              <FileCode className="h-3.5 w-3.5 text-amber-400" />
              <span className="text-xs font-medium text-maia-text-secondary tracking-wide">生成代码</span>
            </div>
            <div className="flex-1 min-h-0 rounded-lg border border-maia-border bg-[#1e1e1e] overflow-auto" style={{ maxHeight: codeHeight }}>
              <Highlight theme={themes.vsDark} code={generatedCode || '# 等待生成代码...'} language="python">
                {({ style, tokens, getLineProps, getTokenProps }) => (
                  <pre style={style} className="px-3 py-2 text-[11px] font-mono leading-relaxed m-0">
                    {tokens.map((line, i) => (
                      <div key={i} {...getLineProps({ line })}>
                        <span className="inline-block w-8 text-right mr-3 text-white/20 select-none text-[10px]">{i + 1}</span>
                        {line.map((token, key) => (
                          <span key={key} {...getTokenProps({ token })} />
                        ))}
                      </div>
                    ))}
                  </pre>
                )}
              </Highlight>
            </div>
          </div>

          {/* 右：沙箱测试 */}
          <div className="col-span-2 flex flex-col min-h-0" style={{ maxHeight: codeHeight }}>
            <div className="flex items-center gap-1.5 mb-2 shrink-0">
              <Play className="h-3.5 w-3.5 text-amber-400" />
              <span className="text-xs font-medium text-maia-text-secondary tracking-wide">沙箱测试</span>
            </div>
            <div className="flex-1 min-h-0 overflow-auto">
              {/* 测试输入表单 */}
              {params.length > 0 && (
                <div className="mb-2 space-y-2">
                  <div className="text-[10px] text-maia-text-muted uppercase tracking-wider font-semibold">测试参数</div>
                  {params.map((p) => (
                    <div key={p.name}>
                      <div className="text-[10px] text-maia-text-muted mb-0.5 flex items-center gap-1">
                        {p.name} <span className="text-maia-accent/60">({p.type})</span>
                        {p.required && <span className="text-maia-danger">*</span>}
                      </div>
                      {isPathParam(p.name, p.type) ? (
                        <div className="flex gap-1">
                          <input type="text" value={testInputs[p.name] || ''}
                            onChange={e => setTestInput(p.name, e.target.value)}
                            className="flex-1 h-7 rounded border border-maia-border px-2 text-[11px] font-mono outline-none focus:border-maia-accent"
                            placeholder={p.desc || p.name} />
                          <button
                            onClick={() => fileInputRefs.current.get(p.name)?.click()}
                            className="shrink-0 h-7 px-2 text-[10px] rounded border border-maia-border hover:bg-maia-sidebar-hover text-maia-text-secondary tracking-wider"
                          >📎 上传</button>
                          <input type="file" ref={el => { if (el) fileInputRefs.current.set(p.name, el) }}
                            onChange={e => handleFileUpload(p.name, e)} className="hidden" />
                        </div>
                      ) : (
                        <input type="text" value={testInputs[p.name] || ''}
                          onChange={e => setTestInput(p.name, e.target.value)}
                          className="w-full h-7 rounded border border-maia-border px-2 text-[11px] font-mono outline-none focus:border-maia-accent"
                          placeholder={p.desc || p.name} />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* 测试输出 */}
              {testOutput && (
                <Card className="border-maia-border mt-2">
                  <CardBody>
                    <div className="text-[10px] text-maia-text-muted uppercase tracking-wider font-semibold mb-1">执行输出</div>
                    <pre className={`text-[10px] font-mono leading-relaxed max-h-[120px] overflow-auto whitespace-pre-wrap ${testOutput.success ? 'text-maia-success' : 'text-maia-danger'}`}>
                      {testOutput.stdout || '(空)'}
                    </pre>
                    {testOutput.stderr && (
                      <>
                        <div className="text-[10px] text-maia-warning uppercase tracking-wider font-semibold mt-1.5 mb-0.5">stderr</div>
                        <pre className="text-[10px] font-mono text-maia-danger leading-relaxed max-h-[80px] overflow-auto whitespace-pre-wrap">{testOutput.stderr}</pre>
                      </>
                    )}
                  </CardBody>
                </Card>
              )}

              {!testOutput && !isTesting && !isAutoDebugging && <div className="text-[11px] text-maia-text-muted mt-2">填写测试参数后运行</div>}
              {isTesting && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-accent"><Loader2 className="h-3 w-3 animate-spin" />测试中...</div>}

              {/* 按钮 */}
              <div className="flex gap-1.5 mt-2">
                {isTesting ? (
                  <Button variant="danger" size="sm" className="flex-1" onClick={stopTest}><XCircle className="h-3.5 w-3.5" /> 停止测试</Button>
                ) : (
                  <Button variant="outline" size="sm" className="flex-1" onClick={handleRunTest} disabled={isTesting || isAutoDebugging}>
                    <Play className="h-3.5 w-3.5" /> 运行测试
                  </Button>
                )}
                {isAutoDebugging ? (
                  <Button variant="danger" size="sm" onClick={stopAutoDebug}><XCircle className="h-3.5 w-3.5" /> 停止调试</Button>
                ) : (
                  <Button size="sm" onClick={handleAutoDebug} disabled={isTesting || isAutoDebugging}
                    className="bg-amber-600 hover:bg-amber-700"><Rocket className="h-3.5 w-3.5" /> 自动调试
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 可拖拽分隔线 — 代码/测试区 ↔ 调试日志 */}
        {(isAutoDebugging || debugRounds.length > 0) && (
          <ResizeHandle onResize={handleLogResize} />
        )}

        {/* 下半部分：自动调试日志 */}
        {(isAutoDebugging || debugRounds.length > 0) && (
          <div className="shrink-0 rounded-lg border border-maia-border bg-maia-bg/50 overflow-auto" style={{ height: logHeight }}>
            <div className="px-3 py-2 text-[11px] font-semibold text-maia-text-secondary tracking-wider border-b border-maia-border sticky top-0 bg-maia-bg/90 z-10">
              🧠 自动调试日志 {isAutoDebugging && <Loader2 className="h-3 w-3 animate-spin inline ml-1" />}
            </div>
            <div className="p-2 space-y-1.5 font-mono text-[10px] text-maia-text-secondary">
              {debugStream && (
                <pre className="whitespace-pre-wrap break-all leading-relaxed text-[10px]">{debugStream}</pre>
              )}
              {debugRounds.map((round, i) => (
                <div key={i}>
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="text-maia-text-muted shrink-0">[第{round.round}轮]</span>
                    <span className={round.success ? 'text-maia-success' : 'text-maia-danger'}>
                      {round.success ? '✅ 通过' : '❌ 失败'}
                    </span>
                    {round.stdout && (
                      <span className="text-maia-text-secondary truncate max-w-[200px]">{round.stdout.slice(0, 80)}</span>
                    )}
                  </div>
                  {round.analysis && (
                    <pre className="text-green-400 font-mono text-[10px] whitespace-pre-wrap break-all ml-14 mt-0.5 leading-relaxed">{round.analysis}</pre>
                  )}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        )}
      </div>

      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger shrink-0"><XCircle className="h-3 w-3" />{error}</div>}

      <div className="flex justify-between mt-4 shrink-0">
        <Button variant="outline" onClick={() => setStep(2)} disabled={isAutoDebugging}><ArrowLeft className="h-3.5 w-3.5" />返回修改 MD</Button>
        <div className="flex gap-2">
          <Button variant="danger" onClick={() => setStep(1)} disabled={isAutoDebugging}><XCircle className="h-3.5 w-3.5" />拒绝</Button>
          <Button onClick={registerTool} disabled={isGenerating || isAutoDebugging}>
            {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />注册中...</> : <><CheckCircle2 className="h-3.5 w-3.5" />批准并注册发布</>}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Step4() {
  const { registeredId, reset } = useToolEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)
  return (
    <div className="max-w-md mx-auto text-center py-12">
      <div className="flex justify-center mb-4"><div className="flex items-center justify-center h-16 w-16 rounded-full bg-maia-success/10"><Rocket className="h-8 w-8 text-maia-success" /></div></div>
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">工具已发布！</h3>
      <Card className="border-maia-border mt-4"><CardBody><div className="space-y-2 text-left"><div className="flex justify-between text-xs"><span className="text-maia-text-muted">Tool ID</span><span className="font-mono text-maia-text">{registeredId}</span></div><div className="flex justify-between text-xs"><span className="text-maia-text-muted">状态</span><Badge variant="success">已注册</Badge></div></div></CardBody></Card>
      <div className="flex gap-3 justify-center mt-6">
        <Button variant="outline" onClick={() => { reset(); setActiveView('chat') }}>返回对话</Button>
        <Button onClick={() => reset()}><Wrench className="h-3.5 w-3.5" />创建新工具</Button>
      </div>
    </div>
  )
}

// ─ 测试输入/输出详情 ─

// @ts-expect-error - reserved for future use
function TestDetail({ detail }: { detail?: Record<string, unknown> }) {
  if (!detail) return null
  const input = detail.input as Record<string, unknown> | undefined
  const output = detail.output as Record<string, unknown> | undefined
  const error = detail.error as string | undefined

  return (
    <div className="mt-3 space-y-2">
      {input && (
        <div>
          <div className="text-[10px] text-maia-text-muted mb-0.5 uppercase tracking-wider">测试输入</div>
          <pre className="rounded border border-maia-border bg-maia-bg px-2 py-1.5 text-[10px] font-mono leading-relaxed max-h-[80px] overflow-auto whitespace-pre-wrap">
            {JSON.stringify(input, null, 2)}
          </pre>
        </div>
      )}
      {output && (
        <div>
          <div className="text-[10px] text-maia-success mb-0.5 uppercase tracking-wider">执行输出</div>
          <pre className="rounded border border-emerald-200 bg-emerald-50/50 px-2 py-1.5 text-[10px] font-mono leading-relaxed max-h-[120px] overflow-auto whitespace-pre-wrap">
            {typeof output === 'string' ? output : JSON.stringify(output, null, 2)}
          </pre>
        </div>
      )}
      {error && (
        <div>
          <div className="text-[10px] text-maia-danger mb-0.5 uppercase tracking-wider">错误</div>
          <pre className="rounded border border-red-200 bg-red-50/50 px-2 py-1.5 text-[10px] font-mono max-h-[80px] overflow-auto whitespace-pre-wrap">{error}</pre>
        </div>
      )}
    </div>
  )
}

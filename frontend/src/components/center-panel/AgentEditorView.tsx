import { useState, useRef, useCallback, useEffect, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Bot, ArrowRight, ArrowLeft, CheckCircle2, XCircle,
  Loader2, FileCode, Play, CheckCheck, Rocket,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useUIStore } from '@/stores/ui-store'
import { useAgentEditorStore } from '@/stores/agent-editor-store'

export function AgentEditorView() {
  const store = useAgentEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-maia-accent" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">
            Agent 编辑器
          </span>
        </div>
        <button
          onClick={() => {
            store.reset()
            setActiveView('chat')
          }}
          className="text-maia-text-muted hover:text-maia-text text-sm"
        >
          × 关闭
        </button>
      </div>

      {/* Step indicators */}
      <div className="flex items-center gap-0 px-4 py-2 border-b border-maia-border bg-white shrink-0">
        {[1, 2, 3, 4].map((s, i) => (
          <div key={s} className="flex items-center gap-0">
            <div
              className={`flex items-center gap-1.5 text-[11px] font-medium tracking-wide px-2 py-1 rounded-full transition-colors ${
                store.step === s
                  ? 'bg-maia-accent text-white'
                  : store.step > s
                    ? 'bg-maia-success/10 text-maia-success'
                    : 'text-maia-text-muted'
              }`}
            >
              {store.step > s ? (
                <CheckCircle2 className="h-3 w-3" />
              ) : (
                <span className="text-[10px]">{s}</span>
              )}
              {['描述', '审阅', '核验', '注册'][i]}
            </div>
            {i < 3 && <div className="w-6 h-[1px] bg-maia-border mx-1" />}
          </div>
        ))}
      </div>

      {/* Step content */}
      <div className="flex-1 min-h-0 overflow-auto p-4">
        {store.step === 1 && <Step1Description />}
        {store.step === 2 && <Step2Review />}
        {store.step === 3 && <Step3Verify />}
        {store.step === 4 && <Step4Done />}
      </div>
    </div>
  )
}

// ── Step 1: 描述需求 (带 @API / $工具 自动补全) ──────────────────

interface AcItem {
  name: string
  id: string
}

function Step1Description() {
  const { description, setDescription, generateSpec, isGenerating, error } =
    useAgentEditorStore()

  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Autocomplete data — fetched once on mount, kept in local state
  const [apiItems, setApiItems] = useState<AcItem[]>([])
  const [toolItems, setToolItems] = useState<AcItem[]>([])

  // Dropdown state
  const [show, setShow] = useState(false)
  const [filtered, setFiltered] = useState<AcItem[]>([])
  const [selIdx, setSelIdx] = useState(0)
  const [ddPos, setDdPos] = useState({ top: 0, left: 0 })
  const [trigger, setTrigger] = useState<'@' | '$'>('@')
  const [tRange, setTRange] = useState({ start: 0, end: 0 })

  // ═══ Fetch data on mount ═══
  useEffect(() => {
    const BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

    fetch(`${BASE}/api/apis/list`)
      .then((r) => r.json())
      .then((d) => {
        const items = ((d.apis || []) as Array<Record<string, unknown>>).map(
          (a: Record<string, unknown>) => ({
            name: (a.name as string) || (a.id as string) || '',
            id: (a.id as string) || '',
          })
        )
        console.log('[AgentEditor] APIs loaded:', items.length)
        setApiItems(items)
      })
      .catch((err) => console.warn('[AgentEditor] API fetch failed:', err))

    fetch(`${BASE}/api/tool/list`)
      .then((r) => r.json())
      .then((d) => {
        const items = (
          (d.tools as Array<Record<string, unknown>>) || []
        ).map((t: Record<string, unknown>) => ({
          name: (t.name as string) || (t.id as string) || '',
          id: (t.id as string) || '',
        }))
        console.log('[AgentEditor] Tools loaded:', items.length)
        setToolItems(items)
      })
      .catch((err) => console.warn('[AgentEditor] Tool fetch failed:', err))
  }, [])

  // ═══ Helpers ═══

  function getCaretPos() {
    const ta = textareaRef.current
    if (!ta) return { top: 0, left: 0 }
    const r = ta.getBoundingClientRect()
    // Position dropdown right below the textarea, aligned left
    const pos = { top: r.bottom + 4, left: r.left }
    console.log('[AgentEditor] Dropdown position:', pos, 'textarea rect:', r)
    return pos
  }

  function doShow(value: string, pos: number) {
    // Scan back from cursor for @ or $
    let trig: '@' | '$' | null = null
    let start = -1
    for (let i = pos - 1; i >= 0; i--) {
      if (value[i] === ' ' || value[i] === '\n') break
      if (value[i] === '@' || value[i] === '$') {
        trig = value[i] as '@' | '$'
        start = i
        break
      }
    }

    if (!trig) {
      setShow(false)
      return
    }

    const q = value.substring(start + 1, pos).toLowerCase()
    const src = trig === '@' ? apiItems : toolItems
    const f = q
      ? src.filter(
          (it) => it.name.toLowerCase().includes(q) || it.id.toLowerCase().includes(q)
        )
      : src

    setTrigger(trig)
    setFiltered(f)
    setSelIdx(0)
    setTRange({ start, end: pos })
    setDdPos(getCaretPos())
    setShow(true)

    console.log(
      `[AgentEditor] Trigger '${trig}' at ${start}, q="${q}", matches=${f.length}`
    )
  }

  function doSelect(item: AcItem) {
    const fmt = trigger === '@' ? `【${item.name}】` : `【【${item.name}】】`
    const before = description.substring(0, tRange.start)
    const after = description.substring(tRange.end)
    const newVal = before + fmt + after
    const newPos = before.length + fmt.length
    setDescription(newVal)
    setShow(false)
    requestAnimationFrame(() => {
      const ta = textareaRef.current
      if (ta) {
        ta.focus()
        ta.setSelectionRange(newPos, newPos)
      }
    })
  }

  // ═══ Event handlers ═══

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setDescription(e.target.value)
    doShow(e.target.value, e.target.selectionStart)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (!show || filtered.length === 0) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelIdx((i) => (i + 1) % filtered.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelIdx((i) => (i - 1 + filtered.length) % filtered.length)
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      if (filtered[selIdx]) doSelect(filtered[selIdx])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setShow(false)
    }
  }

  const isLoading = trigger === '@' ? apiItems.length === 0 : toolItems.length === 0

  // ═══ Render ═══
  return (
    <div className="max-w-2xl mx-auto relative">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">
        Step 1: 描述你的 Agent 需求
      </h3>
      <p className="text-sm text-maia-text-secondary mb-4">
        用自然语言描述你需要什么样的 Agent，系统会调用大模型自动生成标准化的 MD 规范文档。
        输入 <code className="text-maia-accent bg-maia-accent/5 px-1 rounded">@</code> 引用系统 API，
        输入 <code className="text-maia-accent bg-maia-accent/5 px-1 rounded">$</code> 引用注册工具。
      </p>

      <textarea
        ref={textareaRef}
        value={description}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={'例如: "我需要一个能分析EEG数据、检测异常信号、自动生成可视化报告的Agent"'}
        rows={6}
        className="w-full rounded-lg border border-maia-border bg-white px-4 py-3 text-[13px] tracking-wide outline-none resize-none focus:border-maia-accent/40 placeholder:text-maia-text-muted"
      />

      {/* Dropdown via Portal to avoid parent clipping */}
      {show &&
        (isLoading || filtered.length > 0) &&
        createPortal(
          <div
            className="fixed z-[9999] w-72 max-h-48 overflow-y-auto rounded-lg border border-maia-border bg-white shadow-lg py-1"
            style={{ top: ddPos.top, left: ddPos.left }}
          >
            {isLoading ? (
              <div className="px-3 py-2 text-[12px] text-maia-text-muted">加载中...</div>
            ) : (
              filtered.map((item, i) => (
                <button
                  key={item.id}
                  className={`w-full text-left px-3 py-1.5 flex flex-col gap-0 transition-colors ${
                    i === selIdx
                      ? 'bg-maia-accent/10 text-maia-accent'
                      : 'hover:bg-maia-bg text-maia-text'
                  }`}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    doSelect(item)
                  }}
                >
                  <span className="text-[12px] font-medium tracking-wide truncate">
                    {trigger === '@' ? `【${item.name}】` : `【【${item.name}】】`}
                  </span>
                  <span className="text-[10px] text-maia-text-muted truncate">{item.id}</span>
                </button>
              ))
            )}
          </div>,
          document.body
        )}

      {error && (
        <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger">
          <XCircle className="h-3 w-3" />
          {error}
        </div>
      )}

      <div className="flex justify-end mt-4">
        <Button onClick={generateSpec} disabled={!description.trim() || isGenerating}>
          {isGenerating ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              生成中...
            </>
          ) : (
            <>
              生成 MD 文档
              <ArrowRight className="h-3.5 w-3.5" />
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

// ── Step 2: 审阅 MD 文档 ─────────────────────────────────────

function Step2Review() {
  const { generatedMd, setGeneratedMd, generateCode, setStep, isGenerating, error } =
    useAgentEditorStore()

  return (
    <div className="max-w-3xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">
        Step 2: 审阅 & 编辑 MD 规范文档
      </h3>
      <p className="text-sm text-maia-text-secondary mb-4">
        以下是 AI 生成的 Agent 规范文档，你可以直接编辑修改。
      </p>

      <textarea
        value={generatedMd}
        onChange={(e) => setGeneratedMd(e.target.value)}
        rows={20}
        className="w-full rounded-lg border border-maia-border bg-maia-bg/50 px-4 py-3 text-[12px] font-mono tracking-tight outline-none resize-y focus:border-maia-accent/40"
        spellCheck={false}
      />

      {error && (
        <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger">
          <XCircle className="h-3 w-3" />
          {error}
        </div>
      )}

      <div className="flex justify-between mt-4">
        <Button variant="outline" onClick={() => setStep(1)}>
          <ArrowLeft className="h-3.5 w-3.5" />
          返回修改需求
        </Button>
        <Button onClick={generateCode} disabled={!generatedMd.trim() || isGenerating}>
          {isGenerating ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              生成中...
            </>
          ) : (
            <>
              生成 Agent
              <ArrowRight className="h-3.5 w-3.5" />
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

// ── Step 3: 代码核验 ─────────────────────────────────────────

function Step3Verify() {
  const { generatedCode, sandboxResults, registerAgent, setStep, isGenerating, error } =
    useAgentEditorStore()
  const [editingCode, setEditingCode] = useState(false)
  const [code, setCode] = useState(generatedCode)

  const passed = (sandboxResults as Record<string, unknown>)?.passed as string[] || []
  const failed = (sandboxResults as Record<string, unknown>)?.failed as string[] || []

  return (
    <div className="max-w-4xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">
        Step 3: 代码预览 & 沙箱核验
      </h3>

      <div className="grid grid-cols-5 gap-4">
        {/* Code panel */}
        <div className="col-span-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5">
              <FileCode className="h-3.5 w-3.5 text-maia-accent" />
              <span className="text-xs font-medium text-maia-text-secondary tracking-wide">
                生成代码
              </span>
            </div>
            <button
              onClick={() => setEditingCode(!editingCode)}
              className="text-[11px] text-maia-accent hover:underline"
            >
              {editingCode ? '只读' : '编辑'}
            </button>
          </div>
          {editingCode ? (
            <textarea
              value={code}
              onChange={(e) => setCode(e.target.value)}
              rows={18}
              className="w-full rounded-lg border border-maia-border bg-maia-bg/50 px-3 py-2 text-[11px] font-mono outline-none resize-y"
              spellCheck={false}
            />
          ) : (
            <pre className="rounded-lg border border-maia-border bg-maia-bg/50 px-3 py-2 text-[11px] font-mono leading-relaxed overflow-auto max-h-[350px] whitespace-pre-wrap">
              {code}
            </pre>
          )}
        </div>

        {/* Sandbox panel */}
        <div className="col-span-2">
          <div className="flex items-center gap-1.5 mb-2">
            <Play className="h-3.5 w-3.5 text-maia-accent" />
            <span className="text-xs font-medium text-maia-text-secondary tracking-wide">
              沙箱测试
            </span>
          </div>
          <Card className="border-maia-border">
            <CardBody>
              <div className="space-y-1.5">
                {passed.map((msg, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-xs text-maia-success">
                    <CheckCheck className="h-3 w-3 shrink-0" />
                    {msg}
                  </div>
                ))}
                {failed.map((msg, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-xs text-maia-danger">
                    <XCircle className="h-3 w-3 shrink-0" />
                    {msg}
                  </div>
                ))}
                {passed.length === 0 && failed.length === 0 && (
                  <div className="text-xs text-maia-text-muted">等待测试...</div>
                )}
              </div>
            </CardBody>
          </Card>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger">
          <XCircle className="h-3 w-3" />
          {error}
        </div>
      )}

      <div className="flex justify-between mt-4">
        <Button variant="outline" onClick={() => setStep(2)}>
          <ArrowLeft className="h-3.5 w-3.5" />
          返回修改 MD
        </Button>
        <div className="flex gap-2">
          <Button variant="danger" onClick={() => setStep(1)}>
            <XCircle className="h-3.5 w-3.5" />
            拒绝
          </Button>
          <Button
            onClick={registerAgent}
            disabled={isGenerating || failed.length > 0}
          >
            {isGenerating ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                注册中...
              </>
            ) : (
              <>
                <CheckCircle2 className="h-3.5 w-3.5" />
                批准并注册上线
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Step 4: 完成 ─────────────────────────────────────────────

function Step4Done() {
  const { registeredId, reset } = useAgentEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)

  return (
    <div className="max-w-md mx-auto text-center py-12">
      <div className="flex justify-center mb-4">
        <div className="flex items-center justify-center h-16 w-16 rounded-full bg-maia-success/10">
          <Rocket className="h-8 w-8 text-maia-success" />
        </div>
      </div>

      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">
        Agent 已就绪！
      </h3>

      <Card className="border-maia-border mt-4">
        <CardBody>
          <div className="space-y-2 text-left">
            <div className="flex justify-between text-xs">
              <span className="text-maia-text-muted">Agent ID</span>
              <span className="font-mono text-maia-text">{registeredId}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-maia-text-muted">状态</span>
              <Badge variant="success">已注册</Badge>
            </div>
          </div>
        </CardBody>
      </Card>

      <div className="flex gap-3 justify-center mt-6">
        <Button
          variant="outline"
          onClick={() => {
            reset()
            setActiveView('chat')
          }}
        >
          返回对话
        </Button>
        <Button
          onClick={() => {
            reset()
          }}
        >
          <Bot className="h-3.5 w-3.5" />
          创建新 Agent
        </Button>
      </div>
    </div>
  )
}

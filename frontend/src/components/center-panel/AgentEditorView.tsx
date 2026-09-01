import { useState, useRef, useEffect, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Bot, ArrowRight, ArrowLeft, CheckCircle2, XCircle,
  Loader2, FileCode, Play, CheckCheck, Rocket, Save,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useUIStore } from '@/stores/ui-store'
import { useResourceStore } from '@/stores/resource-store'
import { useAgentEditorStore } from '@/stores/agent-editor-store'
import { useTabIndent } from '@/hooks/use-tab-indent'
import { useSaveShortcut } from '@/hooks/use-save-shortcut'

/** Agent 保存状态指示器 + 保存按钮（手动保存，支持 Ctrl/Cmd+S） */
function AgentSaveIndicator() {
  const { saveState, saveError, flushSave } = useAgentEditorStore()

  const cfg = {
    idle:   { text: '无改动',   cls: 'text-maia-text-muted', spin: false, canSave: false },
    dirty:  { text: '未保存',   cls: 'text-amber-500',       spin: false, canSave: true  },
    saving: { text: '保存中',   cls: 'text-maia-accent',     spin: true,  canSave: false },
    saved:  { text: '已保存',   cls: 'text-maia-success',    spin: false, canSave: false },
    error:  { text: '保存失败', cls: 'text-maia-danger',     spin: false, canSave: true  },
  }[saveState]

  return (
    <span className="flex items-center gap-2">
      <span className="flex items-center gap-1.5" title={saveError || ''}>
        {cfg.spin && <Loader2 className="h-3 w-3 animate-spin" />}
        <span className={`text-[11px] ${cfg.cls}`}>{cfg.text}</span>
      </span>
      <button
        onClick={() => void flushSave()}
        disabled={!cfg.canSave}
        title="保存 (Ctrl/Cmd+S)"
        className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border border-maia-border text-maia-accent hover:bg-maia-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <Save className="h-3 w-3" />
        保存
      </button>
    </span>
  )
}

export function AgentEditorView() {
  const store = useAgentEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)
  const selectedResource = useResourceStore((s) => s.selectedResource)

  // Ctrl/Cmd+S 保存（仅编辑已注册 Agent 时有效）
  useSaveShortcut(
    () => { if (store.editingAgentId) void store.flushSave() },
    Boolean(store.editingAgentId)
  )

  const handleClose = () => {
    const isEditingExisting = Boolean(store.editingAgentId)
    const backTo = isEditingExisting && selectedResource?.type === 'agent' ? 'agent-detail' : 'chat'
    if (isEditingExisting && store.hasUnsavedChanges()) {
      const ok = window.confirm(
        '当前 Agent 有未保存的改动。\n\n' +
        '点击「确定」：保存后关闭\n' +
        '点击「取消」：留在编辑器（不保存）'
      )
      if (!ok) return
      void store.flushSave()
    }
    setActiveView(backTo)
    store.reset()
  }

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-maia-accent" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">
            Agent 编辑器
          </span>
          {store.editingAgentId && (
            <span className="text-[11px] text-amber-500 tracking-wide">
              编辑中：{store.editingAgentName || store.editingAgentId}
            </span>
          )}
          {store.editingAgentId && <AgentSaveIndicator />}
        </div>
        <button
          onClick={handleClose}
          title={store.editingAgentId ? '返回该 Agent 详情' : '返回对话'}
          className="text-maia-text-muted hover:text-maia-text text-sm"
        >
          × 关闭
        </button>
      </div>

      {/* Step indicators */}
      <div className="flex items-center gap-0 px-4 py-2 border-b border-maia-border bg-maia-surface shrink-0">
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
  // 注意：列表为空 ≠ 正在加载。用显式状态区分「加载中 / 就绪 / 失败」，
  // 否则后端未启动时 fetch 静默失败，下拉框会永远显示"加载中"误导使用者。
  const [apiItems, setApiItems] = useState<AcItem[]>([])
  const [toolItems, setToolItems] = useState<AcItem[]>([])
  const [apiState, setApiState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [toolState, setToolState] = useState<'loading' | 'ready' | 'failed'>('loading')
  // 失败细分：network（连不上/超时）与 http（后端在跑但接口异常，如编码 500）。
  // 二者的处置完全不同——前者启动后端，后者查后端日志——不能混成一句话。
  const [apiFailReason, setApiFailReason] = useState('network')
  const [toolFailReason, setToolFailReason] = useState('network')

  // Dropdown state
  const [show, setShow] = useState(false)
  const [filtered, setFiltered] = useState<AcItem[]>([])
  const [selIdx, setSelIdx] = useState(0)
  const [ddPos, setDdPos] = useState({ top: 0, left: 0 })
  const [trigger, setTrigger] = useState<'@' | '$'>('@')
  const [tRange, setTRange] = useState({ start: 0, end: 0 })

  // ═══ Fetch data on mount ═══
  useEffect(() => {
    const BASE = ''
    const TIMEOUT_MS = 10000
    // 统一的列表拉取：带超时与非 2xx 判定，失败走 onFail 而不是只 console.warn。
    // 后端未启动（代理 ECONNREFUSED）、返回 404/HTML、挂起超时，统一归为 failed。
    const fetchList = (
      url: string,
      onOk: (d: Record<string, unknown>) => void,
      onFail: (reason: string) => void
    ) => {
      const ctrl = new AbortController()
      const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
      fetch(url, { signal: ctrl.signal })
        .then((r) => {
          if (!r.ok) throw { kind: 'http', status: r.status }
          return r.json()
        })
        .then((d) => {
          clearTimeout(timer)
          onOk(d as Record<string, unknown>)
        })
        .catch((e) => {
          clearTimeout(timer)
          onFail(e?.kind === 'http' ? `http:${e.status}` : 'network')
        })
    }

    fetchList(
      `${BASE}/api/apis/list`,
      (d) => {
        const items = ((d.apis || []) as Array<Record<string, unknown>>).map(
          (a: Record<string, unknown>) => ({
            name: (a.name as string) || (a.id as string) || '',
            id: (a.id as string) || '',
          })
        )
        setApiItems(items)
        setApiState('ready')
      },
      (reason) => {
        setApiFailReason(reason)
        setApiState('failed')
      }
    )

    fetchList(
      `${BASE}/api/tool/list`,
      (d) => {
        const items = (
          ((d as Record<string, unknown>).tools as Array<Record<string, unknown>>) || []
        ).map((t: Record<string, unknown>) => ({
          name: (t.name as string) || (t.id as string) || '',
          id: (t.id as string) || '',
        }))
        setToolItems(items)
        setToolState('ready')
      },
      (reason) => {
        setToolFailReason(reason)
        setToolState('failed')
      }
    )
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

  // 当前触发符对应的列表与状态
  const acSrc = trigger === '@' ? apiItems : toolItems
  const acState = trigger === '@' ? apiState : toolState
  const acFailReason = trigger === '@' ? apiFailReason : toolFailReason
  // 下拉框可见性：加载中 / 失败 / 就绪但列表为空 时显示提示行；
  // 仅"有数据但无匹配项"时静默收起（保持原行为）
  const showHint = acState !== 'ready' || acSrc.length === 0

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
        className="w-full rounded-lg border border-maia-border bg-maia-surface px-4 py-3 text-[13px] tracking-wide outline-none resize-none focus:border-maia-accent/40 placeholder:text-maia-text-muted"
      />

      {/* Dropdown via Portal to avoid parent clipping */}
      {show &&
        (showHint || filtered.length > 0) &&
        createPortal(
          <div
            className="fixed z-[9999] w-72 max-h-48 overflow-y-auto rounded-lg border border-maia-border bg-maia-surface shadow-lg py-1"
            style={{ top: ddPos.top, left: ddPos.left }}
          >
            {acState === 'failed' ? (
              // 两种失败给不同指引：接口异常时让用户查后端日志（后端明明在跑，
              // 说"请先启动后端"反而误导——正是条目 27 误诊案例的教训）
              acFailReason.startsWith('http:') ? (
                <div className="px-3 py-2 text-[12px] text-maia-danger leading-relaxed">
                  后端接口异常（HTTP {acFailReason.slice(5)}）。后端在运行但该接口出错，
                  常见原因（编码/权限等）见后端控制台日志。
                </div>
              ) : (
                <div className="px-3 py-2 text-[12px] text-maia-danger leading-relaxed">
                  无法连接后端服务（http://localhost:8001）
                  <br />
                  <span className="text-[10px] text-maia-text-muted">
                    请先启动后端：uvicorn app.main:app --port 8001
                  </span>
                </div>
              )
            ) : acState === 'loading' ? (
              <div className="px-3 py-2 text-[12px] text-maia-text-muted">加载中...</div>
            ) : acSrc.length === 0 ? (
              <div className="px-3 py-2 text-[12px] text-maia-text-muted">
                {trigger === '@' ? '暂无可用系统 API' : '暂无可用工具'}
              </div>
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
  const { generatedMd, setGeneratedMd, generateCode, setStep, isGenerating, error,
          editingAgentId, editingAgentName } =
    useAgentEditorStore()
  const isEditMode = Boolean(editingAgentId)
  // Markdown 用 2 空格缩进
  const onTabIndent = useTabIndent('  ')
  const notifyEdit = useAgentEditorStore((s) => s.notifyEdit)
  const handleMdChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setGeneratedMd(e.target.value)
    notifyEdit()
  }

  return (
    <div className="max-w-3xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">
        Step 2: 审阅 &amp; 编辑 MD 规范文档
      </h3>
      <p className="text-sm text-maia-text-secondary mb-4">
        {isEditMode
          ? <>正在编辑已有 Agent <span className="text-amber-500">{editingAgentName || editingAgentId}</span>，以下是其现有规范文档，可直接修改后重新生成代码。</>
          : '以下是 AI 生成的 Agent 规范文档，你可以直接编辑修改。'}
      </p>

      <textarea
        value={generatedMd}
        onChange={handleMdChange}
        onKeyDown={onTabIndent}
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
  const { generatedCode, sandboxResults, registerAgent, setStep, isGenerating, error,
          setGeneratedCode, editingAgentId, editingAgentName, baselineCode,
          syncSpecFromCode, saveCode } =
    useAgentEditorStore()
  const [editingCode, setEditingCode] = useState(false)

  // 直接读写 store 中的 generatedCode，确保手工微调后的内容
  // 能被 registerAgent 正确提交（此前用局部 state 会导致改动被丢弃）。
  const code = generatedCode

  const isEditMode = Boolean(editingAgentId)
  const codeDirty = code.trim() !== baselineCode.trim()
  // Python 代码用 4 空格缩进
  const onCodeTabIndent = useTabIndent('    ')
  const notifyEdit = useAgentEditorStore((s) => s.notifyEdit)
  // 代码变更后标记「未保存」
  const handleCodeEdit = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setGeneratedCode(e.target.value)
    notifyEdit()
  }

  const passed = (sandboxResults as Record<string, unknown>)?.passed as string[] || []
  const failed = (sandboxResults as Record<string, unknown>)?.failed as string[] || []

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-lg font-semibold text-maia-text-heading tracking-wide">
          Step 3: 代码预览 &amp; 沙箱核验
        </h3>
        {isEditMode && (
          <span className="text-[11px] text-amber-500 tracking-wide">
            正在编辑已有 Agent：{editingAgentName || editingAgentId}
          </span>
        )}
      </div>

      <div className="grid grid-cols-5 gap-4">
        {/* Code panel */}
        <div className="col-span-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5">
              <FileCode className="h-3.5 w-3.5 text-maia-accent" />
              <span className="text-xs font-medium text-maia-text-secondary tracking-wide">
                生成代码
              </span>
              {codeDirty && <span className="text-[10px] text-amber-500 tracking-wide">已手工修改</span>}
            </div>
            <div className="flex items-center gap-2">
              {isEditMode && (
                <>
                  <button
                    onClick={() => syncSpecFromCode()}
                    disabled={!codeDirty || isGenerating}
                    title={codeDirty ? '让 AI 依据代码改动更新 MD 文档' : '代码未修改，无需同步'}
                    className="text-[11px] text-purple-500 hover:underline disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    同步代码改动到文档
                  </button>
                  <button
                    onClick={() => saveCode()}
                    disabled={!codeDirty || isGenerating}
                    title="保存代码到该 Agent"
                    className="text-[11px] text-maia-accent hover:underline disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    保存代码
                  </button>
                </>
              )}
              <button
                onClick={() => setEditingCode(!editingCode)}
                className="text-[11px] text-maia-accent hover:underline"
              >
                {editingCode ? '只读' : '编辑'}
              </button>
            </div>
          </div>
          {editingCode ? (
            <textarea
              value={code}
              onChange={handleCodeEdit}
              onKeyDown={onCodeTabIndent}
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

import { useState, useEffect, useRef, useCallback, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Wrench, ArrowRight, ArrowLeft, CheckCircle2, XCircle,
  Loader2, FileCode, Play, Rocket, Tag, Plus, X, Save,
  FileText, ChevronDown, ChevronRight, Sparkles,
} from 'lucide-react'
import { Highlight, themes } from 'prism-react-renderer'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'

/** 占位 toolId：新建工具尚未注册时使用。后端据此跳过注册项校验，
 *  概览与精化照常可用，但不能落盘（无注册项可写）。 */
const DRAFT_TOOL_ID = '_draft_'
import { useUIStore } from '@/stores/ui-store'
import { useResourceStore } from '@/stores/resource-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { useTabIndent } from '@/hooks/use-tab-indent'
import { useSaveShortcut } from '@/hooks/use-save-shortcut'
import { SpecOutlineView } from './SpecOutlineView'
import { TableEditView } from './TableEditView'
import type { SpecOutline, SpecTable } from '@/types/spec-outline'

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

/** 保存状态指示器 + 保存按钮（手动保存，支持 Ctrl/Cmd+S） */
function SaveIndicator() {
  const { saveState, saveError, flushSave } = useToolEditorStore()

  // idle/dirty/saving/saved/error → 文案与配色
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

export function ToolEditorView() {
  const store = useToolEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)
  const selectedResource = useResourceStore((s) => s.selectedResource)

  // 关闭时应回到"来处"：编辑已有工具 → 回该工具详情页；新建 → 回对话。
  // 直接跳到对话会丢失上下文，尤其是从详情页点「编辑」进来时。
  // Ctrl/Cmd+S 保存（仅编辑已注册工具时有效：新建工具无处可存）
  useSaveShortcut(
    () => { if (store.editingToolId) void store.flushSave() },
    Boolean(store.editingToolId)
  )

  const handleClose = () => {
    const isEditingExisting = Boolean(store.editingToolId)
    const backTo = isEditingExisting && selectedResource?.type === 'tool' ? 'tool-detail' : 'chat'
    // 有未保存改动时先确认，避免静默丢失
    if (isEditingExisting && store.hasUnsavedChanges()) {
      const ok = window.confirm(
        '当前工具有未保存的改动。\n\n' +
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
          <Wrench className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">工具编辑器</span>
          {store.editingToolId && (
            <span className="text-[11px] text-amber-500 tracking-wide">
              编辑中：{store.editingToolName || store.editingToolId}
            </span>
          )}
          {store.editingToolId && <SaveIndicator />}
        </div>
        <button
          onClick={handleClose}
          title={store.editingToolId ? '返回该工具详情' : '返回对话'}
          className="text-maia-text-muted hover:text-maia-text text-sm"
        >
          × 关闭
        </button>
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
  // 参考代码是 Python，用 4 空格缩进
  const onRefTabIndent = useTabIndent('    ')

  // Autocomplete data
  // 注意：列表为空 ≠ 正在加载。必须用显式状态区分「加载中 / 就绪 / 失败」，
  // 否则后端未启动时 fetch 静默失败，下拉框会永远显示"加载中"误导使用者
  // （曾经的问题是 isLoading 直接用 apiItems.length === 0 判断）。
  const [apiItems, setApiItems] = useState<Array<{name:string,id:string}>>([])
  const [toolItems, setToolItems] = useState<Array<{name:string,id:string}>>([])
  const [apiState, setApiState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [toolState, setToolState] = useState<'loading' | 'ready' | 'failed'>('loading')

  // Dropdown state
  const [show, setShow] = useState(false)
  const [filtered, setFiltered] = useState<Array<{name:string,id:string}>>([])
  const [selIdx, setSelIdx] = useState(0)
  const [ddPos, setDdPos] = useState({ top: 0, left: 0 })
  const [trigger, setTrigger] = useState<'@' | '$'>('@')
  const [tRange, setTRange] = useState({ start: 0, end: 0 })

  useEffect(() => {
    const TIMEOUT_MS = 10000
    // 统一的列表拉取：带超时与非 2xx 判定，失败走 onFail 而不是静默吞掉。
    // 后端未启动（代理 ECONNREFUSED）、返回 404/HTML、挂起超时，统一归为 failed。
    const fetchList = (url: string,
                       onOk: (d: Record<string, unknown>) => void,
                       onFail: () => void) => {
      const ctrl = new AbortController()
      const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
      fetch(url, { signal: ctrl.signal })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
        .then(d => { clearTimeout(timer); onOk(d as Record<string, unknown>) })
        .catch(() => { clearTimeout(timer); onFail() })
    }

    fetchList(`${BASE}/api/apis/list`, (d) => {
      const items = ((d.apis||[]) as Array<Record<string,unknown>>).map((a:Record<string,unknown>) => ({name:(a.name as string)||(a.id as string)||'', id:(a.id as string)||''}))
      setApiItems(items)
      setApiState('ready')
    }, () => setApiState('failed'))

    fetchList(`${BASE}/api/tool/list`, (d) => {
      const items = (((d as Record<string,unknown>).tools||[]) as Array<Record<string,unknown>>).map((t:Record<string,unknown>) => ({name:(t.name as string)||(t.id as string)||'', id:(t.id as string)||''}))
      setToolItems(items)
      setToolState('ready')
    }, () => setToolState('failed'))
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

  // 当前触发符对应的列表与状态
  const acSrc = trigger === '@' ? apiItems : toolItems
  const acState = trigger === '@' ? apiState : toolState
  // 下拉框可见性：加载中 / 失败 / 就绪但列表为空 时显示提示行；
  // 仅"有数据但无匹配项"时静默收起（保持原行为）
  const showHint = acState !== 'ready' || acSrc.length === 0

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
        <textarea value={referenceCode} onChange={e => setReferenceCode(e.target.value)} onKeyDown={onRefTabIndent}
          placeholder="粘贴参考代码..."
          rows={8} className="w-full rounded-lg border border-maia-border bg-maia-bg px-4 py-3 text-[12px] font-mono tracking-wide outline-none resize-none focus:border-maia-accent/40 placeholder:text-maia-text-muted" />
      </div>

      {show && (showHint || filtered.length > 0) && createPortal(
        <div className="fixed z-[9999] w-72 max-h-48 overflow-y-auto rounded-lg border border-maia-border bg-maia-surface shadow-lg py-1" style={{ top: ddPos.top, left: ddPos.left }}>
          {acState === 'failed' ? (
            // 后端连不上是最高频的失败原因（下载仓库后只起了前端），如实告知而不是永远"加载中"
            <div className="px-3 py-2 text-[12px] text-maia-danger leading-relaxed">
              无法连接后端服务（http://localhost:8001）<br />
              <span className="text-[10px] text-maia-text-muted">请先启动后端：uvicorn app.main:app --port 8001</span>
            </div>
          ) : acState === 'loading' ? (
            <div className="px-3 py-2 text-[12px] text-maia-text-muted">加载中...</div>
          ) : acSrc.length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-maia-text-muted">
              {trigger === '@' ? '暂无可用系统 API' : '暂无可用工具'}
            </div>
          ) : filtered.map((item, i) => (
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

/**
 * 概览层容器：懒加载节点树。
 *
 * 默认折叠，因为解析（尤其带摘要时）需要请求后端；
 * 使用者需要看要点时再展开，避免每次进入 Step2 都产生开销。
 */
function OutlineSection({
  toolId, md, onApply,
}: {
  toolId: string
  md: string
  onApply: (updatedMd: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [outline, setOutline] = useState<SpecOutline | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [summarizing, setSummarizing] = useState(false)
  const [summaryLoaded, setSummaryLoaded] = useState(false)
  const [table, setTable] = useState<SpecTable | null>(null)
  // 记录缓存对应的文档内容：文档被修改后旧节点树即失效，需重新解析
  const [cachedFor, setCachedFor] = useState('')

  // 文档变化时丢弃缓存，避免展示与当前文档不符的节点树
  useEffect(() => {
    if (outline && cachedFor !== md) {
      setOutline(null)
      setOpen(false)
      // 文档变了，已生成的摘要与表格数据均失效
      setSummaryLoaded(false)
      setTable(null)
    }
  }, [md, outline, cachedFor])

  // 默认只加载结构（约 1ms，秒开）。
  // 人话摘要需调用推理模型，实测 30 秒左右 —— 不能让它阻塞"查看概览"，
  // 因此改为使用者主动点击「生成通俗解读」时才请求。
  const load = async () => {
    if (outline && cachedFor === md) { setOpen(!open); return }
    setLoading(true); setError('')
    try {
      const { toolApi } = await import('@/services/api/tool')
      // 结构与表格都是纯解析（毫秒级），一起加载不影响秒开体验
      const [res, tbl] = await Promise.all([
        toolApi.getSpecOutline(toolId, md, false),
        toolApi.getSpecTable(toolId, md).catch(() => ({ has_table: false })),
      ])
      setOutline(res)
      setTable(tbl)
      setCachedFor(md)
      setOpen(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // 生成人话摘要。
  // generate-spec 已在后台预生成并缓存，因此这里通常命中缓存、秒回；
  // 只有缓存未就绪（如手动改了文档）才需要等待模型。
  const genSummaries = async () => {
    if (summaryLoaded || summarizing) return
    setSummarizing(true); setError('')
    try {
      const { toolApi } = await import('@/services/api/tool')
      const res = await toolApi.getSpecOutline(toolId, md, true)
      setOutline(res)
      setCachedFor(md)
      setSummaryLoaded(true)
      if (res.warning) setError(res.warning)
    } catch (e) {
      setError(String(e))
    } finally {
      setSummarizing(false)
    }
  }

  // 展开时若摘要已缓存（后台预生成完成），直接带入，无需用户再点一次
  useEffect(() => {
    if (!open || !outline || summaryLoaded || summarizing) return
    if (outline.cached) return   // 首次加载已带摘要
    void (async () => {
      try {
        const { toolApi } = await import('@/services/api/tool')
        const res = await toolApi.getSpecOutline(toolId, md, true)
        // 命中缓存才自动应用；否则等用户主动点按钮（避免无谓等待）
        if (res.cached) {
          setOutline(res); setCachedFor(md); setSummaryLoaded(true)
        }
      } catch { /* 忽略：用户可手动触发 */ }
    })()
  }, [open, outline, summaryLoaded, summarizing, toolId, md])

  return (
    <div className="mb-3 rounded-lg border border-maia-border">
      <button
        onClick={load}
        disabled={loading}
        className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-maia-bg/40"
      >
        <span className="flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-maia-accent" />
          <span className="text-xs text-maia-text-secondary">
            文档要点概览
          </span>
          <span className="text-[10px] text-maia-text-muted">
            （看不懂技术文档时先看这里）
          </span>
        </span>
        <span className="flex items-center gap-1.5">
          {loading && <Loader2 className="h-3 w-3 animate-spin text-maia-text-muted" />}
          {open
            ? <ChevronDown className="h-3 w-3 text-maia-text-muted" />
            : <ChevronRight className="h-3 w-3 text-maia-text-muted" />}
        </span>
      </button>

      {open && outline && (
        <div className="px-3 pb-3">
          {outline.warning && (
            <div className="text-[10px] text-amber-500 mb-2">{outline.warning}</div>
          )}

          {/* 表格表单化编辑：改参数值这类确定性改动直接改，0 延迟、不调 LLM */}
          {table?.has_table && (
            <TableEditView
              toolId={toolId}
              table={table}
              md={md}
              onApplied={(updatedMd) => { onApply(updatedMd); setOutline(null); setTable(null); setOpen(false) }}
            />
          )}

          {/* 人话摘要：慢操作，按需触发并显示等待预期，避免阻塞概览展开 */}
          <div className="mb-2 flex items-center gap-2">
            {summaryLoaded ? (
              <span className="text-[10px] text-maia-success">
                已生成通俗解读
              </span>
            ) : (
              <>
                <button
                  onClick={genSummaries}
                  disabled={summarizing}
                  className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border border-maia-border text-maia-accent hover:bg-maia-accent/10 disabled:opacity-60"
                >
                  {summarizing
                    ? <Loader2 className="h-2.5 w-2.5 animate-spin" />
                    : <Sparkles className="h-2.5 w-2.5" />}
                  {summarizing ? '正在生成…' : '生成通俗解读'}
                </button>
                <span className="text-[10px] text-maia-text-muted">
                  {summarizing
                    ? '需调用模型逐段概括，约需 30 秒'
                    : '把每段技术描述翻译成大白话（文档未改动时会自动缓存）'}
                </span>
              </>
            )}
          </div>

          <SpecOutlineView
            outline={outline}
            toolId={toolId}
            onApply={(updatedMd) => { onApply(updatedMd); setOutline(null); setOpen(false) }}
          />
        </div>
      )}
      {error && (
        <div className="px-3 pb-2 text-[10px] text-maia-danger">{error}</div>
      )}
    </div>
  )
}

function Step2() {
  const { generatedMd, setGeneratedMd, tags, addTag, removeTag, generateCode, setStep, isGenerating, error,
          editingToolId, editingToolName } = useToolEditorStore()
  const [editing, setEditing] = useState(false)
  const [newTag, setNewTag] = useState('')
  const isEditMode = Boolean(editingToolId)
  // Markdown 用 2 空格缩进
  const onTabIndent = useTabIndent('  ')
  const notifyEdit = useToolEditorStore((s) => s.notifyEdit)
  // 内容变更后标记「未保存」（仅对已注册工具生效）
  const handleMdChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setGeneratedMd(e.target.value)
    notifyEdit()
  }

  // 应用节点精化结果：写入编辑框并标记为「未保存」
  const handleMdApply = (updatedMd: string) => {
    setGeneratedMd(updatedMd)
    notifyEdit()
  }

  const handleAddTag = () => {
    const t = newTag.trim()
    if (t) { addTag(t); setNewTag(''); setEditing(false) }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 2: 审阅 &amp; 编辑 MD 规范文档</h3>
      <p className="text-sm text-maia-text-secondary mb-3">
        {isEditMode
          ? <>正在编辑已有工具 <span className="text-amber-500">{editingToolName || editingToolId}</span>，以下是其现有规范文档，可直接修改后重新生成代码。</>
          : '以下是 AI 生成的工具规范文档，你可以直接编辑修改。'}
      </p>

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

      {/* 概览层：人话要点 + 节点级精化。
          放在技术文档上方，让使用者先建立整体印象，再决定是否深入细节。
          只要有文档内容就显示——新建工具（尚未注册）同样需要看懂文档，
          此时用占位 id，精化照常可用，只是不能落盘。 */}
      {generatedMd.trim() && (
        <OutlineSection
          toolId={editingToolId || DRAFT_TOOL_ID}
          md={generatedMd}
          onApply={handleMdApply}
        />
      )}

      <textarea value={generatedMd} onChange={handleMdChange} onKeyDown={onTabIndent} rows={18}
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
          setStep, isGenerating, isTesting, isAutoDebugging, error, debugRounds, debugStream, setTestInput,
          setGeneratedCode, editingToolId, editingToolName, baselineCode, syncSpecFromCode } = useToolEditorStore()
  const logEndRef = useRef<HTMLDivElement>(null)
  const fileInputRefs = useRef<Map<string, HTMLInputElement>>(new Map())
  const [uploadedFiles, setUploadedFiles] = useState<Map<string, File>>(new Map())
  // 面板高度（px），初始值
  const [codeHeight, setCodeHeight] = useState(300)
  const [logHeight, setLogHeight] = useState(180)
  const containerRef = useRef<HTMLDivElement>(null)
  // 代码手工微调开关
  const [isEditingCode, setIsEditingCode] = useState(false)

  const isEditMode = Boolean(editingToolId)
  const codeDirty = generatedCode.trim() !== baselineCode.trim()
  // Python 代码用 4 空格缩进
  const onCodeTabIndent = useTabIndent('    ')
  const notifyEdit = useToolEditorStore((s) => s.notifyEdit)
  // 代码变更后触发防抖自动保存
  const handleCodeEdit = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setGeneratedCode(e.target.value)
    notifyEdit()
  }

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
      <div className="flex items-center justify-between mb-2 shrink-0">
        <h3 className="text-lg font-semibold text-maia-text-heading tracking-wide">Step 3: 代码预览 &amp; 沙箱测试</h3>
        {isEditMode && (
          <span className="text-[11px] text-amber-500 tracking-wide">
            正在编辑已有工具：{editingToolName || editingToolId}
          </span>
        )}
      </div>

      {/* 上半部分：代码 + 测试区（水平分割） */}
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="grid grid-cols-5 gap-4 flex-1 min-h-0">
          {/* 左：代码预览 / 手工微调 */}
          <div className="col-span-3 flex flex-col min-h-0">
            <div className="flex items-center justify-between mb-2 shrink-0">
              <div className="flex items-center gap-1.5">
                <FileCode className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-xs font-medium text-maia-text-secondary tracking-wide">生成代码</span>
                {codeDirty && <span className="text-[10px] text-amber-500 tracking-wide">已手工修改</span>}
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => setIsEditingCode(!isEditingCode)}
                  className="flex items-center gap-1 text-[10px] text-maia-text-muted hover:text-maia-accent px-1.5 py-0.5 rounded border border-maia-border"
                >
                  {isEditingCode ? '预览' : '手工微调'}
                </button>
                {isEditMode && (
                  <button
                    onClick={() => syncSpecFromCode()}
                    disabled={!codeDirty || isGenerating}
                    title={codeDirty ? '让 AI 依据代码改动更新 MD 文档' : '代码未修改，无需同步'}
                    className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-maia-border text-purple-500 hover:bg-purple-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {isGenerating ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : null}
                    同步代码改动到文档
                  </button>
                )}
              </div>
            </div>
            {isEditingCode ? (
              <textarea
                value={generatedCode}
                onChange={handleCodeEdit}
                onKeyDown={onCodeTabIndent}
                spellCheck={false}
                className="flex-1 min-h-0 rounded-lg border border-maia-border bg-[#1e1e1e] text-gray-100 px-3 py-2 text-[11px] font-mono leading-relaxed outline-none resize-none focus:border-maia-accent/40"
                style={{ maxHeight: codeHeight }}
              />
            ) : (
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
            )}
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
  const selectedResource = useResourceStore((s) => s.selectedResource)
  // 注册完成后回到该工具详情页，让使用者立刻看到发布结果。
  // 仅当左侧选中的正是刚注册的工具时才跳转，否则会跳到别的工具详情页。
  const handleBack = () => {
    const cur = selectedResource as { type?: string; id?: string } | null
    const backTo = cur?.type === 'tool' && cur.id === registeredId ? 'tool-detail' : 'chat'
    setActiveView(backTo)
    reset()
  }
  return (
    <div className="max-w-md mx-auto text-center py-12">
      <div className="flex justify-center mb-4"><div className="flex items-center justify-center h-16 w-16 rounded-full bg-maia-success/10"><Rocket className="h-8 w-8 text-maia-success" /></div></div>
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">工具已发布！</h3>
      <Card className="border-maia-border mt-4"><CardBody><div className="space-y-2 text-left"><div className="flex justify-between text-xs"><span className="text-maia-text-muted">Tool ID</span><span className="font-mono text-maia-text">{registeredId}</span></div><div className="flex justify-between text-xs"><span className="text-maia-text-muted">状态</span><Badge variant="success">已注册</Badge></div></div></CardBody></Card>
      <div className="flex gap-3 justify-center mt-6">
        <Button variant="outline" onClick={handleBack}>
          {selectedResource?.type === 'tool' && (selectedResource as { id?: string }).id === registeredId
            ? '返回工具详情'
            : '返回对话'}
        </Button>
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

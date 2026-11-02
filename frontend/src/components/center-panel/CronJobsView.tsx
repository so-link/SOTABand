/** 定时任务视图 — 按 cron 计划周期执行工具 / Agent。
 *
 * 交互原则：凡能以选择完成的绝不要求手工输入——
 * - 调度频率用预设（每天/每周/工作日/每小时/每N分钟）+ 时刻下拉生成 cron，
 *   仅「自定义」高级模式下才暴露表达式文本框；
 * - 任务目标来自 /api/cron/options 的下拉列表；
 * - 执行参数按目标 spec 文档的输入表自动渲染成表单（枚举→单选、
 *   布尔→下拉、数值→数字框），默认值预填；
 * - 超时 / 错过策略 / 时区均为固定选项。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CalendarClock, Plus, Play, Pencil, Trash2, RefreshCw,
  ChevronDown, ChevronRight, Loader2, Wrench, Bot,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { cronApi, type CronJob, type CronOptions, type CronRun, type CronJobPayload } from '@/services/api/cron'

// ── spec 输入表解析（与 ToolDetailView.parseInputs 同格式） ──
interface SpecField { name: string; type: string; required: boolean; default: string | null; desc: string }

function parseToolInputs(md: string): SpecField[] {
  const fields: SpecField[] = []
  let inTable = false
  for (const line of md.split('\n')) {
    if (line.includes('输入规范')) { inTable = true; continue }
    if (inTable && line.startsWith('##')) break
    if (inTable && line.startsWith('|') && !line.includes('参数名') && !line.includes('---')) {
      const parts = line.split('|').slice(1, -1).map((s) => s.trim())
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

/** Agent spec 的「## 输入 / ## 参数」段 "- name: ..." 列表 */
function parseAgentInputs(md: string): SpecField[] {
  const fields: SpecField[] = []
  let inSec = false
  for (const line of md.split('\n')) {
    if (/^##\s*(输入|参数)/.test(line.trim())) { inSec = true; continue }
    if (inSec && line.startsWith('##')) break
    if (inSec) {
      const m = line.trim().match(/^[-*]\s*([A-Za-z_][\w-]*)\s*[:：]?\s*(.*)$/)
      if (m) fields.push({ name: m[1], type: 'string', required: false, default: null, desc: m[2] || '' })
    }
  }
  return fields
}

/** 从说明里提取枚举候选（形如 "overwrite"=... / "append"=...） */
function extractEnum(desc: string): string[] {
  const hits = [...desc.matchAll(/"([^"]+)"\s*=/g)].map((m) => m[1])
  return hits.length >= 2 ? Array.from(new Set(hits)) : []
}

function stripQuotes(s: string): string {
  const t = s.trim()
  return t.startsWith('"') && t.endsWith('"') && t.length > 1 ? t.slice(1, -1) : t
}

// ── cron 预设 ──
type Freq = 'daily' | 'weekly' | 'weekday' | 'monthly' | 'hourly' | 'everyN' | 'custom'
const FREQ_OPTIONS: { value: Freq; label: string }[] = [
  { value: 'daily', label: '每天' },
  { value: 'weekday', label: '工作日（周一至周五）' },
  { value: 'weekly', label: '每周指定星期' },
  { value: 'monthly', label: '每月指定日期' },
  { value: 'hourly', label: '每小时整点' },
  { value: 'everyN', label: '每 N 分钟' },
  { value: 'custom', label: '自定义 cron（高级）' },
]
const DOW_OPTIONS = [
  { v: '1', label: '周一' }, { v: '2', label: '周二' }, { v: '3', label: '周三' },
  { v: '4', label: '周四' }, { v: '5', label: '周五' }, { v: '6', label: '周六' }, { v: '0', label: '周日' },
]
const MINUTE_OPTIONS = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
const TIMEOUT_OPTIONS = [120, 300, 600, 1800, 3600]
const SELECT_CLS = 'h-8 rounded-md border border-maia-border bg-maia-surface px-2 text-[13px] text-maia-text focus:outline-none focus:border-maia-accent'

function buildCron(freq: Freq, hour: number, minute: number, dow: string, dom: number, everyN: number, custom: string): string {
  switch (freq) {
    case 'daily': return `${minute} ${hour} * * *`
    case 'weekday': return `${minute} ${hour} * * 1-5`
    case 'weekly': return `${minute} ${hour} * * ${dow}`
    case 'monthly': return `${minute} ${hour} ${dom} * *`
    case 'hourly': return '0 * * * *'
    case 'everyN': return `*/${everyN} * * * *`
    case 'custom': return custom.trim()
  }
}

function describeCron(cron: string): string {
  const p = cron.split(/\s+/)
  if (p.length !== 5) return ''
  const [mi, h, dom, mon, dow] = p
  const hm = mi !== '*' && h !== '*' ? ` ${h}:${mi.padStart(2, '0')}` : ''
  if (/^\*\/\d+$/.test(mi) && h === '*' && dom === '*' && dow === '*') return `每 ${mi.slice(2)} 分钟执行`
  if (h === '*' && dom === '*' && dow === '*' && mi === '0') return '每小时整点执行'
  if (dom === '*' && mon === '*' && dow === '*') return `每天${hm}执行`
  if (dom === '*' && dow === '1-5') return `工作日${hm}执行`
  if (dom === '*' && /^\d$/.test(dow)) {
    const w = DOW_OPTIONS.find((d) => d.v === dow)?.label || `星期${dow}`
    return `每${w}${hm}执行`
  }
  if (/^\d+$/.test(dom) && dow === '*') return `每月 ${dom} 日${hm}执行`
  return cron
}

/** 把已保存的 cron 反解回预设表单；不能反解则落到 custom */
function parseCronToForm(cron: string) {
  const p = cron.trim().split(/\s+/)
  const base = { freq: 'custom' as Freq, hour: 9, minute: 0, dow: '1', dom: 1, everyN: 15, custom: cron }
  if (p.length !== 5) return base
  const [mi, h, dom, mon, dow] = p
  const num = (s: string) => (/^\d+$/.test(s) ? parseInt(s, 10) : null)
  if (/^\*\/\d+$/.test(mi) && h === '*' && dom === '*' && mon === '*' && dow === '*')
    return { ...base, freq: 'everyN' as Freq, everyN: parseInt(mi.slice(2), 10) }
  if (mi === '0' && h === '*' && dom === '*' && mon === '*' && dow === '*')
    return { ...base, freq: 'hourly' as Freq }
  const mm = num(mi); const hh = num(h)
  if (mm === null || hh === null || mon !== '*') return base
  if (dom === '*' && dow === '*') return { ...base, freq: 'daily', hour: hh, minute: mm, custom: '' }
  if (dom === '*' && dow === '1-5') return { ...base, freq: 'weekday', hour: hh, minute: mm, custom: '' }
  if (dom === '*' && num(dow) !== null) return { ...base, freq: 'weekly', hour: hh, minute: mm, dow, custom: '' }
  if (num(dom) !== null && dow === '*') return { ...base, freq: 'monthly', hour: hh, minute: mm, dom: num(dom)!, custom: '' }
  return base
}

function fmtTs(ts: number | null): string {
  return ts ? new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '-'
}

const RUN_BADGE: Record<string, string> = {
  success: 'bg-maia-success/15 text-maia-success',
  failed: 'bg-maia-danger/15 text-maia-danger',
  timeout: 'bg-maia-danger/15 text-maia-danger',
  running: 'bg-amber-500/15 text-amber-500',
  skipped: 'bg-maia-text-muted/15 text-maia-text-muted',
}
const RUN_LABEL: Record<string, string> = {
  success: '成功', failed: '失败', timeout: '超时', running: '执行中', skipped: '跳过',
}

export function CronJobsView() {
  const [jobs, setJobs] = useState<CronJob[]>([])
  const [options, setOptions] = useState<CronOptions | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<CronJob | 'new' | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [runs, setRuns] = useState<CronRun[]>([])
  const [busyId, setBusyId] = useState('')

  const reload = useCallback(async () => {
    try {
      setJobs(await cronApi.list())
      setError('')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }, [])

  useEffect(() => {
    reload()
    cronApi.options().then(setOptions).catch(() => setOptions({ tools: [], agents: [], timezones: ['Asia/Shanghai'] }))
  }, [reload])

  // 有执行中的 run 时轮询刷新历史与任务状态
  useEffect(() => {
    if (!expanded) return
    let stop = false
    const poll = async () => {
      try {
        const r = await cronApi.runs(expanded, 20)
        if (stop) return
        setRuns(r)
        if (!r.some((x) => x.status === 'running')) return
      } catch { /* 轮询失败静默 */ }
    }
    poll()
    const t = setInterval(poll, 3000)
    return () => { stop = true; clearInterval(t) }
  }, [expanded])

  const toggleExpand = async (id: string) => {
    if (expanded === id) { setExpanded(null); return }
    setExpanded(id)
    try { setRuns(await cronApi.runs(id, 20)) } catch { setRuns([]) }
  }

  const doTrigger = async (job: CronJob) => {
    setBusyId(job.id)
    try {
      await cronApi.trigger(job.id)
      if (expanded === job.id) setRuns(await cronApi.runs(job.id, 20))
      else await toggleExpand(job.id)
    } catch (e) { alert(`触发失败：${e instanceof Error ? e.message : e}`) }
    finally { setBusyId('') }
  }

  const doToggle = async (job: CronJob) => {
    try { await cronApi.toggle(job.id); reload() }
    catch (e) { alert(`操作失败：${e instanceof Error ? e.message : e}`) }
  }

  const doDelete = async (job: CronJob) => {
    if (!confirm(`确定删除定时任务「${job.name}」？执行历史将保留。`)) return
    try { await cronApi.remove(job.id); if (expanded === job.id) setExpanded(null); reload() }
    catch (e) { alert(`删除失败：${e instanceof Error ? e.message : e}`) }
  }

  const targetName = (job: CronJob) => {
    const pool = job.kind === 'tool' ? options?.tools : options?.agents
    return pool?.find((t) => t.id === job.target_id)?.name || job.target_id
  }

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="sticky top-0 z-10 flex items-center gap-2 px-4 h-11 bg-maia-surface border-b border-maia-border shrink-0">
        <CalendarClock className="h-4 w-4 text-cyan-500" />
        <span className="text-[14px] font-medium text-maia-text">定时任务</span>
        <span className="text-[12px] text-maia-text-muted">按计划周期执行工具 / Agent</span>
        <div className="flex-1" />
        <button onClick={reload} title="刷新"
          className="flex items-center gap-1 h-7 px-2 rounded-md text-[12px] text-maia-text-muted hover:text-maia-text hover:bg-maia-sidebar-hover transition-colors">
          <RefreshCw className="h-3 w-3" />刷新
        </button>
        <button onClick={() => setEditing('new')}
          className="flex items-center gap-1 h-7 px-2.5 rounded-md bg-maia-accent text-white text-[12px] hover:opacity-90 transition-opacity">
          <Plus className="h-3 w-3" />新建任务
        </button>
      </div>

      {error && <div className="mx-4 mt-3 p-2 rounded-md bg-maia-danger/10 text-maia-danger text-[12px]">{error}</div>}

      <div className="p-4 space-y-2">
        {jobs.length === 0 && !editing && (
          <div className="text-center text-[13px] text-maia-text-muted py-16">
            暂无定时任务。点击右上角「新建任务」，选择工具与执行频率即可创建周期任务。
          </div>
        )}
        {jobs.map((job) => (
          <div key={job.id} className="rounded-lg border border-maia-border bg-maia-surface overflow-hidden">
            <div className="flex items-center gap-3 px-3 py-2.5">
              <button onClick={() => toggleExpand(job.id)} title="展开执行历史"
                className="shrink-0 text-maia-text-muted hover:text-maia-text">
                {expanded === job.id ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </button>
              {job.kind === 'tool' ? <Wrench className="h-3.5 w-3.5 text-amber-500 shrink-0" /> : <Bot className="h-3.5 w-3.5 text-purple-500 shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className={cn('text-[13px] font-medium truncate', job.enabled ? 'text-maia-text' : 'text-maia-text-muted line-through')}>{job.name}</span>
                  <span className="text-[11px] px-1.5 py-0.5 rounded bg-maia-bg border border-maia-border text-maia-text-muted shrink-0">{targetName(job)}</span>
                </div>
                <div className="text-[12px] text-maia-text-muted mt-0.5">
                  {describeCron(job.cron)}
                  <span className="mx-1.5 opacity-50">·</span>下次 {job.enabled ? (job.next_run_local || fmtTs(job.next_run_at)) : '已停用'}
                  {job.last_run_local && <><span className="mx-1.5 opacity-50">·</span>上次 {job.last_run_local}
                    {job.last_status && <span className={cn('ml-1 px-1 rounded', RUN_BADGE[job.last_status] || 'bg-maia-text-muted/15 text-maia-text-muted')}>{RUN_LABEL[job.last_status] || job.last_status}</span>}
                  </>}
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button onClick={() => doTrigger(job)} disabled={busyId === job.id} title="立即执行一次"
                  className="flex items-center justify-center h-6 w-6 rounded hover:bg-maia-sidebar-hover text-maia-text-muted hover:text-maia-accent transition-colors disabled:opacity-50">
                  {busyId === job.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                </button>
                <button onClick={() => doToggle(job)} title={job.enabled ? '停用' : '启用'}
                  className={cn('text-[11px] px-2 h-6 rounded border transition-colors',
                    job.enabled ? 'border-maia-border text-maia-text-muted hover:text-maia-text' : 'border-maia-success/40 text-maia-success hover:bg-maia-success/10')}>
                  {job.enabled ? '停用' : '启用'}
                </button>
                <button onClick={() => setEditing(job)} title="编辑"
                  className="flex items-center justify-center h-6 w-6 rounded hover:bg-maia-sidebar-hover text-maia-text-muted hover:text-maia-text transition-colors">
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button onClick={() => doDelete(job)} title="删除"
                  className="flex items-center justify-center h-6 w-6 rounded hover:bg-maia-danger-bg text-maia-text-muted hover:text-maia-danger transition-colors">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            {expanded === job.id && (
              <div className="border-t border-maia-border bg-maia-bg/50 px-3 py-2">
                {runs.length === 0 && <div className="text-[12px] text-maia-text-muted py-2">暂无执行记录</div>}
                {runs.map((r) => (
                  <div key={r.id} className="flex items-center gap-2 py-1 text-[12px] border-b border-maia-border/50 last:border-0">
                    <span className={cn('shrink-0 px-1.5 py-0.5 rounded', RUN_BADGE[r.status] || 'bg-maia-text-muted/15 text-maia-text-muted')}>{RUN_LABEL[r.status] || r.status}</span>
                    <span className="text-maia-text-muted shrink-0">{fmtTs(r.started_at)}</span>
                    <span className="text-maia-text-muted shrink-0">{r.trigger === 'manual' ? '手动' : '计划'}</span>
                    {r.duration_ms != null && <span className="text-maia-text-muted shrink-0">{(r.duration_ms / 1000).toFixed(1)}s</span>}
                    <span className="truncate text-maia-text-secondary" title={r.error || r.message}>
                      {r.error || r.message}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {editing && (
        <JobForm
          job={editing === 'new' ? null : editing}
          options={options}
          onDone={() => { setEditing(null); reload() }}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  )
}

// ── 新建 / 编辑表单 ──

function JobForm({ job, options, onDone, onCancel }: {
  job: CronJob | null
  options: CronOptions | null
  onDone: () => void
  onCancel: () => void
}) {
  const targets = useMemo(
    () => (job?.kind === 'agent' ? options?.agents : options?.tools) ?? [],
    [job?.kind, options],
  )
  const [name, setName] = useState(job?.name || '')
  const [kind, setKind] = useState<'tool' | 'agent'>(job?.kind || 'tool')
  const [targetId, setTargetId] = useState(job?.target_id || '')
  const [timezone, setTimezone] = useState(job?.timezone || 'Asia/Shanghai')
  const [timeout_, setTimeout_] = useState(job?.timeout ?? 600)
  const [misfire, setMisfire] = useState<'skip' | 'fire_once'>(job?.misfire || 'skip')
  const [enabled, setEnabled] = useState(job?.enabled ?? true)
  const form0 = job ? parseCronToForm(job.cron) : { freq: 'weekday' as Freq, hour: 17, minute: 0, dow: '5', dom: 1, everyN: 15, custom: '' }
  const [freq, setFreq] = useState<Freq>(form0.freq)
  const [hour, setHour] = useState(form0.hour)
  const [minute, setMinute] = useState(form0.minute)
  const [dow, setDow] = useState(form0.dow)
  const [dom, setDom] = useState(form0.dom)
  const [everyN, setEveryN] = useState(form0.everyN)
  const [customCron, setCustomCron] = useState(form0.custom)
  const [paramValues, setParamValues] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(job?.params || {})) out[k] = typeof v === 'string' ? v : JSON.stringify(v)
    return out
  })
  const [specFields, setSpecFields] = useState<SpecField[]>([])
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const cron = buildCron(freq, hour, minute, dow, dom, everyN, customCron)

  // 切换类型时清空目标；目标决定可用候选列表
  const switchKind = (k: 'tool' | 'agent') => {
    if (k === kind) return
    setKind(k); setTargetId(''); setSpecFields([])
  }

  // 选定目标后拉 spec 渲染参数表单；名称自动预填
  useEffect(() => {
    if (!targetId) { setSpecFields([]); return }
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch(kind === 'tool' ? `/api/tool/${targetId}` : `/api/agent/${targetId}`)
        if (!res.ok) return
        const data = await res.json()
        if (cancelled) return
        const md: string = data.spec_md || ''
        setSpecFields(kind === 'tool' ? parseToolInputs(md) : parseAgentInputs(md))
        if (!job && !name) {
          const pool = kind === 'tool' ? options?.tools : options?.agents
          const tname = pool?.find((t) => t.id === targetId)?.name || targetId
          setName(`${tname} 定时任务`)
        }
      } catch { /* spec 拉不到则退化为无参数表单 */ }
    }
    load()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId, kind])

  // 参数表单出现且无已有值时，用 spec 默认值预填
  useEffect(() => {
    if (job || specFields.length === 0) return
    setParamValues((prev) => {
      const next = { ...prev }
      for (const f of specFields) {
        if (next[f.name] === undefined && f.default != null) next[f.name] = stripQuotes(f.default)
      }
      return next
    })
  }, [specFields, job])

  const save = async () => {
    setErr('')
    if (!name.trim()) { setErr('请填写任务名称'); return }
    if (!targetId) { setErr('请选择执行目标'); return }
    if (!cron) { setErr('请填写 cron 表达式'); return }
    // 组装参数：空字符串的可选参数省略；boolean/integer 转型
    const params: Record<string, unknown> = {}
    for (const f of specFields) {
      const raw = (paramValues[f.name] ?? '').trim()
      if (raw === '') { if (f.required) { setErr(`参数 ${f.name} 为必填`); return } continue }
      if (/^(boolean|bool)$/i.test(f.type)) params[f.name] = raw === 'true'
      else if (/^(integer|int|number|float)$/i.test(f.type)) params[f.name] = Number(raw)
      else params[f.name] = raw
    }
    // 无 spec 字段时（Agent 无输入表 / 编辑态未及解析）保留原始 params
    const finalParams = specFields.length === 0 ? (job?.params ?? {}) : params
    const payload: CronJobPayload = {
      name: name.trim(), kind, target_id: targetId, params: finalParams,
      cron, timezone, enabled, timeout: timeout_, misfire,
    }
    setSaving(true)
    try {
      if (job) await cronApi.update(job.id, payload)
      else await cronApi.create(payload)
      onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setSaving(false) }
  }

  const label = 'text-[12px] text-maia-text-muted shrink-0 w-16'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onMouseDown={onCancel}>
      <div onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-2xl max-h-[88vh] overflow-auto rounded-xl bg-maia-surface border border-maia-border shadow-2xl">
        <div className="sticky top-0 flex items-center justify-between px-4 h-11 bg-maia-surface border-b border-maia-border z-10">
          <span className="text-[14px] font-medium text-maia-text">{job ? '编辑定时任务' : '新建定时任务'}</span>
          <button onClick={onCancel} className="text-[12px] text-maia-text-muted hover:text-maia-text">关闭</button>
        </div>

        <div className="p-4 space-y-3">
          {/* 任务类型 */}
          <div className="flex items-center gap-2">
            <span className={label}>任务类型</span>
            <div className="flex rounded-md border border-maia-border overflow-hidden">
              {([['tool', '执行工具', Wrench], ['agent', '执行 Agent', Bot]] as const).map(([k, txt, Icon]) => (
                <button key={k} onClick={() => switchKind(k)}
                  className={cn('flex items-center gap-1.5 h-8 px-3 text-[13px] transition-colors',
                    kind === k ? 'bg-maia-accent text-white' : 'bg-maia-surface text-maia-text-muted hover:text-maia-text')}>
                  <Icon className="h-3.5 w-3.5" />{txt}
                </button>
              ))}
            </div>
          </div>

          {/* 执行目标 */}
          <div className="flex items-center gap-2">
            <span className={label}>执行目标</span>
            <select value={targetId} onChange={(e) => setTargetId(e.target.value)} className={cn(SELECT_CLS, 'flex-1')}>
              <option value="">— 请选择{kind === 'tool' ? '工具' : ' Agent'} —</option>
              {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>

          {/* 任务名称 */}
          <div className="flex items-center gap-2">
            <span className={label}>任务名称</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="选择目标后自动生成，可修改"
              className="h-8 flex-1 rounded-md border border-maia-border bg-maia-surface px-2 text-[13px] text-maia-text focus:outline-none focus:border-maia-accent" />
          </div>

          {/* 调度频率 */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className={label}>调度频率</span>
            <select value={freq} onChange={(e) => setFreq(e.target.value as Freq)} className={SELECT_CLS}>
              {FREQ_OPTIONS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
            {(freq === 'daily' || freq === 'weekday' || freq === 'weekly' || freq === 'monthly') && (
              <>
                {freq === 'weekly' && (
                  <select value={dow} onChange={(e) => setDow(e.target.value)} className={SELECT_CLS}>
                    {DOW_OPTIONS.map((d) => <option key={d.v} value={d.v}>{d.label}</option>)}
                  </select>
                )}
                {freq === 'monthly' && (
                  <select value={dom} onChange={(e) => setDom(Number(e.target.value))} className={SELECT_CLS}>
                    {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d} 日</option>)}
                  </select>
                )}
                <select value={hour} onChange={(e) => setHour(Number(e.target.value))} className={SELECT_CLS}>
                  {Array.from({ length: 24 }, (_, i) => i).map((h) => <option key={h} value={h}>{String(h).padStart(2, '0')} 时</option>)}
                </select>
                <select value={minute} onChange={(e) => setMinute(Number(e.target.value))} className={SELECT_CLS}>
                  {MINUTE_OPTIONS.map((m) => <option key={m} value={m}>{String(m).padStart(2, '0')} 分</option>)}
                </select>
              </>
            )}
            {freq === 'everyN' && (
              <select value={everyN} onChange={(e) => setEveryN(Number(e.target.value))} className={SELECT_CLS}>
                {[5, 10, 15, 20, 30].map((n) => <option key={n} value={n}>{n} 分钟</option>)}
              </select>
            )}
          </div>
          {freq === 'custom' && (
            <div className="flex items-center gap-2">
              <span className={label}>cron 表达式</span>
              <input value={customCron} onChange={(e) => setCustomCron(e.target.value)} placeholder="分 时 日 月 周，如 0 17 * * 1-5"
                className="h-8 flex-1 rounded-md border border-maia-border bg-maia-surface px-2 text-[13px] font-mono text-maia-text focus:outline-none focus:border-maia-accent" />
            </div>
          )}
          <div className="flex items-center gap-2 pl-16 text-[12px] text-maia-text-muted">
            <span>生成：</span>
            <code className="px-1.5 py-0.5 rounded bg-maia-bg border border-maia-border font-mono">{cron || '-'}</code>
            <span>{describeCron(cron)}</span>
          </div>

          {/* 执行参数（按 spec 输入表自动渲染） */}
          {specFields.length > 0 && (
            <div className="rounded-lg border border-maia-border">
              <div className="px-3 py-1.5 text-[12px] font-medium text-maia-text-secondary bg-maia-bg/60 border-b border-maia-border">
                执行参数（已按工具规范自动生成，默认值预填）
              </div>
              <div className="p-3 space-y-2">
                {specFields.map((f) => {
                  const enumVals = extractEnum(f.desc)
                  const isBool = /^(boolean|bool)$/i.test(f.type)
                  const val = paramValues[f.name] ?? ''
                  return (
                    <div key={f.name} className="flex items-center gap-2">
                      <span className="w-28 shrink-0 text-[12px] text-maia-text-secondary truncate" title={`${f.type}${f.required ? ' · 必填' : ''}　${f.desc}`}>
                        {f.name}{f.required && <span className="text-maia-danger">*</span>}
                      </span>
                      {enumVals.length >= 2 ? (
                        <select value={val} onChange={(e) => setParamValues({ ...paramValues, [f.name]: e.target.value })} className={cn(SELECT_CLS, 'flex-1')}>
                          <option value="">（使用默认）</option>
                          {enumVals.map((v) => <option key={v} value={v}>{v}</option>)}
                        </select>
                      ) : isBool ? (
                        <select value={val} onChange={(e) => setParamValues({ ...paramValues, [f.name]: e.target.value })} className={cn(SELECT_CLS, 'flex-1')}>
                          <option value="">（使用默认）</option>
                          <option value="true">是</option>
                          <option value="false">否</option>
                        </select>
                      ) : (
                        <input
                          type={/^(integer|int|number|float)$/i.test(f.type) ? 'number' : 'text'}
                          value={val}
                          onChange={(e) => setParamValues({ ...paramValues, [f.name]: e.target.value })}
                          placeholder={f.default != null ? `默认 ${f.default}` : f.desc.slice(0, 40)}
                          className="h-8 flex-1 min-w-0 rounded-md border border-maia-border bg-maia-surface px-2 text-[12px] text-maia-text focus:outline-none focus:border-maia-accent"
                        />
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* 高级选项 */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className={label}>高级选项</span>
            <select value={timezone} onChange={(e) => setTimezone(e.target.value)} className={SELECT_CLS} title="时区">
              {(options?.timezones || ['Asia/Shanghai']).map((tz) => <option key={tz} value={tz}>{tz}</option>)}
            </select>
            <select value={timeout_} onChange={(e) => setTimeout_(Number(e.target.value))} className={SELECT_CLS} title="单次执行超时">
              {TIMEOUT_OPTIONS.map((t) => <option key={t} value={t}>超时 {t >= 60 ? `${t / 60} 分钟` : `${t} 秒`}</option>)}
            </select>
            <select value={misfire} onChange={(e) => setMisfire(e.target.value as 'skip' | 'fire_once')} className={SELECT_CLS} title="错过执行点的策略">
              <option value="skip">错过则跳过</option>
              <option value="fire_once">错过补跑一次</option>
            </select>
            <label className="flex items-center gap-1.5 text-[12px] text-maia-text-secondary cursor-pointer ml-1">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-[var(--maia-accent,#5b8cff)]" />
              创建后立即启用
            </label>
          </div>

          {err && <div className="p-2 rounded-md bg-maia-danger/10 text-maia-danger text-[12px]">{err}</div>}
        </div>

        <div className="sticky bottom-0 flex items-center justify-end gap-2 px-4 h-12 bg-maia-surface border-t border-maia-border">
          <button onClick={onCancel} className="h-8 px-3 rounded-md text-[13px] text-maia-text-muted hover:text-maia-text hover:bg-maia-sidebar-hover transition-colors">取消</button>
          <button onClick={save} disabled={saving || !targetId}
            className="flex items-center gap-1.5 h-8 px-4 rounded-md bg-maia-accent text-white text-[13px] hover:opacity-90 disabled:opacity-50 transition-opacity">
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {job ? '保存修改' : '创建任务'}
          </button>
        </div>
      </div>
    </div>
  )
}

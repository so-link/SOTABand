import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Wrench, FileCode, FileText, X, Play, Loader2, Download,
  CheckCheck, Save, Bot, Code, Pencil, Plus, Square,
  Database,
} from 'lucide-react'
import { FileSelect } from '@/components/ui/file-select'
import { DataFilePicker } from '@/components/ui/data-file-picker'
import { Highlight, themes } from 'prism-react-renderer'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'
import { toolApi } from '@/services/api/tool'
import { authedUrl } from '@/services/api/auth'
import { useTabIndent } from '@/hooks/use-tab-indent'
import { cn } from '@/lib/utils'
import type { ToolResource } from '@/types/resources'
import { clearToolDraft, loadToolDraft, saveToolDraft, type ToolDetailDraft } from '@/lib/tool-detail-draft'

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
  const [dataPickerFor, setDataPickerFor] = useState<string | null>(null)
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
  // 覆盖层（fork）状态：内置资源要修改先复制为私有副本；null=未知/非内置
  interface ForkStatus { is_builtin: boolean; has_copy: boolean; forked: boolean; upstream_changed: boolean; forked_from?: { forked_at: string } | null }
  const [forkStatus, setForkStatus] = useState<ForkStatus | null>(null)
  const [detailSource, setDetailSource] = useState<string | null>(null)
  const [forkBusy, setForkBusy] = useState(false)

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
    setForkStatus(null)
    setDetailSource(null)
    fetch(`${BASE_URL}/api/tool/${id}`)
      .then(r => r.json())
      .then(data => {
        const md = data.spec_md || ''
        const c = data.code || ''
        setSpecMd(md)
        setCode(c)
        setDetailSource(data.source || null)
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
        // fork 状态决定徽章与编辑入口行为：非内置直接返回 null 不打扰
        toolApi.getForkStatus(id)
          .then((fs) => setForkStatus(fs.is_builtin ? fs : null))
          .catch(() => setForkStatus(null))

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

  const inputs = parseInputs(specMd)

  /** 进入编辑器：内置资源且还没有副本时，先复制为我的副本再进（方案 C）。
   *  直接编辑内置 = 改 git 里的系统文件，一人改动全员跟着变，禁止。 */
  const openEditor = async () => {
    if (!tool) return
    if (forkStatus?.is_builtin && !forkStatus.has_copy) {
      const ok = window.confirm(
        `「${tool.name}」是内置工具（随系统分发，不可直接修改）。\n\n` +
        '点击「确定」：复制为我的副本后进入编辑\n' +
        '点击「取消」：留在详情页（不复制）'
      )
      if (!ok) return
      setForkBusy(true)
      try {
        await toolApi.fork(tool.id)
        setForkStatus({ ...forkStatus, has_copy: true, forked: true })
      } catch (e) {
        window.alert(`复制副本失败：${String(e)}`)
        setForkBusy(false)
        return
      }
      setForkBusy(false)
    }
    const params = (inputs || []).map((f: InputField) => ({
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
      // Step1 的参考代码一并带入并作为基线，
      // 否则编辑器里看不到、改也无法单独保存
      referenceCode,
    })
    setActiveView('tool-editor')
  }

  /** 丢弃副本：删除私有文件后详情重新加载，回到内置版本 */
  const discardFork = async () => {
    if (!tool) return
    if (!window.confirm(
      `确定丢弃「${tool.name}」的我的副本吗？\n\n副本上的所有修改将丢失，\n恢复为直接使用内置版本。`)) return
    setForkBusy(true)
    try {
      await toolApi.discardFork(tool.id)
      setForkStatus({ is_builtin: true, has_copy: false, forked: false, upstream_changed: false })
      // 详情内容也要跟着变（读回内置版本），重新拉一次
      const data = await (await fetch(`${BASE_URL}/api/tool/${tool.id}`)).json()
      setSpecMd(data.spec_md || '')
      const c = data.code || ''
      setCode(c); setEditedCode(c); setCodeModified(false)
    } catch (e) {
      window.alert(`丢弃副本失败：${String(e)}`)
    }
    setForkBusy(false)
  }

  /** 上游有更新时：按当前内置重新复制（我的副本改动会丢弃） */
  const refork = async () => {
    if (!tool) return
    if (!window.confirm(
      '内置版本已有更新。重新复制将以最新内置内容覆盖我的副本，\n副本上的修改会丢失。确定重新复制吗？')) return
    setForkBusy(true)
    try {
      await toolApi.fork(tool.id)
      setForkStatus({ is_builtin: true, has_copy: true, forked: true, upstream_changed: false })
    } catch (e) {
      window.alert(`复制失败：${String(e)}`)
    }
    setForkBusy(false)
  }

  // 来源徽章：内置（未 fork）/ 我的副本 / 我的工具。
  // 用原生 span 而非 Badge：需要 title 悬浮解释三者区别，Badge 不透传 title。
  const forkBadge = (() => {
    if (forkStatus?.is_builtin) {
      return forkStatus.has_copy
        ? { label: '我的副本', cls: 'bg-maia-warning-bg text-maia-warning border-maia-warning/20', title: '已复制为私有副本：编辑与执行都用副本，内置版本不受影响' }
        : { label: '内置', cls: 'bg-maia-sidebar-active text-maia-text-secondary border-maia-border', title: '内置工具（随系统分发）：要修改请先复制为我的副本' }
    }
    if (detailSource === 'private' || forkStatus?.has_copy) {
      return { label: '我的工具', cls: 'bg-maia-success-bg text-maia-success border-maia-success/20', title: '我创建的工具（私有目录）' }
    }
    return null
  })()

  // ── 导出为迁移包（.sotatool.zip）：可在另一套部署里导入 ──
  const handleExport = async () => {
    if (!tool) return
    setExporting(true)
    try {
      const res = await fetch(`${BASE_URL}/api/migrate/tool/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool_id: tool.id }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `导出失败 (${res.status})`)
      setExportInfo({ name: data.file_name, path: data.bundle_path, size: data.size })
    } catch (e) {
      window.alert(`导出失败：${(e as Error).message}`)
    } finally {
      setExporting(false)
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

  /** 内置资源不可直接写：保存前先确保有我的副本（就地复制到私有），返回可否继续。
   *  状态从服务端现取而不是读组件里的 forkStatus：调用点有的在 useCallback
   *  闭包里，组件状态可能已过期；而过期成"没有副本"会误触发重新复制，
   *  把已有副本（可能带着未同步的修改）整份覆盖掉。 */
  const ensurePrivateCopy = async (): Promise<boolean> => {
    if (!tool) return false
    const st = await toolApi.getForkStatus(tool.id).catch(() => null)
    if (st) setForkStatus(st.is_builtin ? st : null)
    if (!st || !st.is_builtin || st.has_copy) return true
    const ok = window.confirm(
      `「${tool.name}」是内置工具（随系统分发，不可直接修改）。\n\n` +
      '点击「确定」：复制为我的副本后保存\n' +
      '点击「取消」：不保存'
    )
    if (!ok) return false
    try {
      await toolApi.fork(tool.id)
      setForkStatus({ ...st, has_copy: true, forked: true })
      return true
    } catch (e) {
      window.alert(`复制副本失败：${String(e)}`)
      return false
    }
  }

  const updateCode = async () => {
    if (!editedCode.trim()) return
    if (!(await ensurePrivateCopy())) return
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
    // 调试成功后后端会把新代码就地落盘：内置资源同样要先有我的副本
    if (!(await ensurePrivateCopy())) return
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
          {forkBadge && (
            <span
              title={forkBadge.title}
              className={cn(
                'inline-flex items-center rounded-full px-2 py-0.5 text-[12px] font-semibold border shrink-0',
                forkBadge.cls,
              )}
            >
              {forkBadge.label}
            </span>
          )}

          <div className="ml-auto flex items-center gap-2 shrink-0">
            {forkStatus?.is_builtin && forkStatus.has_copy && (
              <button
                onClick={discardFork}
                disabled={forkBusy}
                title="删除我的副本，恢复为直接使用内置版本（副本上的修改会丢失）"
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-maia-border text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text transition-colors shrink-0 disabled:opacity-40"
              >
                丢弃副本
              </button>
            )}
            {forkStatus?.upstream_changed && (
              <button
                onClick={refork}
                disabled={forkBusy}
                title="内置版本已被更新（例如系统升级）。重新复制会以最新内置内容覆盖我的副本"
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-maia-warning/30 bg-maia-warning-bg text-maia-warning hover:opacity-90 transition-colors shrink-0 disabled:opacity-40"
              >
                内置已更新，重新复制
              </button>
            )}
            <a
              href={authedUrl(`${BASE_URL}/api/tool/${encodeURIComponent(tool.id)}/export`)}
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
            onClick={() => void openEditor()}
            disabled={forkBusy}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-transparent text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text transition-colors shrink-0 ml-auto disabled:opacity-40"
            title={forkStatus?.is_builtin && !forkStatus.has_copy
              ? '内置工具：先复制为我的副本再编辑'
              : '带齐现有文档与代码，从审阅步进入编辑'}
          >
            {forkBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pencil className="h-3.5 w-3.5" />}
            {forkBusy ? '复制中…' : '编辑'}
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-1 text-[11px] text-emerald-500 hover:underline ml-2 disabled:opacity-50 shrink-0"
            title="导出为 .sotatool.zip 迁移包（含依赖清单），可在其它环境导入"
          >
            {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
            {exporting ? '导出中…' : '导出迁移包'}
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
            href={`${BASE_URL}/api/migrate/bundles/${encodeURIComponent(exportInfo.name)}`}
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
                      {isFilePathParam(f) ? (
                        <div className="flex items-center gap-1.5">
                          <FileSelect
                            value={formValues[f.name] || ''}
                            onChange={(v) => setFormValues(p => ({ ...p, [f.name]: v }))}
                            placeholder={f.required ? '必选文件' : '可选文件'}
                            className="flex-1 min-w-0"
                          />
                          <button
                            type="button"
                            onClick={() => setDataPickerFor(f.name)}
                            title="从数据空间选择数据集文件（data_path）"
                            className="shrink-0 h-8 px-2 rounded border border-maia-border bg-maia-surface hover:border-maia-accent/50 hover:text-maia-accent text-maia-text-muted text-[12px]"
                          >
                            <Database className="h-3.5 w-3.5" />
                            数据空间
                          </button>
                        </div>
                      ) : (
                        <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                          placeholder={f.required ? '必填' : f.default || '可选'}
                          className="h-8 w-full rounded border border-maia-border bg-maia-surface px-3 text-[13px] text-maia-text outline-none focus:border-maia-accent/40" />
                      )}
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
                <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3 border border-maia-border">{JSON.stringify(output, null, 2)}</pre>
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
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3">{specMd || '加载中...'}</pre>
          </CardBody></Card>)}
          {showDemand && demandText && (<Card className="border-maia-border"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-[13px] font-medium text-maia-text-secondary">用户需求描述</span><button onClick={() => setShowDemand(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3">{demandText}</pre>
          </CardBody></Card>)}
          {showReference && referenceCode && (<Card className="border-purple-200 bg-purple-50/30"><CardBody>
            <div className="flex items-center justify-between mb-2"><span className="text-[13px] font-medium text-maia-accent">参考代码</span><button onClick={() => setShowReference(false)}><X className="h-3 w-3 text-maia-text-muted" /></button></div>
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3 border border-maia-border">{referenceCode}</pre>
          </CardBody></Card>)}
          <div>
            <h3 className="text-sm font-semibold text-maia-text-heading mb-3">输入参数</h3>
            {inputs.length === 0 ? (<p className="text-[13px] text-maia-text-muted">此工具无需输入参数</p>) : (
              <div className="space-y-3">
                {inputs.map(f => (<div key={f.name}>
                  <label className="text-[12px] font-medium text-maia-text-secondary">{f.name}{f.required && <span className="text-maia-danger ml-0.5">*</span>}<span className="text-maia-text-muted font-normal ml-1">({f.type})</span></label>
                  {f.desc && <p className="text-[12px] text-maia-text-muted mb-1">{f.desc}</p>}
                  {isFilePathParam(f) ? (
                    <div className="flex items-center gap-1.5">
                      <FileSelect
                        value={formValues[f.name] || ''}
                        onChange={(v) => setFormValues(p => ({ ...p, [f.name]: v }))}
                        placeholder={f.required ? '必选文件' : '可选文件'}
                        className="flex-1 min-w-0"
                      />
                      <button
                        type="button"
                        onClick={() => setDataPickerFor(f.name)}
                        title="从数据空间选择数据集文件（data_path）"
                        className="shrink-0 h-8 px-2 rounded border border-maia-border bg-maia-surface hover:border-maia-accent/50 hover:text-maia-accent text-maia-text-muted text-[12px]"
                      >
                        <Database className="h-3.5 w-3.5" />
                        数据空间
                      </button>
                    </div>
                  ) : (
                    <input type="text" value={formValues[f.name] || ''} onChange={(e) => setFormValues(p => ({ ...p, [f.name]: e.target.value }))}
                      placeholder={f.required ? '必填' : f.default || '可选'} className="h-8 w-full rounded border border-maia-border bg-maia-surface px-3 text-[13px] text-maia-text outline-none focus:border-maia-accent/40" />
                  )}
                </div>))}
              </div>)}
          </div>
          {error && <div className="p-3 rounded-lg border border-red-200 bg-red-50 text-[13px] text-maia-danger">{error}</div>}
          {output && (<Card className="border-emerald-200 bg-emerald-50/30"><CardBody>
            <div className="flex items-center gap-1.5 mb-2">
              <div className={`h-2 w-2 rounded-full ${output.status === 'success' ? 'bg-maia-success' : 'bg-maia-danger'}`} />
              <span className="text-[13px] font-medium text-maia-text-secondary">{output.status === 'success' ? '执行成功' : '执行失败'}</span>
            </div>
            <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3 border border-maia-border">{JSON.stringify(output, null, 2)}</pre>
          </CardBody></Card>)}
          </div>
        )}
      </div>
      {/* 数据集文件选择弹窗（值为 data_path） */}
      {dataPickerFor && (
        <DataFilePicker
          paramName={dataPickerFor}
          onSelect={(path) => {
            setFormValues(p => ({ ...p, [dataPickerFor]: path }))
            setDataPickerFor(null)
          }}
          onClose={() => setDataPickerFor(null)}
        />
      )}
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

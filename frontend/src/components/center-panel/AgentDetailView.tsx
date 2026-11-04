import { useState, useEffect, useRef } from 'react'
import {
  Bot, Send, Play, Square, RefreshCw, Loader2, FileText, FileCode, X, Pencil, Save, FolderOpen, Download,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { useAgentEditorStore } from '@/stores/agent-editor-store'
import { agentApi } from '@/services/api/agent'
import { useTabIndent } from '@/hooks/use-tab-indent'
import type { AgentResource } from '@/types/resources'
import type { InlineCard as InlineCardType } from '@/types/chat'
import { apiFetchRaw, apiGet, apiPost, assertOk } from '@/services/api/client'
import { InlineCard } from './InlineCard'
import { WorkspaceFilePicker } from './WorkspaceFilePicker'

export function AgentDetailView({ agentId }: { agentId?: string } = {}) {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const agents = useResourceStore((s) => s.agentResources)
  // 指定 agentId 时（独立标签页），从列表中定位该 Agent；
  // 否则回退到全局选中的资源（兼容旧的 'agent-detail' 视图）。
  const agent: AgentResource | null = agentId
    ? ((agents.find((a: { id: string }) => a.id === agentId) as AgentResource | undefined) || null)
    : (selectedResource as AgentResource | null)
  // Python 代码用 4 空格缩进
  const onCodeTabIndent = useTabIndent('    ')

  // Agent 类型（三类）：interactive / task / web-service
  const role = (agent?.role as string) || 'task'
  const isTask = role === 'task'
  const isWebService = role === 'web-service'

  // Agent runtime state
  const [running, setRunning] = useState(false)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Array<{ role: string; content: string; cards?: InlineCardType[] }>>([])
  const [streaming, setStreaming] = useState('')
  const [streamCards, setStreamCards] = useState<InlineCardType[]>([])
  const [isSending, setIsSending] = useState(false)
  const [loading, setLoading] = useState(false)

  // 任务式 Agent：启动参数（由 spec.inputs 渲染）
  const [taskParams, setTaskParams] = useState<Record<string, string>>({})

  // Web 服务式 Agent：服务信息与 API 测试
  const [serviceInfo, setServiceInfo] = useState<{ running: boolean; host?: string; port?: number; port_ready?: boolean; apis?: Array<{ name: string; method?: string; path?: string; params?: Array<{ name: string; type?: string; required?: boolean; desc?: string }> }> }>({ running: false })
  const [testApi, setTestApi] = useState('')
  const [testParams, setTestParams] = useState<Record<string, string>>({})
  const [testResult, setTestResult] = useState('')
  const [testing, setTesting] = useState(false)
  // 文件路径型参数：打开工作区文件选择器时记录目标参数名
  const [pickForParam, setPickForParam] = useState<string | null>(null)

  // Detail panels state
  const [specMd, setSpecMd] = useState('')
  const [code, setCode] = useState('')
  const [demandMd, setDemandMd] = useState('')
  const [hasDemand, setHasDemand] = useState(false)
  const [showSpec, setShowSpec] = useState(false)
  const [showCode, setShowCode] = useState(false)
  const [showDemand, setShowDemand] = useState(false)

  // 代码手工微调
  const [isEditingCode, setIsEditingCode] = useState(false)
  const [draftCode, setDraftCode] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')

  // 导出迁移包（.sotaagent.zip）的状态。必须声明在 `if (!agent) return` 之前，
  // 否则 agent 由空变有会让 hooks 数量变化（React 会直接报错）。
  const [exporting, setExporting] = useState(false)
  const [exportInfo, setExportInfo] = useState<{ name: string; path: string; size: number } | null>(null)

  const setActiveView = useUIStore((s) => s.setActiveView)

  const bottomRef = useRef<HTMLDivElement>(null)

  // Fetch agent details on mount / agent change
  useEffect(() => {
    if (!agent) return
    apiGet<any>(`/api/agent/${agent.id}`)
      .then((data) => {
        setSpecMd(data.spec_md || '')
        setCode(data.code || '')
        if (data.has_demand) {
          setHasDemand(true)
          setDemandMd(data.demand_md || '')
        }
      })
      .catch(() => {})
  }, [agent])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  // Web 服务式 Agent：加载服务信息（未启动时也拉取 API 声明用于展示）
  useEffect(() => {
    if (agent && isWebService) { loadServiceInfo() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.id, isWebService])

  // 读取 Agent 运行状态（轮询，避免"进程已退出/已在别处启动"时界面状态失真）
  useEffect(() => {
    if (!agent) return
    const agentId = agent.id
    let alive = true

    const tick = async () => {
      try {
        const d = await apiGet<any>(`/api/agent/${agentId}/status`)
        if (!alive) return
        setRunning(!!d.running)
        // web-service：端口可能比进程晚就绪，或已在别处重启，顺带同步服务信息
        if (isWebService) {
          const svc = await apiGet<any>(`/api/agent/${agentId}/service`)
          if (alive) {
            setServiceInfo((prev) => ({ ...prev, ...svc }))
          }
        }
      } catch { /* 网络抖动忽略，下一轮再试 */ }
    }

    void tick()
    const timer = window.setInterval(tick, 5000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.id, isWebService])

  if (!agent) {
    return (
      <div className="flex items-center justify-center h-full text-maia-text-muted text-sm">
        请在左侧 Agent 空间选择一个 Agent
      </div>
    )
  }

  // Web 服务式 Agent：加载服务信息（含未启动时的 API 声明）
  const loadServiceInfo = async () => {
    if (!agent || !isWebService) return
    try {
      const data = await apiGet<any>(`/api/agent/${agent.id}/service`)
      setServiceInfo(data)
      // 默认选中第一个 API
      const apis = data.apis || []
      if (apis.length && !apis.some((a: { name: string }) => a.name === testApi)) {
        setTestApi(apis[0].name)
      }
    } catch { /* 服务信息拉不到时保持原状态 */ }
  }

  // 发起 API 测试（走后端代理）
  const runApiTest = async () => {
    if (!agent || !testApi || testing) return
    if (!serviceInfo.running || !serviceInfo.port) {
      setTestResult('服务尚未就绪（端口未上报）：请稍候几秒，或点「重启」后重试')
      return
    }
    setTesting(true); setTestResult('')
    try {
      const data = await apiPost<any>(`/api/agent/${agent.id}/test-api`, {
        api_name: testApi,
        params: testParams,
      })
      setTestResult(JSON.stringify(data, null, 2))
    } catch (e) {
      setTestResult(`请求失败: ${String(e)}`)
    } finally {
      setTesting(false)
    }
  }

  const togglePanel = (panel: 'spec' | 'code' | 'demand') => {
    if (panel === 'spec') { setShowSpec(!showSpec); setShowCode(false); setShowDemand(false) }
    else if (panel === 'code') {
      setShowCode(!showCode); setShowSpec(false); setShowDemand(false)
      // 打开代码面板时重置草稿，避免沿用上一个 Agent 的内容
      if (!showCode) { setDraftCode(code); setIsEditingCode(false); setSaveMsg('') }
    }
    else { setShowDemand(!showDemand); setShowSpec(false); setShowCode(false) }
  }

  const codeDirty = draftCode.trim() !== code.trim()

  // 任务式 Agent：从 MD 规范文档的输入段解析启动参数名。
  // 与后端 AgentCodeBuilder._parse_spec_inputs 保持一致，支持：
  //   - 标题："## 输入" / "## 3. 输入规范" / "### 参数"
  //   - 列表："- name: 说明（string，必填）"
  //   - 表格："| name | string | 是 | 说明 |"
  // 解析不到参数名时，启动面板会显示「无启动参数」，Agent 便只能收到空参数。
  const taskInputNames: string[] = (() => {
    if (!isTask || !specMd) return []
    const lines = specMd.split('\n')
    const headRe = /^#{2,3}\s*(?:[\d一二三四五六七八九十]+[.、)．]?\s*)?(输入|输入规范|参数|启动参数)\s*$/
    const names: string[] = []
    let inSection = false
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim()
      if (!inSection) {
        if (headRe.test(line)) inSection = true
        continue
      }
      // 遇到同级或更高级标题即结束
      if (/^#{1,3}\s+\S/.test(line)) break
      if (!line) continue
      const lm = line.match(/^[-*]\s*`?([A-Za-z_][\w-]*)`?\s*[:：]/)
      if (lm) { names.push(lm[1]); continue }
      if (line.startsWith('|')) {
        const cells = line.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
        if (!cells[0] || /^[-:\s]+$/.test(cells[0])) continue          // 分隔行
        const next = lines.slice(i + 1).map((l) => l.trim()).find(Boolean) || ''
        if (/^\|[\s:|-]+\|?$/.test(next)) continue                     // 表头行
        const cm = cells[0].replace(/`/g, '').match(/^[A-Za-z_][\w-]*$/)
        if (cm) names.push(cm[0])
      }
    }
    return Array.from(new Set(names))
  })()

  const handleSaveCode = async () => {
    if (!agent || !codeDirty) return
    setIsSaving(true); setSaveMsg('')
    try {
      await agentApi.saveCode(agent.id, draftCode)
      setCode(draftCode)          // 保存成功后更新基准，清除「已修改」标记
      setIsEditingCode(false)
      setSaveMsg('已保存')
      // 若该 Agent 正在运行，提示需重启才生效
      if (running) setSaveMsg('已保存，重启后生效')
    } catch (e) {
      setSaveMsg(`保存失败: ${String(e)}`)
    } finally {
      setIsSaving(false)
      setTimeout(() => setSaveMsg(''), 3000)
    }
  }

  // 导出为迁移包（.sotaagent.zip）
  const handleExport = async () => {
    if (!agent) return
    setExporting(true)
    try {
      const data = await apiPost<{ file_name: string; bundle_path: string; size: number }>(
        '/api/migrate/agent/export',
        { agent_id: agent.id }
      )
      setExportInfo({ name: data.file_name, path: data.bundle_path, size: data.size })
    } catch (e) {
      window.alert(`导出失败：${(e as Error).message}`)
    } finally {
      setExporting(false)
    }
  }

  const handleEditInEditor = () => {
    if (!agent) return
    // 带齐现有 MD/代码，从「审阅」步进入，避免被迫重走需求描述流程
    useAgentEditorStore.getState().prefillFromAgent({
      agentId: agent.id,
      agentName: agent.name || agent.id,
      description: demandMd || agent.name || agent.id,
      specMd: specMd,
      code: draftCode || code,
    })
    setActiveView('agent-editor')
  }

  const start = async () => {
    setLoading(true)
    try {
      const data = await apiPost<any>(`/api/agent/${agent.id}/start`)
      // 后端在「代码错误 / 端口被占 / 加载 spec 失败」时会返回 status=failed，
      // 旧实现无条件 setRunning(true)，用户只能看到"看起来启动了但用不了"。
      if (data.status === 'failed') {
        setRunning(false)
        setMessages([{ role: 'system', content: `⚠️ 启动失败：${data.error || data.detail || '未知错误'}` }])
      } else {
        setRunning(true)
        setMessages([])
        setStreaming('')
        setStreamCards([])
        if (isWebService) await loadServiceInfo()
      }
    } catch (e) {
      setRunning(false)
      setMessages([{ role: 'system', content: `⚠️ 启动请求失败：${String(e)}` }])
    }
    setLoading(false)
  }

  const stop = async () => {
    setLoading(true)
    try {
      await apiPost(`/api/agent/${agent.id}/stop`)
      setRunning(false)
      if (isWebService) setServiceInfo((s) => ({ ...s, running: false, port: undefined }))
    } catch { /* ignore */ }
    setLoading(false)
  }

  const restart = async () => {
    setLoading(true)
    try {
      // 统一 client：非 2xx 会抛 ApiError；额外校验 200 + status=failed
      // （上游 #11 的语义：重启失败要显式提示，不能静默当作成功）
      const data = await apiPost<{ status?: string; error?: string; detail?: string }>(
        `/api/agent/${agent.id}/restart`
      )
      if (data && data.status === 'failed') {
        throw new Error(data.error || data.detail || 'restart failed')
      }
      setRunning(true)
      setMessages([])
      setStreaming('')
      setStreamCards([])
    } catch (e) {
      setRunning(false)
      setMessages([{ role: 'system', content: `Restart failed: ${String(e)}` }])
    }
    setLoading(false)
  }

  /** 通用执行：把 params 发给 Agent 的 /execute 接口，流式渲染 SSE 事件。 */
  const runExecute = async (params: Record<string, unknown>, echo?: string) => {
    if (isSending) return
    if (echo !== undefined) {
      setMessages((m) => [...m, { role: 'user', content: echo }])
    }
    setIsSending(true)
    setStreaming('')
    setStreamCards([])

    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 120_000)
    let cardSeq = 0
    const localCards: InlineCardType[] = []

    try {
      const res = await apiFetchRaw(`/api/agent/${agent.id}/execute`, {
        method: 'POST',
        body: JSON.stringify(params),
        signal: controller.signal,
      })
      await assertOk(res)

      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let full = ''
      let eventType = ''
      let streamDone = false

      while (!streamDone) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6))
              if (eventType === 'content' || !eventType) {
                full += payload.text || ''
                setStreaming(full)
              } else if (eventType === 'card') {
                // 保存为结构化卡片，由 InlineCard 组件渲染（表格/图片/文件等完整展示），
                // 而不是降级成文本（否则对象字段会显示为 [object Object]，列表无法展示）。
                const card: InlineCardType = {
                  id: `card-${Date.now()}-${cardSeq++}`,
                  type: payload.type || 'result-summary',
                  title: payload.title || '',
                  summary: payload.summary || '',
                  data: payload.data || {},
                }
                localCards.push(card)
                setStreamCards([...localCards])
              } else if (eventType === 'done') {
                setMessages((m) => [...m, { role: 'agent', content: full, cards: [...localCards] }])
                setStreaming('')
                setStreamCards([])
                streamDone = true
              } else if (eventType === 'error') {
                setMessages((m) => [...m, { role: 'system', content: `⚠️ ${payload.message || '执行错误'}` }])
                streamDone = true
              }
              eventType = ''
            } catch { /* skip */ }
          }
        }
      }

      if (!streamDone && (full || localCards.length)) {
        setMessages((m) => [...m, { role: 'agent', content: full, cards: [...localCards] }])
        setStreaming('')
        setStreamCards([])
      }
    } catch (e: unknown) {
      if ((e as Error).name === 'AbortError') {
        setMessages((m) => [...m, { role: 'system', content: '⚠️ 请求超时，Agent 未在 2 分钟内响应' }])
      } else {
        setMessages((m) => [...m, { role: 'system', content: '⚠️ 请求失败' }])
      }
      setStreaming('')
    } finally {
      clearTimeout(timeout)
      setIsSending(false)
    }
  }

  /** 交互式：发送对话消息 */
  const sendMessage = async () => {
    if (!input.trim() || isSending) return
    const userInput = input
    setInput('')
    await runExecute({ content: userInput }, userInput)
  }

  /** 任务式：点击「启动任务」，按启动参数执行既定流程 */
  const startTask = async () => {
    if (!running) {
      setMessages((m) => [...m, { role: 'system', content: '⚠️ 请先点击「启动」按钮启动 Agent 进程' }])
      return
    }
    setMessages([])
    await runExecute({ ...taskParams })
  }

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-maia-accent" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">
            {agent.name}
          </span>
          <Badge variant={running ? 'success' : 'default'}>
            {running ? '运行中' : '未启动'}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-1 text-[11px] text-emerald-500 hover:underline disabled:opacity-50"
            title="导出为 .sotaagent.zip，可在其它环境导入"
          >
            {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
            {exporting ? '导出中…' : '导出'}
          </button>
          {/* Detail toggle buttons */}
          {hasDemand && (
            <button
              onClick={() => togglePanel('demand')}
              className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline"
            >
              <FileText className="h-3 w-3" />
              {showDemand ? '收起需求' : '查看需求描述'}
            </button>
          )}
          <button
            onClick={() => togglePanel('spec')}
            className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline"
          >
            <FileText className="h-3 w-3" />
            {showSpec ? '收起 MD' : '查看 MD 文档'}
          </button>
          <button
            onClick={() => togglePanel('code')}
            className="flex items-center gap-1 text-[11px] text-maia-accent hover:underline"
          >
            <FileCode className="h-3 w-3" />
            {showCode ? '收起代码' : '查看代码'}
          </button>
          <button
            onClick={handleEditInEditor}
            className="flex items-center gap-1 text-[11px] text-amber-500 hover:underline ml-1"
            title="带齐现有文档与代码，从审阅步进入编辑"
          >
            <Pencil className="h-3 w-3" />
            编辑
          </button>

          <div className="w-px h-5 bg-maia-border mx-1" />

          {/* Start/Stop/Restart buttons */}
          {!running ? (
            <Button size="sm" variant="default" onClick={start} disabled={loading}>
              {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
              启动
            </Button>
          ) : (
            <>
              <Button size="sm" variant="outline" onClick={stop} disabled={loading}>
                <Square className="h-3 w-3" />
                停止
              </Button>
              <Button size="sm" variant="outline" onClick={restart} disabled={loading}>
                <RefreshCw className="h-3 w-3" />
                重启
              </Button>
            </>
          )}
        </div>
      </div>

      {exportInfo && (
        <div className="flex items-center gap-2 px-4 py-1.5 text-[11px] border-b border-maia-border bg-emerald-500/10 shrink-0">
          <span className="text-emerald-600 shrink-0">
            已导出 {Math.max(1, Math.round(exportInfo.size / 1024))} KB：
          </span>
          <a
            className="text-maia-accent underline truncate"
            href={`/api/migrate/agent/bundles/${encodeURIComponent(exportInfo.name)}`}
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

      {/* Content area */}
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
                <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">
                  {specMd || '加载中...'}
                </pre>
              </CardBody>
            </Card>
          )}

          {/* Demand description panel */}
          {showDemand && demandMd && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-maia-text-secondary tracking-wide">用户需求描述</span>
                  <button onClick={() => setShowDemand(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                </div>
                <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">
                  {demandMd}
                </pre>
              </CardBody>
            </Card>
          )}

          {/* Code panel */}
          {showCode && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-maia-text-secondary tracking-wide">Agent 源代码</span>
                    {codeDirty && <Badge variant="warning">已修改</Badge>}
                    {saveMsg && (
                      <span className={`text-[10px] ${saveMsg.startsWith('保存失败') ? 'text-maia-danger' : 'text-maia-success'}`}>
                        {saveMsg}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => { if (isEditingCode) { setDraftCode(code); setIsEditingCode(false) } else { setDraftCode(code); setIsEditingCode(true) } }}
                      className="flex items-center gap-1 text-[10px] text-maia-text-muted hover:text-maia-accent px-1.5 py-0.5 rounded border border-maia-border"
                    >
                      <Pencil className="h-2.5 w-2.5" />{isEditingCode ? '取消' : '手工微调'}
                    </button>
                    <button
                      onClick={handleSaveCode}
                      disabled={!codeDirty || isSaving}
                      className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-maia-border text-maia-accent hover:bg-maia-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {isSaving ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Save className="h-2.5 w-2.5" />}
                      保存代码
                    </button>
                    <button onClick={() => setShowCode(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                  </div>
                </div>
                {isEditingCode ? (
                  <textarea
                    value={draftCode}
                    onChange={(e) => setDraftCode(e.target.value)}
                    onKeyDown={onCodeTabIndent}
                    spellCheck={false}
                    className="w-full max-h-[400px] min-h-[240px] text-[11px] font-mono leading-relaxed text-maia-text bg-maia-bg rounded p-3 border border-maia-border outline-none resize-y focus:border-maia-accent/40"
                  />
                ) : (
                  <pre className="text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[400px] overflow-auto bg-maia-bg rounded p-3">
                    {code || '加载中...'}
                  </pre>
                )}
              </CardBody>
            </Card>
          )}

          {/* 任务式 Agent：启动参数 + 启动任务按钮 */}
          {isTask && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="text-xs font-medium text-maia-text-secondary tracking-wide mb-2">任务启动参数</div>
                <div className="space-y-2">
                  {(taskInputNames.length ? taskInputNames : ['（无启动参数）']).map((name) => (
                    name.startsWith('（') ? (
                      <p key={name} className="text-[11px] text-maia-text-muted">{name}</p>
                    ) : (
                      <div key={name} className="flex items-center gap-2">
                        <label className="text-[12px] text-maia-text-muted w-24 shrink-0">{name}</label>
                        <input
                          value={taskParams[name] || ''}
                          onChange={(e) => setTaskParams((p) => ({ ...p, [name]: e.target.value }))}
                          className="flex-1 text-[12px] bg-maia-bg rounded border border-maia-border px-2 py-1 outline-none focus:border-maia-accent/40"
                          placeholder={`请输入 ${name}`}
                        />
                      </div>
                    )
                  ))}
                </div>
                <div className="mt-3">
                  <Button size="sm" variant="default" onClick={startTask} disabled={isSending || !running}>
                    {isSending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                    启动任务
                  </Button>
                  {!running && <span className="text-[11px] text-maia-text-muted ml-2">（请先点击顶部「启动」）</span>}
                </div>
              </CardBody>
            </Card>
          )}

          {/* Web 服务式 Agent：服务信息 + API 测试区 */}
          {isWebService && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-maia-text-secondary tracking-wide">服务状态</span>
                  <Badge variant={serviceInfo.running ? 'success' : 'default'}>
                    {serviceInfo.running
                      ? (serviceInfo.port ? `运行中 :${serviceInfo.port}` : '运行中（端口未上报）')
                      : '未运行'}
                  </Badge>
                </div>
                {(serviceInfo.apis?.length || 0) > 0 && (
                  <>
                    <div className="flex items-center justify-between mt-3 mb-2">
                      <span className="text-xs font-medium text-maia-text-secondary tracking-wide">API 测试</span>
                      {/* API 说明文档：浏览 / 下载 */}
                      <div className="flex items-center gap-2">
                        <button
                          onClick={async () => {
                            try {
                              const d = await apiGet<any>(`/api/agent/${agent!.id}/api-doc`)
                              // 在新窗口以 Markdown 原文展示（可复制/另存）
                              const w = window.open('', '_blank')
                              if (w) {
                                w.document.write(`<pre style="white-space:pre-wrap;font-family:ui-monospace,monospace;padding:16px;line-height:1.6">${
                                  (d.markdown || '').replace(/[&<>]/g, (c: string) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' } as Record<string, string>)[c])
                                }</pre>`)
                                w.document.title = `${agent!.id}-api.md`
                              }
                            } catch { /* ignore */ }
                          }}
                          className="text-[11px] text-maia-accent hover:underline"
                        >
                          查看 API 文档
                        </button>
                        <a
                          href={`/api/agent/${agent!.id}/api-doc/download`}
                          className="text-[11px] text-maia-accent hover:underline"
                          download
                        >
                          下载 .md
                        </a>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 mb-2">
                      <label className="text-[12px] text-maia-text-muted w-24 shrink-0">选择 API</label>
                      <select
                        value={testApi}
                        onChange={(e) => { setTestApi(e.target.value); setTestParams({}); setTestResult('') }}
                        className="flex-1 text-[12px] bg-maia-bg rounded border border-maia-border px-2 py-1 outline-none focus:border-maia-accent/40"
                      >
                        {serviceInfo.apis!.map((a) => (
                          <option key={a.name} value={a.name}>{a.method || 'POST'} {a.path} — {a.name}</option>
                        ))}
                      </select>
                    </div>
                    {/* 动态参数表单：文件路径型参数提供「选择工作区文件」按钮 */}
                    {(serviceInfo.apis!.find((a) => a.name === testApi)?.params || []).map((p) => {
                      const isPathParam = /file|path|image|dir|img|图片|路径|文件/i.test(
                        `${p.name} ${p.desc || ''}`
                      )
                      return (
                        <div key={p.name} className="flex items-center gap-2 mb-2">
                          <label className="text-[12px] text-maia-text-muted w-24 shrink-0">
                            {p.name}{p.required ? ' *' : ''}
                          </label>
                          <input
                            value={testParams[p.name] || ''}
                            onChange={(e) => setTestParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                            className="flex-1 text-[12px] bg-maia-bg rounded border border-maia-border px-2 py-1 outline-none focus:border-maia-accent/40"
                            placeholder={p.desc || `请输入 ${p.name}`}
                          />
                          {isPathParam && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setPickForParam(p.name)}
                              className="shrink-0 h-7 px-2 text-[11px]"
                              title="从工作区选择文件"
                            >
                              <FolderOpen className="h-3 w-3" />
                              选择文件
                            </Button>
                          )}
                        </div>
                      )
                    })}
                    <div className="mt-2">
                      <Button size="sm" variant="default" onClick={runApiTest} disabled={testing || !serviceInfo.running || !serviceInfo.port}>
                        {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
                        发送测试请求
                      </Button>
                      {!serviceInfo.running ? (
                        <span className="text-[11px] text-maia-text-muted ml-2">（请先点击顶部「启动」）</span>
                      ) : !serviceInfo.port ? (
                        <span className="text-[11px] text-amber-500 ml-2">（服务端口未上报，请点「重启」后重试）</span>
                      ) : null}
                    </div>
                    {testResult && (
                      <pre className="mt-3 text-[11px] font-mono leading-relaxed text-maia-text whitespace-pre-wrap max-h-[300px] overflow-auto bg-maia-bg rounded p-3 border border-maia-border">
                        {testResult}
                      </pre>
                    )}
                  </>
                )}
              </CardBody>
            </Card>
          )}

          {/* Messages area */}
          {messages.length === 0 && !streaming && (
            <div className="flex flex-col items-center justify-center py-12 text-maia-text-muted gap-2">
              <Bot className="h-8 w-8 opacity-30" />
              <p className="text-sm">
                {!running
                  ? '点击"启动"按钮启动 Agent'
                  : isWebService
                    ? '服务已启动，可在上方 API 测试区发起测试'
                    : isTask
                      ? 'Agent 已启动，填写启动参数后点击「启动任务」'
                      : 'Agent 已启动，输入消息开始交互'}
              </p>
            </div>
          )}

          <div className="space-y-3">
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`flex gap-2 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
              >
                <div
                  className={`flex-shrink-0 h-6 w-6 rounded-full flex items-center justify-center text-[10px] ${
                    msg.role === 'user'
                      ? 'bg-maia-accent text-white'
                      : msg.role === 'system'
                        ? 'bg-amber-100 text-amber-600'
                        : 'bg-purple-100 text-purple-600'
                  }`}
                >
                  {msg.role === 'user' ? 'U' : msg.role === 'system' ? 'S' : 'A'}
                </div>
                <div className="flex-1 min-w-0 flex flex-col gap-2">
                  {msg.content && (
                    <div className="text-[13px] leading-relaxed tracking-wide text-maia-text whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  )}
                  {/* 结构化卡片：表格 / 图片 / 文件等完整展示（复用 InlineCard） */}
                  {msg.cards && msg.cards.length > 0 && (
                    <div className="flex flex-col gap-2">
                      {msg.cards.map((card) => (
                        <InlineCard key={card.id} card={card} />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}

            {streaming && (
              <div className="flex gap-2">
                <div className="flex-shrink-0 h-6 w-6 rounded-full bg-purple-100 text-purple-600 flex items-center justify-center text-[10px]">
                  A
                </div>
                <div className="text-[13px] leading-relaxed tracking-wide text-maia-text whitespace-pre-wrap">
                  {streaming}
                  <span className="inline-block w-1.5 h-4 bg-maia-accent ml-0.5 animate-pulse" />
                </div>
              </div>
            )}

            {/* 流式过程中已到达的卡片（实时展示） */}
            {streamCards.length > 0 && (
              <div className="flex gap-2">
                <div className="flex-shrink-0 h-6 w-6 rounded-full bg-purple-100 text-purple-600 flex items-center justify-center text-[10px]">
                  A
                </div>
                <div className="flex-1 min-w-0 flex flex-col gap-2">
                  {streamCards.map((card) => (
                    <InlineCard key={card.id} card={card} />
                  ))}
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>
      </div>

      {/* Input — 仅交互式 Agent 显示对话输入框 */}
      {running && !isTask && !isWebService && (
        <div className="border-t border-maia-border px-4 py-3 shrink-0">
          <div className="flex items-end gap-2 max-w-2xl mx-auto bg-maia-bg rounded-lg border border-maia-border p-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  sendMessage()
                }
              }}
              placeholder={`向 ${agent.name} 发送消息...`}
              rows={1}
              className="flex-1 bg-transparent text-[13px] tracking-wide outline-none resize-none placeholder:text-maia-text-muted"
              disabled={isSending}
            />
            <Button
              size="icon"
              onClick={sendMessage}
              disabled={isSending || !input.trim()}
              className="shrink-0 h-8 w-8 rounded-lg"
            >
              {isSending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      )}

      {/* 工作区文件选择器（用于文件路径型参数） */}
      {pickForParam && (
        <WorkspaceFilePicker
          onSelect={(path) => setTestParams((prev) => ({ ...prev, [pickForParam]: path }))}
          onClose={() => setPickForParam(null)}
        />
      )}
    </div>
  )
}

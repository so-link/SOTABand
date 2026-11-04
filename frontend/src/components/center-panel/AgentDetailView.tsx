import { useState, useEffect, useRef } from 'react'
import {
  Bot, Send, Play, Square, RefreshCw, Loader2, FileText, FileCode, X, Pencil, Save, Eraser, Database,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import { useAuthStore } from '@/stores/auth-store'
import { useAgentEditorStore } from '@/stores/agent-editor-store'
import { agentApi } from '@/services/api/agent'
import { cn } from '@/lib/utils'
import { useTabIndent } from '@/hooks/use-tab-indent'
import type { AgentResource } from '@/types/resources'
import type { InlineCard as InlineCardType } from '@/types/chat'
import { InlineCard } from './InlineCard'
import { FileSelect } from '@/components/ui/file-select'
import { DataFilePicker } from '@/components/ui/data-file-picker'

const BASE_URL = ''

// 工作流程步骤（后端 step 事件）；startedAt/durationMs 用于记录每步耗时
type Step = {
  id: string
  text: string
  status: 'running' | 'done' | 'error'
  startedAt?: number
  durationMs?: number
}

// 一条对话消息；steps 为该轮执行的工作流程步骤（跟在该轮用户输入后面）
type ChatMessage = { role: string; content: string; cards?: InlineCardType[]; steps?: Step[] }

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
  // 对话消息：后端 SQLite 为唯一状态源（chat_store，会话 ID agent-chat-{agentId}）。
  // 此前只存 localStorage，换浏览器/清缓存即丢；现在挂载时从后端拉取。
  // 后端历史为空但本地存有旧数据时（升级前的用户），先迁移到后端再拉取。
  const [messages, setMessages] = useState<ChatMessage[]>([])
  useEffect(() => {
    if (!agentId) return
    let cancelled = false
    const legacyKey = `sotaband-agent-messages-${agentId}`
    const apply = (list: unknown[]) => {
      if (cancelled) return
      setMessages((list as Array<{ role: string; content: string; cards?: InlineCardType[] }>).map((m) => ({
        role: m.role,
        content: m.content,
        cards: Array.isArray(m.cards) && m.cards.length > 0 ? m.cards : undefined,
      })))
    }
    const load = (url: string, init?: RequestInit) =>
      fetch(url, init).then((r) => (r.ok ? r.json() : { messages: [] })).then((d) => d.messages || [])
    load(`${BASE_URL}/api/agent/${agentId}/chat-history`)
      .then(async (msgs) => {
        if (msgs.length === 0) {
          let legacy: unknown[] = []
          try { legacy = JSON.parse(localStorage.getItem(legacyKey) || '[]') } catch { /* ignore */ }
          if (Array.isArray(legacy) && legacy.length > 0) {
            const r = await fetch(`${BASE_URL}/api/agent/${agentId}/chat-history/migrate`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ messages: legacy }),
            }).catch(() => null)
            const d = r?.ok ? await r.json().catch(() => ({ migrated: 0 })) : { migrated: 0 }
            if ((d.migrated ?? 0) > 0) {
              localStorage.removeItem(legacyKey)
              return load(`${BASE_URL}/api/agent/${agentId}/chat-history`)
            }
          }
        }
        return msgs
      })
      .then(apply)
      .catch(() => { /* 后端不可用时显示空历史 */ })
    return () => { cancelled = true }
  }, [agentId])
  const [streaming, setStreaming] = useState('')
  const [streamCards, setStreamCards] = useState<InlineCardType[]>([])
  const [isSending, setIsSending] = useState(false)
  const [loading, setLoading] = useState(false)
  // Agent 工作流程步骤（流式中的实时步骤；done 后存入该轮 assistant 消息）
  const [steps, setSteps] = useState<Step[]>([])

  // 任务式 Agent：启动参数（由 spec.inputs 渲染）
  const [taskParams, setTaskParams] = useState<Record<string, string>>({})

  // Web 服务式 Agent：服务信息与 API 测试
  const [serviceInfo, setServiceInfo] = useState<{ running: boolean; host?: string; port?: number; apis?: Array<{ name: string; method?: string; path?: string; params?: Array<{ name: string; type?: string; required?: boolean; desc?: string }> }> }>({ running: false })
  const [testApi, setTestApi] = useState('')
  const [testParams, setTestParams] = useState<Record<string, string>>({})
  const [dataPickerFor, setDataPickerFor] = useState<string | null>(null)
  const [testResult, setTestResult] = useState('')
  const [testing, setTesting] = useState(false)

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

  // 覆盖层（fork）状态：内置 Agent 要修改先复制为私有副本；null=非内置/未知
  const [forkStatus, setForkStatus] = useState<{
    is_builtin: boolean; has_copy: boolean; forked: boolean; upstream_changed: boolean
  } | null>(null)
  const [detailSource, setDetailSource] = useState<string | null>(null)
  const [forkBusy, setForkBusy] = useState(false)
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  // 超管发布的公共 Agent：全体可用，但只有超管能改（后端 ensure_can_touch_builtin 兜底）
  const isSharedAgent = detailSource === 'shared'

  const setActiveView = useUIStore((s) => s.setActiveView)

  const bottomRef = useRef<HTMLDivElement>(null)
  // 中间面板滚动容器与展开面板的滚动位置记忆
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const panelScrollRef = useRef<number>(0)
  const specPanelRef = useRef<HTMLDivElement>(null)
  const demandPanelRef = useRef<HTMLDivElement>(null)
  const codePanelRef = useRef<HTMLDivElement>(null)

  // Fetch agent details on mount / agent change
  useEffect(() => {
    if (!agent) return
    setForkStatus(null)
    setDetailSource(null)
    fetch(`${BASE_URL}/api/agent/${agent.id}`)
      .then((r) => r.json())
      .then((data) => {
        setSpecMd(data.spec_md || '')
        setCode(data.code || '')
        setDetailSource(data.source || null)
        if (data.has_demand) {
          setHasDemand(true)
          setDemandMd(data.demand_md || '')
        }
        // fork 状态决定徽章与编辑入口行为：非内置直接返回 null 不打扰
        agentApi.getForkStatus(agent.id)
          .then((fs) => setForkStatus(fs.is_builtin ? fs : null))
          .catch(() => setForkStatus(null))
      })
      .catch(() => {})
  }, [agent])

  // 新消息、流式文本、工作流程步骤输出时，都自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming, steps])

  // Web 服务式 Agent：加载服务信息（未启动时也拉取 API 声明用于展示）
  useEffect(() => {
    if (agent && isWebService) { loadServiceInfo() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.id, isWebService])

  // 读取 Agent 运行状态
  useEffect(() => {
    if (!agent) return
    fetch(`${BASE_URL}/api/agent/${agent.id}/status`)
      .then((r) => r.json())
      .then((d) => setRunning(!!d.running))
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent?.id])

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
      const res = await fetch(`${BASE_URL}/api/agent/${agent.id}/service`)
      if (res.ok) {
        const data = await res.json()
        setServiceInfo(data)
        // 默认选中第一个 API
        const apis = data.apis || []
        if (apis.length && !apis.some((a: { name: string }) => a.name === testApi)) {
          setTestApi(apis[0].name)
        }
      }
    } catch { /* ignore */ }
  }

  // 发起 API 测试（走后端代理）
  const runApiTest = async () => {
    if (!agent || !testApi || testing) return
    setTesting(true); setTestResult('')
    try {
      const res = await fetch(`${BASE_URL}/api/agent/${agent.id}/test-api`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_name: testApi, params: testParams }),
      })
      const data = await res.json()
      setTestResult(JSON.stringify(data, null, 2))
    } catch (e) {
      setTestResult(`请求失败: ${String(e)}`)
    } finally {
      setTesting(false)
    }
  }

  const togglePanel = (panel: 'spec' | 'code' | 'demand') => {
    const container = scrollContainerRef.current
    // 切换前记录当前滚动位置：展开后滚到面板，收起后恢复到此位置
    if (container) panelScrollRef.current = container.scrollTop

    const isOpening = panel === 'spec' ? !showSpec : panel === 'code' ? !showCode : !showDemand

    if (panel === 'spec') { setShowSpec(!showSpec); setShowCode(false); setShowDemand(false) }
    else if (panel === 'code') {
      setShowCode(!showCode); setShowSpec(false); setShowDemand(false)
      // 打开代码面板时重置草稿，避免沿用上一个 Agent 的内容
      if (!showCode) { setDraftCode(code); setIsEditingCode(false); setSaveMsg('') }
    }
    else { setShowDemand(!showDemand); setShowSpec(false); setShowCode(false) }

    // 等待 DOM 更新后再滚动（两层 rAF 确保面板已挂载/卸载）
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const el = scrollContainerRef.current
      if (!el) return
      if (isOpening) {
        const panelEl = panel === 'spec' ? specPanelRef.current
          : panel === 'demand' ? demandPanelRef.current : codePanelRef.current
        if (panelEl) {
          // 用 getBoundingClientRect 计算面板相对滚动容器的偏移，避免 offsetParent 不一致
          const panelRect = panelEl.getBoundingClientRect()
          const containerRect = el.getBoundingClientRect()
          el.scrollTo({ top: el.scrollTop + (panelRect.top - containerRect.top) - 16, behavior: 'smooth' })
        }
      } else {
        // 收起后内容变短，clamp 到可滚动范围
        const maxTop = el.scrollHeight - el.clientHeight
        el.scrollTo({ top: Math.min(panelScrollRef.current, Math.max(0, maxTop)), behavior: 'smooth' })
      }
    }))
  }

  const codeDirty = draftCode.trim() !== code.trim()

  // 任务式 Agent：从 MD 规范文档的「## 输入」段落解析启动参数名
  // 支持 "## 输入" / "## 参数" 段落下的 "- name: ..." 列表
  const taskInputNames: string[] = (() => {
    if (!isTask || !specMd) return []
    const m = specMd.match(/^##+\s*(输入|参数|启动参数)\s*$([\s\S]*?)(^##+\s|\Z)/m)
    if (!m) return []
    const body = m[2] || ''
    const names: string[] = []
    const re = /^\s*[-*]\s*([A-Za-z_][\w-]*)\s*[:：]/gm
    let mm: RegExpExecArray | null
    while ((mm = re.exec(body)) !== null) names.push(mm[1])
    return names
  })()

  /** 内置资源不可直接写：保存前先确保有我的副本（就地复制到私有），返回可否继续 */
  const ensurePrivateCopy = async (): Promise<boolean> => {
    if (!agent) return false
    if (isSharedAgent && !isAdmin) {
      window.alert(`「${agent.name}」是超级用户发布的公共 Agent，不可直接修改。`)
      return false
    }
    if (!(forkStatus?.is_builtin && !forkStatus.has_copy)) return true
    const ok = window.confirm(
      `「${agent.name}」是内置 Agent（随系统分发，不可直接修改）。\n\n` +
      '点击「确定」：复制为我的副本后保存\n' +
      '点击「取消」：不保存'
    )
    if (!ok) return false
    try {
      await agentApi.fork(agent.id)
      setForkStatus({ ...(forkStatus ?? { is_builtin: true, forked: false, upstream_changed: false }), has_copy: true, forked: true })
      return true
    } catch (e) {
      window.alert(`复制副本失败：${String(e)}`)
      return false
    }
  }

  const handleSaveCode = async () => {
    if (!agent || !codeDirty) return
    if (!(await ensurePrivateCopy())) return
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

  const handleEditInEditor = async () => {
    if (!agent) return
    if (isSharedAgent && !isAdmin) {
      window.alert(`「${agent.name}」是超级用户发布的公共 Agent，不可直接修改。`)
      return
    }
    // 内置 Agent 不可直接修改（改了就是改 git 里的系统文件）：
    // 先复制为我的副本，编辑落私有，内置不受影响（方案 C）
    if (forkStatus?.is_builtin && !forkStatus.has_copy) {
      const ok = window.confirm(
        `「${agent.name}」是内置 Agent（随系统分发，不可直接修改）。\n\n` +
        '点击「确定」：复制为我的副本后进入编辑\n' +
        '点击「取消」：留在详情页（不复制）'
      )
      if (!ok) return
      setForkBusy(true)
      try {
        await agentApi.fork(agent.id)
        setForkStatus({ ...forkStatus, has_copy: true, forked: true })
      } catch (e) {
        window.alert(`复制副本失败：${String(e)}`)
        setForkBusy(false)
        return
      }
      setForkBusy(false)
    }
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

  /** 丢弃副本：删除私有文件与条目，回到内置版本 */
  const handleDiscardFork = async () => {
    if (!agent) return
    if (!window.confirm(
      `确定丢弃「${agent.name}」的我的副本吗？\n\n副本上的所有修改将丢失，\n恢复为直接使用内置版本。`)) return
    setForkBusy(true)
    try {
      await agentApi.discardFork(agent.id)
      setForkStatus({ is_builtin: true, has_copy: false, forked: false, upstream_changed: false })
      const data = await (await fetch(`${BASE_URL}/api/agent/${agent.id}`)).json()
      setSpecMd(data.spec_md || '')
      setCode(data.code || '')
      setDraftCode(data.code || '')
    } catch (e) {
      window.alert(`丢弃副本失败：${String(e)}`)
    }
    setForkBusy(false)
  }

  /** 上游有更新：按当前内置重新复制（副本改动丢弃） */
  const handleRefork = async () => {
    if (!agent) return
    if (!window.confirm(
      '内置版本已有更新。重新复制将以最新内置内容覆盖我的副本，\n副本上的修改会丢失。确定重新复制吗？')) return
    setForkBusy(true)
    try {
      await agentApi.fork(agent.id)
      setForkStatus({ is_builtin: true, has_copy: true, forked: true, upstream_changed: false })
    } catch (e) {
      window.alert(`复制失败：${String(e)}`)
    }
    setForkBusy(false)
  }

  // 来源徽章（内置/我的副本/我的 Agent）——span 而非 Badge：需要 title 悬浮解释
  const forkBadge = (() => {
    if (forkStatus?.is_builtin) {
      return forkStatus.has_copy
        ? { label: '我的副本', cls: 'bg-maia-warning-bg text-maia-warning border-maia-warning/20', title: '已复制为私有副本：编辑与运行都用副本，内置版本不受影响' }
        : { label: '内置', cls: 'bg-maia-sidebar-active text-maia-text-secondary border-maia-border', title: '内置 Agent（随系统分发）：要修改请先复制为我的副本' }
    }
    if (detailSource === 'shared') {
      return { label: '公共', cls: 'bg-maia-accent/10 text-maia-accent border-maia-accent-border', title: '超级用户发布：全体用户可用（仅超级用户可修改）' }
    }
    if (detailSource === 'private' || forkStatus?.has_copy) {
      return { label: '我的 Agent', cls: 'bg-maia-success-bg text-maia-success border-maia-success/20', title: '我创建的 Agent（私有目录）' }
    }
    return null
  })()

  const start = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/start`, { method: 'POST' })
      setRunning(true)
      setStreaming('')
      setStreamCards([])
      if (isWebService) await loadServiceInfo()
    } catch { /* ignore */ }
    setLoading(false)
  }

  const stop = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/stop`, { method: 'POST' })
      setRunning(false)
      if (isWebService) setServiceInfo((s) => ({ ...s, running: false, port: undefined }))
    } catch { /* ignore */ }
    setLoading(false)
  }

  const restart = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/restart`, { method: 'POST' })
      setRunning(true)
      setStreaming('')
      setStreamCards([])
    } catch { /* ignore */ }
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
    setSteps([])

    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 120_000)
    let cardSeq = 0
    const localCards: InlineCardType[] = []
    // 本轮执行的工作流程步骤（实时显示用全局 steps，done 后存入消息）
    const localSteps: Step[] = []

    try {
      const res = await fetch(`${BASE_URL}/api/agent/${agent.id}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // session_id：后端据此把本轮对话落入 SQLite（Agent 空间历史）
        body: JSON.stringify({ ...params, session_id: `agent-chat-${agent.id}` }),
        signal: controller.signal,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

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
              if (eventType === 'content' || eventType === 'delta' || !eventType) {
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
                setMessages((m) => [...m, { role: 'agent', content: full, cards: [...localCards], steps: [...localSteps] }])
                setStreaming('')
                setStreamCards([])
                setSteps([])
                streamDone = true
              } else if (eventType === 'step') {
                // 工作流程步骤：按 id 合并状态（running→done/error），实时显示；
                // running 时记录开始时间，done/error 时计算该步耗时
                const status: Step['status'] = payload.status || 'running'
                const now = Date.now()
                const idx = localSteps.findIndex((s) => s.id === payload.id)
                if (idx >= 0) {
                  const prev = localSteps[idx]
                  const item: Step = { ...prev, text: payload.text, status }
                  if (status === 'running' && item.startedAt === undefined) item.startedAt = now
                  if (status !== 'running' && prev.startedAt !== undefined) item.durationMs = now - prev.startedAt
                  localSteps[idx] = item
                } else {
                  localSteps.push({
                    id: payload.id,
                    text: payload.text,
                    status,
                    startedAt: status === 'running' ? now : undefined,
                    durationMs: status === 'running' ? undefined : 0,
                  })
                }
                setSteps([...localSteps])
              } else if (eventType === 'error') {
                setMessages((m) => [...m, { role: 'system', content: `[WARN] ${payload.message || '执行错误'}` }])
                streamDone = true
              }
              eventType = ''
            } catch { /* skip */ }
          }
        }
      }

      if (!streamDone && (full || localCards.length)) {
        setMessages((m) => [...m, { role: 'agent', content: full, cards: [...localCards], steps: [...localSteps] }])
        setStreaming('')
        setStreamCards([])
        setSteps([])
      }
    } catch (e: unknown) {
      if ((e as Error).name === 'AbortError') {
        setMessages((m) => [...m, { role: 'system', content: '[WARN] 请求超时，Agent 未在 2 分钟内响应' }])
      } else {
        setMessages((m) => [...m, { role: 'system', content: '[WARN] 请求失败' }])
      }
      setStreaming('')
    } finally {
      clearTimeout(timeout)
      setIsSending(false)
      // 刷新任务历史与最近使用（后端在流结束时已写入 SQLite）
      try {
        const { useResourceStore } = await import('@/stores/resource-store')
        useResourceStore.getState().fetchTasksFromApi()
      } catch { /* ignore */ }
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
      setMessages((m) => [...m, { role: 'system', content: '[WARN] 请先点击「启动」按钮启动 Agent 进程' }])
      return
    }
    setMessages([])
    // 显示清空必须同步删后端历史（新一轮任务从干净历史开始），
    // 否则刷新后旧历史又会冒出来，与显示分叉。
    fetch(`${BASE_URL}/api/agent/${agent.id}/chat-history`, { method: 'DELETE' }).catch(() => {})
    await runExecute({ ...taskParams })
  }

  // 渲染一轮的工作流程步骤条（用在每轮对话的用户输入之后）
  const renderSteps = (list: Step[]) => (
    <div className="rounded-lg border border-maia-border bg-maia-bg/50 px-3 py-2 space-y-1.5">
      <div className="text-[11px] uppercase tracking-wide text-maia-text-muted">工作流程</div>
      {list.map((s) => (
        <div key={s.id} className="flex items-center gap-2 text-[12px]">
          <span className={cn(
            'inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px]',
            s.status === 'done' && 'bg-maia-success/20 text-maia-success',
            s.status === 'error' && 'bg-maia-danger/20 text-maia-danger',
            s.status === 'running' && 'bg-maia-accent/20 text-maia-accent animate-pulse',
          )}>
            {s.status === 'done' ? '✓' : s.status === 'error' ? '✕' : '•'}
          </span>
          <span className={cn(
            s.status === 'done' ? 'text-maia-text-muted' : 'text-maia-text',
          )}>{s.text}</span>
          {s.durationMs !== undefined && (
            <span className="text-[11px] text-maia-text-muted tabular-nums">
              {s.durationMs < 1000 ? `${s.durationMs}ms` : `${(s.durationMs / 1000).toFixed(1)}s`}
            </span>
          )}
        </div>
      ))}
    </div>
  )

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header：第一行是身份与启停，第二行是文档/代码工具条，避免标题与操作挤在一行换行 */}
      <div className="shrink-0 border-b border-maia-border bg-maia-bg/40">
        <div className="flex items-center gap-2 px-4 h-11 min-w-0">
          <Bot className="h-4 w-4 text-maia-accent shrink-0" />
          <span className="text-[14px] font-semibold text-maia-text-heading truncate min-w-0">
            {agent.name}
          </span>
          <Badge variant={running ? 'success' : 'default'} className="shrink-0">
            {running ? '运行中' : '未启动'}
          </Badge>
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
                onClick={handleDiscardFork}
                disabled={forkBusy}
                title="删除我的副本，恢复为直接使用内置版本（副本上的修改会丢失）"
                className="inline-flex items-center h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-maia-border text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text transition-colors shrink-0 disabled:opacity-40"
              >
                丢弃副本
              </button>
            )}
            {forkStatus?.upstream_changed && (
              <button
                onClick={handleRefork}
                disabled={forkBusy}
                title="内置版本已被更新（例如系统升级）。重新复制会以最新内置内容覆盖我的副本"
                className="inline-flex items-center h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-maia-warning/30 bg-maia-warning-bg text-maia-warning hover:opacity-90 transition-colors shrink-0 disabled:opacity-40"
              >
                内置已更新，重新复制
              </button>
            )}
            {!running ? (
              <Button size="sm" variant="default" onClick={start} disabled={loading}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                启动
              </Button>
            ) : (
              <>
                <Button size="sm" variant="outline" onClick={stop} disabled={loading}>
                  <Square className="h-3.5 w-3.5" />
                  停止
                </Button>
                <Button size="sm" variant="outline" onClick={restart} disabled={loading}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  重启
                </Button>
              </>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1 px-3 h-9 border-t border-maia-border/60 overflow-x-auto">
          {hasDemand && (
            <button
              onClick={() => togglePanel('demand')}
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
            onClick={() => togglePanel('spec')}
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
          <button
            onClick={() => togglePanel('code')}
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
              if (messages.length > 0 && confirm('确定清空当前对话历史？此操作不可恢复。')) {
                setMessages([])
                fetch(`${BASE_URL}/api/agent/${agentId}/chat-history`, { method: 'DELETE' }).catch(() => {})
              }
            }}
            disabled={messages.length === 0}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-transparent text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-danger transition-colors shrink-0 ml-auto disabled:opacity-40 disabled:pointer-events-none"
            title="清空当前 Agent 的对话历史"
          >
            <Eraser className="h-3.5 w-3.5" />
            清空对话
          </button>
          <button
            onClick={() => void handleEditInEditor()}
            disabled={forkBusy}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[12px] whitespace-nowrap border border-transparent text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text transition-colors shrink-0 disabled:opacity-40"
            title={forkStatus?.is_builtin && !forkStatus.has_copy
              ? '内置 Agent：先复制为我的副本再编辑'
              : '带齐现有文档与代码，从审阅步进入编辑'}
          >
            {forkBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pencil className="h-3.5 w-3.5" />}
            {forkBusy ? '复制中…' : '编辑'}
          </button>
        </div>
      </div>

      {/* Content area */}
      <div ref={scrollContainerRef} className="flex-1 min-h-0 overflow-auto p-4">
        <div className="max-w-2xl mx-auto space-y-4">

          {/* MD Spec panel */}
          {showSpec && (
            <div ref={specPanelRef}>
              <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[13px] font-medium text-maia-text-secondary">MD 规范文档</span>
                  <button onClick={() => setShowSpec(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                </div>
                <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3 border border-maia-border">
                  {specMd || '加载中...'}
                </pre>
              </CardBody>
            </Card>
            </div>
          )}

          {/* Demand description panel */}
          {showDemand && demandMd && (
            <div ref={demandPanelRef}>
              <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[13px] font-medium text-maia-text-secondary">用户需求描述</span>
                  <button onClick={() => setShowDemand(false)}><X className="h-3 w-3 text-maia-text-muted" /></button>
                </div>
                <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3 border border-maia-border">
                  {demandMd}
                </pre>
              </CardBody>
            </Card>
            </div>
          )}

          {/* Code panel */}
          {showCode && (
            <div ref={codePanelRef}>
              <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-maia-text-secondary">Agent 源代码</span>
                    {codeDirty && <Badge variant="warning">已修改</Badge>}
                    {saveMsg && (
                      <span className={`text-[12px] ${saveMsg.startsWith('保存失败') ? 'text-maia-danger' : 'text-maia-success'}`}>
                        {saveMsg}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => { if (isEditingCode) { setDraftCode(code); setIsEditingCode(false) } else { setDraftCode(code); setIsEditingCode(true) } }}
                      className="flex items-center gap-1 text-[12px] text-maia-text-muted hover:text-maia-accent px-1.5 py-0.5 rounded border border-maia-border"
                    >
                      <Pencil className="h-2.5 w-2.5" />{isEditingCode ? '取消' : '手工微调'}
                    </button>
                    <button
                      onClick={handleSaveCode}
                      disabled={!codeDirty || isSaving}
                      className="flex items-center gap-1 text-[12px] px-1.5 py-0.5 rounded border border-maia-border text-maia-accent hover:bg-maia-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
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
                    className="w-full max-h-[400px] min-h-[240px] text-[13px] font-mono leading-[22px] text-maia-text bg-maia-bg rounded p-3 border border-maia-border outline-none resize-y focus:border-maia-accent/40"
                  />
                ) : (
                  <pre className="text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3 border border-maia-border">
                    {code || '加载中...'}
                  </pre>
                )}
              </CardBody>
            </Card>
            </div>
          )}

          {/* 任务式 Agent：启动参数 + 启动任务按钮 */}
          {isTask && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="text-[13px] font-medium text-maia-text-secondary mb-2">任务启动参数</div>
                <div className="space-y-2">
                  {(taskInputNames.length ? taskInputNames : ['（无启动参数）']).map((name) => (
                    name.startsWith('（') ? (
                      <p key={name} className="text-[12px] text-maia-text-muted">{name}</p>
                    ) : (
                      <div key={name} className="flex items-center gap-2">
                        <label className="text-[13px] text-maia-text-muted w-24 shrink-0">{name}</label>
                        <input
                          value={taskParams[name] || ''}
                          onChange={(e) => setTaskParams((p) => ({ ...p, [name]: e.target.value }))}
                          className="flex-1 text-[13px] bg-maia-bg rounded border border-maia-border px-2 py-1 outline-none focus:border-maia-accent/40"
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
                  {!running && <span className="text-[12px] text-maia-text-muted ml-2">（请先点击顶部「启动」）</span>}
                </div>
              </CardBody>
            </Card>
          )}

          {/* Web 服务式 Agent：服务信息 + API 测试区 */}
          {isWebService && (
            <Card className="border-maia-border">
              <CardBody>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[13px] font-medium text-maia-text-secondary">服务状态</span>
                  <Badge variant={serviceInfo.running ? 'success' : 'default'}>
                    {serviceInfo.running ? `运行中 :${serviceInfo.port}` : '未运行'}
                  </Badge>
                </div>
                {(serviceInfo.apis?.length || 0) > 0 && (
                  <>
                    <div className="flex items-center justify-between mt-3 mb-2">
                      <span className="text-[13px] font-medium text-maia-text-secondary">API 测试</span>
                      {/* API 说明文档：浏览 / 下载 */}
                      <div className="flex items-center gap-2">
                        <button
                          onClick={async () => {
                            try {
                              const r = await fetch(`${BASE_URL}/api/agent/${agent!.id}/api-doc`)
                              const d = await r.json()
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
                          className="text-[12px] text-maia-accent hover:underline"
                        >
                          查看 API 文档
                        </button>
                        <a
                          href={`${BASE_URL}/api/agent/${agent!.id}/api-doc/download`}
                          className="text-[12px] text-maia-accent hover:underline"
                          download
                        >
                          下载 .md
                        </a>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 mb-2">
                      <label className="text-[13px] text-maia-text-muted w-24 shrink-0">选择 API</label>
                      <select
                        value={testApi}
                        onChange={(e) => { setTestApi(e.target.value); setTestParams({}); setTestResult('') }}
                        className="flex-1 text-[13px] bg-maia-bg rounded border border-maia-border px-2 py-1 outline-none focus:border-maia-accent/40"
                      >
                        {serviceInfo.apis!.map((a) => (
                          <option key={a.name} value={a.name}>{a.method || 'POST'} {a.path} — {a.name}</option>
                        ))}
                      </select>
                    </div>
                    {/* 动态参数表单：文件型参数用「我的文件」下拉选择（file_id） */}
                    {(serviceInfo.apis!.find((a) => a.name === testApi)?.params || []).map((p) => {
                      const isPathParam = /file|path|image|dir|img|图片|路径|文件/i.test(
                        `${p.name} ${p.desc || ''}`
                      )
                      return (
                        <div key={p.name} className="flex items-center gap-2 mb-2">
                          <label className="text-[13px] text-maia-text-muted w-24 shrink-0">
                            {p.name}{p.required ? ' *' : ''}
                          </label>
                          {isPathParam ? (
                            <div className="flex items-center gap-1.5">
                              <FileSelect
                                value={testParams[p.name] || ''}
                                onChange={(v) => setTestParams((prev) => ({ ...prev, [p.name]: v }))}
                                placeholder={p.desc || '选择文件'}
                                className="flex-1 text-[13px] bg-maia-bg rounded border border-maia-border px-2 py-1 outline-none focus:border-maia-accent/40"
                              />
                              <button
                                type="button"
                                onClick={() => setDataPickerFor(p.name)}
                                title="从数据空间选择数据集文件（data_path）"
                                className="shrink-0 h-7 px-2 rounded border border-maia-border hover:bg-maia-sidebar-hover text-maia-text-secondary text-[12px]"
                              >
                                <Database className="h-3 w-3" />
                                数据空间
                              </button>
                            </div>
                          ) : (
                            <input
                              value={testParams[p.name] || ''}
                              onChange={(e) => setTestParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                              className="flex-1 text-[13px] bg-maia-bg rounded border border-maia-border px-2 py-1 outline-none focus:border-maia-accent/40"
                              placeholder={p.desc || `请输入 ${p.name}`}
                            />
                          )}
                        </div>
                      )
                    })}
                    <div className="mt-2">
                      <Button size="sm" variant="default" onClick={runApiTest} disabled={testing || !serviceInfo.running}>
                        {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
                        发送测试请求
                      </Button>
                      {!serviceInfo.running && <span className="text-[12px] text-maia-text-muted ml-2">（请先点击顶部「启动」）</span>}
                    </div>
                    {testResult && (
                      <pre className="mt-3 text-[13px] font-mono leading-[22px] text-maia-text whitespace-pre-wrap bg-maia-bg rounded p-3 border border-maia-border">
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
            <div className="flex flex-col items-center justify-center py-14 text-center">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-maia-border bg-maia-sidebar text-maia-text-muted">
                <Bot className="h-5 w-5" />
              </span>
              <p className="text-[13px] leading-[20px] text-maia-text mt-3">
                {!running
                  ? 'Agent 尚未启动'
                  : isWebService
                    ? '服务已启动，可在上方 API 测试区发起测试'
                    : isTask
                      ? 'Agent 已启动，填写启动参数后点击「启动任务」'
                      : 'Agent 已启动，输入消息开始交互'}
              </p>
              {!running && (
                <p className="text-[12px] leading-[18px] text-maia-text-muted mt-1">
                  点击右上角「启动」运行 Agent 后即可交互
                </p>
              )}
            </div>
          )}

          <div className="space-y-3">
            {messages.map((msg, i) => {
              if (msg.role === 'user') {
                return (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[90%] rounded-xl rounded-br-sm bg-maia-chat-user border border-maia-accent-border px-3.5 py-2 text-[14px] leading-[22px] text-maia-text whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  </div>
                )
              }
              if (msg.role === 'system') {
                return (
                  <div key={i} className="text-center text-[12px] leading-[18px] text-maia-text-muted py-1">
                    {msg.content}
                  </div>
                )
              }
              return (
                <div key={i} className="flex flex-col gap-2">
                  {/* 该轮执行的工作流程步骤（跟在该轮用户输入后面） */}
                  {msg.steps && msg.steps.length > 0 && renderSteps(msg.steps)}
                  {/* 结构化卡片在前（SSE 里 card 事件先于整理文本到达），
                      助手的解析文本是对卡片结果的说明，应排在卡片下面 */}
                  {msg.cards && msg.cards.length > 0 && (
                    <div className="flex flex-col gap-2">
                      {msg.cards.map((card) => (
                        <InlineCard key={card.id} card={card} />
                      ))}
                    </div>
                  )}
                  {msg.content && (
                    <div className="text-[14px] leading-[24px] text-maia-text whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  )}
                </div>
              )
            })}

            {(streaming || steps.length > 0) && (
              <div className="flex flex-col gap-2">
                {/* 工作流程步骤与卡片先渲染，流式文本随后 —— 与 SSE 事件顺序一致 */}
                {steps.length > 0 && renderSteps(steps)}
                {streamCards.length > 0 && (
                  <div className="flex flex-col gap-2">
                    {streamCards.map((card) => (
                      <InlineCard key={card.id} card={card} />
                    ))}
                  </div>
                )}
                {streaming && (
                  <div className="text-[14px] leading-[24px] text-maia-text whitespace-pre-wrap">
                    {streaming}
                    <span className="inline-block w-1.5 h-4 bg-maia-accent ml-0.5 animate-pulse" />
                  </div>
                )}
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>
      </div>

      {/* Input — 仅交互式 Agent 显示对话输入框 */}
      {running && !isTask && !isWebService && (
        <div className="border-t border-maia-border bg-maia-surface px-4 pt-3 pb-2.5 shrink-0">
          <div className="flex items-end gap-2 w-full bg-maia-sidebar rounded-xl border border-maia-border p-2 focus-within:border-maia-accent/50 transition-colors">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  sendMessage()
                }
              }}
              placeholder={`向 ${agent.name} 发送消息…（Enter 发送，Shift+Enter 换行）`}
              rows={1}
              className="flex-1 bg-transparent px-1 py-1.5 text-[14px] leading-[22px] outline-none resize-none min-h-[32px] max-h-[140px] placeholder:text-maia-text-muted"
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

      {dataPickerFor && (
        <DataFilePicker
          paramName={dataPickerFor}
          onSelect={(path) => {
            setTestParams((prev) => ({ ...prev, [dataPickerFor]: path }))
            setDataPickerFor(null)
          }}
          onClose={() => setDataPickerFor(null)}
        />
      )}
    </div>
  )
}

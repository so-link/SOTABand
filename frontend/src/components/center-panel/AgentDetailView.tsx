import { useState, useEffect, useRef } from 'react'
import {
  Bot, Send, Play, Square, RefreshCw, Loader2, FileText, FileCode, X, Pencil, Save,
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

const BASE_URL = ''

export function AgentDetailView() {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const agent = selectedResource as AgentResource | null
  // Python 代码用 4 空格缩进
  const onCodeTabIndent = useTabIndent('    ')

  // Agent runtime state
  const [running, setRunning] = useState(false)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([])
  const [streaming, setStreaming] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [loading, setLoading] = useState(false)

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

  const setActiveView = useUIStore((s) => s.setActiveView)

  const bottomRef = useRef<HTMLDivElement>(null)

  // Fetch agent details on mount / agent change
  useEffect(() => {
    if (!agent) return
    fetch(`${BASE_URL}/api/agent/${agent.id}`)
      .then((r) => r.json())
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

  if (!agent) {
    return (
      <div className="flex items-center justify-center h-full text-maia-text-muted text-sm">
        请在左侧 Agent 空间选择一个 Agent
      </div>
    )
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
      await fetch(`${BASE_URL}/api/agent/${agent.id}/start`, { method: 'POST' })
      setRunning(true)
      setMessages([])
      setStreaming('')
    } catch { /* ignore */ }
    setLoading(false)
  }

  const stop = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/stop`, { method: 'POST' })
      setRunning(false)
    } catch { /* ignore */ }
    setLoading(false)
  }

  const restart = async () => {
    setLoading(true)
    try {
      await fetch(`${BASE_URL}/api/agent/${agent.id}/restart`, { method: 'POST' })
      setRunning(true)
      setMessages([])
      setStreaming('')
    } catch { /* ignore */ }
    setLoading(false)
  }

  const sendMessage = async () => {
    if (!input.trim() || isSending) return
    const userInput = input
    setInput('')
    setMessages((m) => [...m, { role: 'user', content: userInput }])
    setIsSending(true)
    setStreaming('')

    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 120_000)

    try {
      const res = await fetch(`${BASE_URL}/api/agent/${agent.id}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: userInput }),
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
              if (eventType === 'content' || !eventType) {
                full += payload.text || ''
                setStreaming(full)
              } else if (eventType === 'card') {
                // Append card summary to content so it's visible
                const cardTitle = payload.title || ''
                const cardSummary = payload.summary || payload.data?.summary || ''
                if (cardTitle) {
                  full += `\n\n📋 **${cardTitle}**`
                }
                if (cardSummary) {
                  full += `\n${cardSummary}`
                }
                // Also inline structured data if present
                const d = payload.data
                if (d && typeof d === 'object') {
                  for (const [k, v] of Object.entries(d)) {
                    if (k !== 'summary' && v !== null && v !== undefined && v !== '') {
                      full += `\n- ${k}: ${v}`
                    }
                  }
                }
                setStreaming(full)
              } else if (eventType === 'done') {
                setMessages((m) => [...m, { role: 'agent', content: full }])
                setStreaming('')
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

      if (!streamDone && full) {
        setMessages((m) => [...m, { role: 'agent', content: full }])
        setStreaming('')
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

          {/* Messages area */}
          {messages.length === 0 && !streaming && (
            <div className="flex flex-col items-center justify-center py-12 text-maia-text-muted gap-2">
              <Bot className="h-8 w-8 opacity-30" />
              <p className="text-sm">
                {running ? 'Agent 已启动，输入消息开始交互' : '点击"启动"按钮启动 Agent'}
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
                <div className="text-[13px] leading-relaxed tracking-wide text-maia-text whitespace-pre-wrap">
                  {msg.content}
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

            <div ref={bottomRef} />
          </div>
        </div>
      </div>

      {/* Input */}
      {running && (
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
    </div>
  )
}

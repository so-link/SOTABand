import { useMemo, useRef, useState } from 'react'
import { Bot, Clock, Database, FolderOpen, Loader2, Plus, Wrench, BrainCircuit, MessagesSquare } from 'lucide-react'
import { useChatStore } from '@/stores/chat-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { useResourceStore } from '@/stores/resource-store'

interface Suggestion {
  key: string
  title: string
  subtitle: string
  icon: typeof Bot
  prompt: string
}

/** 系统尚无资源时展示的「能做什么」条目 */
const CAPABILITIES = [
  { icon: Database, text: '注册与预览数据集：CSV、Excel、Parquet、图片、Markdown 等格式，支持打包下载' },
  { icon: Wrench, text: '用自然语言生成可复用的数据处理工具，代码核验后注册入库' },
  { icon: BrainCircuit, text: '注册模型、编排 Agent，支持定时执行与结果汇报' },
  { icon: MessagesSquare, text: '直接在对话里描述目标，系统自动匹配数据、工具与 Agent 执行' },
]

/** 系统尚无资源时展示的「如何开始」步骤 */
const GETTING_STARTED = [
  { step: '1', title: '准备好数据', desc: '点击下方「上传数据」，或在左栏数据空间注册本机已有目录' },
  { step: '2', title: '建立处理能力', desc: '左栏工具空间 / Agent 空间点「+」，用自然语言创建，也可以直接跳过' },
  { step: '3', title: '开始对话', desc: '在下方输入框描述目标并引用数据，剩下的交给系统' },
]

export function ChatEmptyState() {
  const setInputText = useChatStore((s) => s.setInputText)
  const agents = useResourceStore((s) => s.agentResources)
  const tools = useResourceStore((s) => s.toolResources)
  const dataResources = useResourceStore((s) => s.dataResources)
  const modelResources = useResourceStore((s) => s.modelResources)
  const recentAgentUsage = useResourceStore((s) => s.recentAgentUsage)
  const uploadFiles = useFileTreeStore((s) => s.uploadFiles)

  const fileRef = useRef<HTMLInputElement>(null)
  const [isUploading, setIsUploading] = useState(false)

  // 示例任务优先取用户真实上下文：数据集 → 最近用过的 Agent → 可用工具，
  // 不足 3 条再用通用引导补齐，避免出现与当前资源无关的凑数项。
  const suggestions: Suggestion[] = useMemo(() => {
    const usageMap = new Map(recentAgentUsage.map((u) => [u.agent_id, u]))
    const usedAgents = agents
      .filter((a) => usageMap.has(a.id) && a.status === 'active')
      .sort((a, b) => usageMap.get(b.id)!.last_at - usageMap.get(a.id)!.last_at)
      .slice(0, 2)
      .map((a) => ({
        key: `agent-${a.id}`,
        title: `用 ${a.name} 处理任务`,
        subtitle: `Agent · 最近用过 ${usageMap.get(a.id)!.use_count} 次`,
        icon: Bot,
        prompt: `用 ${a.name} 帮我处理当前任务`,
      }))

    const datasetSuggestion: Suggestion[] = dataResources
      .filter((d) => d.available !== false)
      .slice(0, 1)
      .map((d) => ({
        key: `data-${d.id}`,
        title: `分析「${d.name}」`,
        subtitle: `数据集 · ${d.format && d.format !== 'unknown' ? d.format.toUpperCase() : '文件'} · 概况与异常`,
        icon: Database,
        prompt: `帮我分析数据集「${d.name}」，先给出数据概况、字段含义和异常情况`,
      }))

    const toolSuggestion: Suggestion[] = tools
      .filter((t) => (t.usageCount || 0) > 0)
      .sort((a, b) => (b.usageCount || 0) - (a.usageCount || 0))
      .slice(0, 1)
      .map((t) => ({
        key: `tool-${t.id}`,
        title: `用 ${t.name} 处理数据`,
        subtitle: `工具 · 已用过 ${t.usageCount} 次`,
        icon: Wrench,
        prompt: `用 ${t.name} 处理我的数据`,
      }))

    const starters: Suggestion[] = [
      {
        key: 'starter-analyze',
        title: '分析一个数据集并给出结论',
        subtitle: '数据分析 · 自动匹配工具与 Agent',
        icon: Database,
        prompt: '帮我分析一个数据集，先看数据质量和主要规律，再给出结论',
      },
      {
        key: 'starter-build-tool',
        title: '创建一个可复用的数据处理工具',
        subtitle: '工具生成 · 代码可核验后注册',
        icon: Wrench,
        prompt: '我想创建一个数据处理工具，先告诉我需要提供什么',
      },
      {
        key: 'starter-agent',
        title: '让 Agent 定时监控并汇报结果',
        subtitle: 'Agent 编排 · 支持定时执行',
        icon: Clock,
        prompt: '帮我搭建一个定时执行的 Agent 任务，定期监控并汇报结果',
      },
    ]

    return [...datasetSuggestion, ...usedAgents, ...toolSuggestion, ...starters].slice(0, 3)
  }, [agents, tools, dataResources, recentAgentUsage])

  // 系统还很"空"（没有任何数据/工具/模型/Agent）时，展示使用指引，
  // 帮助用户理解本系统能解决什么问题、从哪里开始。
  const isFreshSystem =
    dataResources.length === 0 && tools.length === 0 && agents.length === 0 && modelResources.length === 0

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setIsUploading(true)
    try {
      await uploadFiles(files)
    } finally {
      setIsUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* 中栏高度不足时内容纵向溢出会盖住上方标签栏，故外层滚动、
          内层 min-h-full 居中：内容放得下时居中，放不下时可滚动而不会向上溢出。 */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6">
        <div className="min-h-full flex flex-col items-center justify-center py-6 text-center">
        <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-maia-accent-border bg-maia-accent-light text-maia-accent">
          <Bot className="h-5 w-5" />
        </span>
        <h2 className="text-[18px] leading-7 font-semibold text-maia-text-heading mt-4 text-balance">想完成什么任务？</h2>
        <p className="text-[13px] leading-[20px] text-maia-text-muted mt-1.5 max-w-[520px] text-pretty">
          描述目标或引用数据，SOTABand 会匹配 Agent 与工具并开始执行。
        </p>

        {isFreshSystem ? (
          /* 首次使用：系统能力介绍与上手步骤 */
          <div className="w-full max-w-[560px] mt-5 flex flex-col gap-3 text-left">
            <div className="rounded-[10px] border border-maia-border bg-maia-sidebar px-4 py-3.5">
              <div className="flex items-center gap-1.5 mb-2.5">
                <Bot className="h-3.5 w-3.5 text-maia-accent" />
                <span className="text-[13px] font-semibold text-maia-text-heading">SOTABand 能做什么</span>
              </div>
              <ul className="flex flex-col gap-2">
                {CAPABILITIES.map((c) => {
                  const Icon = c.icon
                  return (
                    <li key={c.text} className="flex items-start gap-2 text-[13px] leading-[20px] text-maia-text-secondary">
                      <Icon className="h-3.5 w-3.5 shrink-0 mt-[3px] text-maia-text-muted" />
                      <span>{c.text}</span>
                    </li>
                  )
                })}
              </ul>
            </div>

            <div className="rounded-[10px] border border-maia-border bg-maia-sidebar px-4 py-3.5">
              <div className="flex items-center gap-1.5 mb-2.5">
                <FolderOpen className="h-3.5 w-3.5 text-maia-accent" />
                <span className="text-[13px] font-semibold text-maia-text-heading">如何开始</span>
              </div>
              <ol className="flex flex-col gap-2">
                {GETTING_STARTED.map((g) => (
                  <li key={g.step} className="flex items-start gap-2.5">
                    <span className="flex h-[18px] w-[18px] shrink-0 mt-[2px] items-center justify-center rounded-full bg-maia-accent-light text-maia-accent text-[11px] font-semibold">
                      {g.step}
                    </span>
                    <span className="text-[13px] leading-[20px]">
                      <span className="font-medium text-maia-text">{g.title}</span>
                      <span className="text-maia-text-muted"> · {g.desc}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        ) : (
          suggestions.length > 0 && (
          <div className="w-full max-w-[560px] mt-5 flex flex-col gap-2">
            {suggestions.map((s) => {
              const Icon = s.icon
              return (
                <button
                  key={s.key}
                  onClick={() => setInputText(s.prompt)}
                  className="group flex items-center gap-3 h-[54px] px-3.5 rounded-[10px] border border-maia-border bg-maia-sidebar text-left hover:bg-maia-sidebar-hover hover:border-maia-accent/40 transition-colors"
                >
                  <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[8px] bg-maia-surface text-maia-text-secondary group-hover:text-maia-accent transition-colors">
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] leading-[18px] font-medium text-maia-text truncate">{s.title}</span>
                    <span className="block text-[13px] leading-4 text-maia-text-muted truncate">{s.subtitle}</span>
                  </span>
                  <span className="text-maia-text-muted opacity-0 group-hover:opacity-100 transition-opacity">
                    <Plus className="h-3 w-3" />
                  </span>
                </button>
              )
            })}
          </div>
          )
        )}

        <button
          onClick={() => fileRef.current?.click()}
          disabled={isUploading}
          className="mt-3 inline-flex items-center gap-1.5 h-7 px-2.5 rounded-[7px] border border-maia-border bg-maia-surface text-[13px] text-maia-text-secondary hover:bg-maia-sidebar-hover transition-colors disabled:opacity-60"
        >
          {isUploading ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />
              正在上传…
            </>
          ) : (
            <>
              <Database className="h-3 w-3" />
              上传数据
            </>
          )}
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => void handleUpload(e.target.files)}
        />
        </div>
      </div>
    </div>
  )
}

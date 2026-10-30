import { useRef, useState } from 'react'
import { Bot, Database, Loader2, Plus, Wrench } from 'lucide-react'
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

export function ChatEmptyState() {
  const setInputText = useChatStore((s) => s.setInputText)
  const agents = useResourceStore((s) => s.agentResources)
  const tools = useResourceStore((s) => s.toolResources)
  const uploadFiles = useFileTreeStore((s) => s.uploadFiles)

  const fileRef = useRef<HTMLInputElement>(null)
  const [isUploading, setIsUploading] = useState(false)

  // 示例任务取自真实资源清单，避免写死不存在的工具/Agent
  const suggestions: Suggestion[] = [
    ...agents.slice(0, 2).map((a) => ({
      key: `agent-${a.id}`,
      title: `用 ${a.name} 处理任务`,
      subtitle: 'Agent · 自动调度',
      icon: Bot,
      prompt: `用 ${a.name} 帮我处理当前任务`,
    })),
    ...tools.slice(0, 3 - Math.min(agents.length, 2)).map((t) => ({
      key: `tool-${t.id}`,
      title: `调用 ${t.name}`,
      subtitle: '工具 · 直接执行',
      icon: Wrench,
      prompt: `用 ${t.name} 处理我的数据`,
    })),
  ]

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
      <div className="flex-1 min-h-0 flex flex-col items-center justify-center px-6 text-center">
        <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-maia-accent-border bg-maia-accent-light text-maia-accent">
          <Bot className="h-5 w-5" />
        </span>
        <h2 className="text-[16px] leading-6 font-semibold text-maia-text-heading mt-3.5 text-balance">想完成什么任务？</h2>
        <p className="text-[13px] leading-[20px] text-maia-text-muted mt-1.5 max-w-[440px] text-pretty">
          描述目标或引用数据，SOTABand 会匹配 Agent 与工具并开始执行。
        </p>

        {suggestions.length > 0 && (
          <div className="w-full max-w-[420px] mt-4 flex flex-col gap-1.5">
            {suggestions.map((s) => {
              const Icon = s.icon
              return (
                <button
                  key={s.key}
                  onClick={() => setInputText(s.prompt)}
                  className="group flex items-center gap-2.5 h-[46px] px-3 rounded-[9px] border border-maia-border bg-maia-sidebar text-left hover:bg-maia-sidebar-hover transition-colors"
                >
                  <span className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[7px] bg-maia-surface text-maia-text-secondary">
                    <Icon className="h-3.5 w-3.5" />
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
  )
}

import { useMemo } from 'react'
import {
  Bot,
  ChevronRight,
  Database,
  Folder,
  Settings,
  Wrench,
} from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useChatStore } from '@/stores/chat-store'
import { useUIStore } from '@/stores/ui-store'

function Section({ title, extra, children }: { title: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between text-[13px] text-maia-text-muted">
        <span>{title}</span>
        {extra}
      </div>
      {children}
    </div>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="rounded-[10px] border border-maia-border bg-maia-surface p-2.5">{children}</div>
}

/**
 * 右栏「会话上下文」：没有选中资源/文件时展示，替代原来的空白占位。
 * 有对话时是当前 Agent + 引用资源 + 运行信息；对话为空时是开始使用 + 最近 Agent + 快速开始。
 */
export function ChatContextPanel() {
  const messages = useChatStore((s) => s.messages)
  const isSending = useChatStore((s) => s.isSending)
  const agents = useResourceStore((s) => s.agentResources)
  const tools = useResourceStore((s) => s.toolResources)
  const pathRefs = useChatStore((s) => s.pathRefs)
  const attachedFiles = useChatStore((s) => s.attachedFiles)
  const setActiveView = useUIStore((s) => s.setActiveView)

  const isEmpty = messages.length === 0
  const currentAgent = agents[0]

  const references = useMemo(
    () => [
      ...pathRefs.map((r) => ({ key: `p-${r.name}`, name: r.name, kind: '目录' })),
      ...attachedFiles.map((f) => ({ key: `f-${f.id}`, name: f.fileName, kind: f.format?.toUpperCase() || '文件' })),
    ],
    [pathRefs, attachedFiles],
  )

  const toolCallCount = useChatStore((s) =>
    s.messages.reduce((n, m) => n + (m.cards?.filter((c) => c.type === 'tool-confirm' || c.type === 'execution-progress').length ?? 0), 0),
  )

  return (
    <div className="flex flex-col h-full w-full min-w-0 overflow-hidden">
      <div className="flex items-center justify-between h-9 px-3 shrink-0 border-b border-maia-border text-[13px] text-maia-text-muted">
        会话上下文
      </div>

      <div className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden p-3 flex flex-col gap-3.5">
        {isEmpty ? (
          <>
            <Section title="开始使用">
              <Card>
                <div className="flex items-center gap-2.5">
                  <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-maia-accent text-white">
                    <Bot className="h-3.5 w-3.5" />
                  </span>
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium text-maia-text">还没有选择 Agent</div>
                    <div className="text-[12px] leading-[18px] text-maia-text-muted">可直接描述任务，系统自动匹配</div>
                  </div>
                </div>
                <p className="text-[13px] leading-[18px] text-maia-text-muted mt-2">
                  也可以在 Agent 空间挑选一个专用 Agent，再开始对话。
                </p>
                <button
                  onClick={() => agents[0] ? setActiveView(`agent-detail:${agents[0].id}` as never) : setActiveView('agent-editor')}
                  className="mt-2 w-full h-7 rounded-[7px] bg-maia-accent text-white text-[13px] font-medium hover:bg-maia-accent-hover transition-colors"
                >
                  {agents.length > 0 ? '浏览 Agent' : '创建 Agent'}
                </button>
              </Card>
            </Section>

            {agents.length > 0 && (
              <Section title="最近使用的 Agent" extra={<span>{agents.length}</span>}>
                <div className="rounded-[10px] border border-maia-border bg-maia-surface p-1">
                  {agents.slice(0, 3).map((a) => (
                    <button
                      key={a.id}
                      onClick={() => setActiveView(`agent-detail:${a.id}` as never)}
                      className="flex items-center gap-2 w-full px-1.5 py-1.5 rounded hover:bg-maia-sidebar-hover text-left transition-colors"
                    >
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[6px] bg-maia-sidebar-active text-[12px] font-semibold text-maia-text-secondary">
                        {a.name.slice(0, 1)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-[13px] text-maia-text truncate">{a.name}</span>
                        <span className="block text-[12px] text-maia-text-muted truncate">{'requiredTools' in a && a.requiredTools?.length ? `工具 ${a.requiredTools.length} 个` : '通用任务'}</span>
                      </span>
                      <span className="text-[12px] text-maia-text-muted">可用</span>
                    </button>
                  ))}
                </div>
              </Section>
            )}

            <Section title="快速开始">
              <div className="rounded-[10px] border border-maia-border bg-maia-surface p-1">
                {[
                  { label: '上传数据集', icon: Database, view: 'dataset-editor' as const },
                  { label: '注册自定义工具', icon: Wrench, view: 'tool-editor' as const },
                  { label: '配置模型服务', icon: Settings, view: 'settings' as const },
                ].map((item) => (
                  <button
                    key={item.label}
                    onClick={() => setActiveView(item.view)}
                    className="flex items-center gap-2 w-full h-7 px-1.5 rounded text-[13px] text-maia-text-secondary hover:bg-maia-sidebar-hover transition-colors"
                  >
                    <item.icon className="h-3.5 w-3.5 text-maia-text-muted" />
                    <span className="flex-1 text-left">{item.label}</span>
                    <ChevronRight className="h-3 w-3 text-maia-text-muted" />
                  </button>
                ))}
              </div>
            </Section>
          </>
        ) : (
          <>
            <Section title="当前 Agent">
              <Card>
                <div className="flex items-center gap-2.5">
                  <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-maia-accent text-white text-[13px] font-semibold">
                    {(currentAgent?.name ?? 'A').slice(0, 1)}
                  </span>
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium text-maia-text truncate">
                      {currentAgent?.name ?? '交互 Agent'}
                    </div>
                    <div className="flex items-center gap-1 text-[13px] text-maia-text-muted">
                      <span className={`h-1.5 w-1.5 rounded-full ${isSending ? 'bg-maia-warning' : 'bg-maia-success'}`} />
                      {isSending ? '执行中 · 自动调度' : '空闲 · 自动调度'}
                    </div>
                  </div>
                </div>
                <p className="text-[13px] leading-[18px] text-maia-text-muted mt-2">
                  负责读取数据、调用工具，并汇总执行结果。
                </p>
                <button
                  onClick={() => currentAgent ? setActiveView(`agent-detail:${currentAgent.id}` as never) : setActiveView('agent-editor')}
                  className="mt-2 w-full h-7 rounded-[7px] border border-maia-border bg-maia-surface text-[13px] text-maia-text-secondary hover:bg-maia-sidebar-hover transition-colors"
                >
                  {currentAgent ? '查看 Agent' : '选择 Agent'}
                </button>
              </Card>
            </Section>

            <Section title="引用资源" extra={<span>{references.length}</span>}>
              <div className="rounded-[10px] border border-maia-border bg-maia-surface p-1">
                {references.length === 0 && (
                  <p className="px-1.5 py-1 text-[13px] text-maia-text-muted">还没有引用资源，@ 或拖入文件即可。</p>
                )}
                {references.map((r) => (
                  <div key={r.key} className="flex items-center gap-2 h-7 px-1.5 text-[13px] text-maia-text-secondary">
                    <Folder className="h-3.5 w-3.5 shrink-0 text-maia-text-muted" />
                    <span className="min-w-0 flex-1 truncate">{r.name}</span>
                    <span className="text-[12px] text-maia-text-muted">{r.kind}</span>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="运行信息">
              <div className="rounded-[10px] border border-maia-border bg-maia-surface px-2.5 py-1">
                {[
                  ['状态', isSending ? '执行中' : '空闲'],
                  ['会话消息', `${messages.length} 条`],
                  ['工具调用', `${toolCallCount} 次`],
                  ['可用工具', `${tools.length} 个`],
                ].map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between h-[26px] text-[13px] text-maia-text-muted">
                    <span>{k}</span>
                    <span className="text-maia-text-secondary font-medium">{v}</span>
                  </div>
                ))}
              </div>
            </Section>
          </>
        )}
      </div>
    </div>
  )
}

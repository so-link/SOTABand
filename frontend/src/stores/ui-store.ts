import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type ActiveView =
  | 'chat'
  | 'data-preview'
  | 'file-preview'
  | 'code-review'
  | 'orchestration'
  | 'task-monitor'
  | 'agent-editor'
  | 'agent-detail'
  | 'tool-editor'
  | 'tool-detail'
  | 'dataset-editor'
  | 'repository'
  | 'dataset-repository'
  | 'cron-jobs'
  | 'settings'
  | 'account'
  | 'users'
  // 每个 Agent 独立的详情视图：形如 "agent-detail:{agentId}"
  // 使不同 Agent 各自拥有独立的标签页与界面，互不共享/覆盖。
  | `agent-detail:${string}`
  // 每个工具独立的详情视图：形如 "tool-detail:{toolId}"
  | `tool-detail:${string}`
  // 每个模型独立的详情视图：形如 "model-detail:{modelId}"
  | `model-detail:${string}`
  // 每个数据集独立的预览视图：形如 "data-preview:{datasetId}"
  | `data-preview:${string}`

/** 判断视图是否为「单 Agent 详情」并取出其 agentId。 */
export function parseAgentDetailView(view: string): string | null {
  return view.startsWith('agent-detail:') ? view.slice('agent-detail:'.length) : null
}

/** 判断视图是否为「单工具详情」并取出其 toolId。 */
export function parseToolDetailView(view: string): string | null {
  return view.startsWith('tool-detail:') ? view.slice('tool-detail:'.length) : null
}

/** 判断视图是否为「单模型详情」并取出其 modelId。 */
export function parseModelDetailView(view: string): string | null {
  return view.startsWith('model-detail:') ? view.slice('model-detail:'.length) : null
}

/** 判断视图是否为「单数据集预览」并取出其 datasetId。 */
export function parseDatasetPreviewView(view: string): string | null {
  return view.startsWith('data-preview:') ? view.slice('data-preview:'.length) : null
}

export type Theme = 'dark' | 'light'

function getInitialTheme(): Theme {
  try {
    const s = localStorage.getItem('sotaband-theme')
    if (s === 'light' || s === 'dark') return s
  } catch {}
  return 'dark'
}

interface UIState {
  leftPanelOpen: boolean
  rightPanelOpen: boolean
  activeView: ActiveView
  openViews: ActiveView[]
  leftPanelTab: 'files' | 'resources'
  theme: Theme

  toggleLeftPanel: () => void
  toggleRightPanel: () => void
  setLeftPanelOpen: (open: boolean) => void
  setRightPanelOpen: (open: boolean) => void
  setActiveView: (view: ActiveView) => void
  closeView: (view: ActiveView) => void
  closeOtherViews: () => void
  closeAllViews: () => void
  setLeftPanelTab: (tab: 'files' | 'resources') => void
  toggleTheme: () => void
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      leftPanelOpen: true,
      rightPanelOpen: true,
      activeView: 'chat',
      openViews: ['chat'],
      leftPanelTab: 'files',
      theme: getInitialTheme(),

      toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
      toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
      setLeftPanelOpen: (open) => set({ leftPanelOpen: open }),
      setRightPanelOpen: (open) => set({ rightPanelOpen: open }),

      setActiveView: (view) =>
        set((s) => ({
          activeView: view,
          openViews: s.openViews.includes(view) ? s.openViews : [...s.openViews, view],
        })),

      closeView: (view) =>
        set((s) => {
          if (view === 'chat') return s
          const next = s.openViews.filter((v) => v !== view)
          let nextActive = s.activeView
          if (s.activeView === view) {
            const idx = s.openViews.indexOf(view)
            nextActive = next[Math.min(idx, next.length - 1)] || 'chat'
          }
          return { openViews: next, activeView: nextActive }
        }),

      // 关闭除当前激活标签外的所有可关闭标签（chat 固定不可关）
      closeOtherViews: () =>
        set((s) => {
          const keep = s.activeView
          const next = s.openViews.filter((v) => v === keep || v === 'chat')
          return { openViews: next.length > 0 ? next : ['chat'], activeView: keep }
        }),

      // 关闭所有可关闭标签，回到对话视图
      closeAllViews: () => set({ openViews: ['chat'], activeView: 'chat' }),

      setLeftPanelTab: (tab) => set({ leftPanelTab: tab }),

      toggleTheme: () =>
        set((s) => {
          const next: Theme = s.theme === 'dark' ? 'light' : 'dark'
          document.documentElement.setAttribute('data-theme', next)
          try { localStorage.setItem('sotaband-theme', next) } catch {}
          return { theme: next }
        }),
    }),
    {
      name: 'sotaband-ui',
      // 只持久化与「刷新后恢复界面」相关的字段：左侧标签、中栏打开的标签与激活标签。
      // 面板开关、主题等不放入（主题已有独立 sotaband-theme，面板开关每次默认打开更合理）。
      partialize: (s) => ({
        activeView: s.activeView,
        openViews: s.openViews,
        leftPanelTab: s.leftPanelTab,
      }),
    },
  ),
)

import { create } from 'zustand'

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
  | 'settings'
  // 每个 Agent 独立的详情视图：形如 "agent-detail:{agentId}"
  // 使不同 Agent 各自拥有独立的标签页与界面，互不共享/覆盖。
  | `agent-detail:${string}`
  // 每个工具独立的详情视图：形如 "tool-detail:{toolId}"
  | `tool-detail:${string}`

/** 判断视图是否为「单 Agent 详情」并取出其 agentId。 */
export function parseAgentDetailView(view: string): string | null {
  return view.startsWith('agent-detail:') ? view.slice('agent-detail:'.length) : null
}

/** 判断视图是否为「单工具详情」并取出其 toolId。 */
export function parseToolDetailView(view: string): string | null {
  return view.startsWith('tool-detail:') ? view.slice('tool-detail:'.length) : null
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
  setActiveView: (view: ActiveView) => void
  closeView: (view: ActiveView) => void
  setLeftPanelTab: (tab: 'files' | 'resources') => void
  toggleTheme: () => void
}

export const useUIStore = create<UIState>((set) => ({
  leftPanelOpen: true,
  rightPanelOpen: false,
  activeView: 'chat',
  openViews: ['chat'],
  leftPanelTab: 'files',
  theme: getInitialTheme(),

  toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),

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

  setLeftPanelTab: (tab) => set({ leftPanelTab: tab }),

  toggleTheme: () =>
    set((s) => {
      const next: Theme = s.theme === 'dark' ? 'light' : 'dark'
      document.documentElement.setAttribute('data-theme', next)
      try { localStorage.setItem('sotaband-theme', next) } catch {}
      return { theme: next }
    }),
}))

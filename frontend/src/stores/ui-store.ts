import { create } from 'zustand'

export type ActiveView =
  | 'chat'
  | 'data-preview'
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

import { create } from 'zustand'

export type ActiveView =
  | 'chat'
  | 'data-preview'
  | 'code-review'
  | 'orchestration'
  | 'task-monitor'

interface UIState {
  leftPanelOpen: boolean
  rightPanelOpen: boolean
  activeView: ActiveView
  leftPanelTab: 'files' | 'resources'

  toggleLeftPanel: () => void
  toggleRightPanel: () => void
  setActiveView: (view: ActiveView) => void
  setLeftPanelTab: (tab: 'files' | 'resources') => void
}

export const useUIStore = create<UIState>((set) => ({
  leftPanelOpen: true,
  rightPanelOpen: false,
  activeView: 'chat',
  leftPanelTab: 'files',

  toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  setActiveView: (view) => set({ activeView: view }),
  setLeftPanelTab: (tab) => set({ leftPanelTab: tab }),
}))

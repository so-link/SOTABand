import { useRef, useEffect, useCallback } from 'react'
import {
  Panel,
  Group,
  Separator,
  usePanelRef,
  type Layout,
} from 'react-resizable-panels'
import { TopBar } from '@/components/topbar/TopBar'
import { StatusBar } from '@/components/status-bar/StatusBar'
import { LeftPanel } from '@/components/left-panel/LeftPanel'
import { CenterPanel } from '@/components/center-panel/CenterPanel'
import { RightPanel } from '@/components/right-panel/RightPanel'
import { useUIStore } from '@/stores/ui-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { useResourceStore } from '@/stores/resource-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'

const STORAGE_KEY = 'maia-layout'

const DEFAULT_LAYOUT = [38, 40, 22] // left, center, right in %

function loadLayout(): number[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return null
}

function saveLayout(layout: number[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout))
  } catch { /* ignore */ }
}

export function MainLayout() {
  const { leftPanelOpen, rightPanelOpen } = useUIStore()
  const loadTree = useFileTreeStore((s) => s.loadTree)
  const fetchAllResources = useResourceStore((s) => s.fetchAllResources)
  const leftRef = usePanelRef()
  const rightRef = usePanelRef()
  const initialLayout = useRef(loadLayout() || DEFAULT_LAYOUT)

  useEffect(() => {
    loadTree()
    fetchAllResources()
    // 工作区工具清单在启动时就加载：状态栏 TOOLS 与左栏「工具空间」用的是同一份数据，
    // 不能等到用户切到「资源空间」才拉取，否则首屏会显示 0。
    useWorkspaceToolStore.getState().fetchFromApi()
  }, [loadTree, fetchAllResources])

  // Sync panel collapse/expand with TopBar toggle
  const prevLeftOpen = useRef(leftPanelOpen)
  const prevRightOpen = useRef(rightPanelOpen)

  useEffect(() => {
    if (prevLeftOpen.current === leftPanelOpen) return
    prevLeftOpen.current = leftPanelOpen
    if (leftPanelOpen) leftRef.current?.expand()
    else leftRef.current?.collapse()
  }, [leftPanelOpen, leftRef])

  useEffect(() => {
    if (prevRightOpen.current === rightPanelOpen) return
    prevRightOpen.current = rightPanelOpen
    if (rightPanelOpen) rightRef.current?.expand()
    else rightRef.current?.collapse()
  }, [rightPanelOpen, rightRef])

  const handleLayoutChange = useCallback((layout: Layout) => {
    const raw = [
      layout['panel-left'] ?? DEFAULT_LAYOUT[0],
      layout['panel-center'] ?? DEFAULT_LAYOUT[1],
      layout['panel-right'] ?? DEFAULT_LAYOUT[2],
    ]
    // 折叠中的面板尺寸为 0，不要把 0 写进存储，否则下次启动会一直是收起状态
    const sizes = raw.map((v, i) => (Math.round(v) <= 0 ? DEFAULT_LAYOUT[i] : Math.round(v)))
    saveLayout(sizes)
  }, [])

  return (
    <div className="flex flex-col h-full">
      <TopBar />

      <div className="flex-1 min-h-0">
        <Group
          orientation="horizontal"
          onLayoutChanged={handleLayoutChange}
        >
          <Panel
            id="panel-left"
            panelRef={leftRef}
            defaultSize={`${initialLayout.current[0]}%`}
            minSize={'18%'}
            collapsible
            collapsedSize={'0%'}
          >
            <LeftPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-center"
            defaultSize={`${initialLayout.current[1]}%`}
            minSize={'20%'}
          >
            <CenterPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-right"
            panelRef={rightRef}
            defaultSize={`${initialLayout.current[2]}%`}
            minSize={'18%'}
            collapsible
            collapsedSize={'0%'}
          >
            <RightPanel />
          </Panel>
        </Group>
      </div>

      <StatusBar />
    </div>
  )
}

function ResizeHandle() {
  return (
    <Separator className="w-[6px] bg-transparent hover:bg-maia-accent/8 active:bg-maia-accent/15 transition-colors cursor-col-resize flex items-center justify-center">
      <div className="w-[1px] h-full bg-maia-border" />
    </Separator>
  )
}

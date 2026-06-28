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
  }, [loadTree, fetchAllResources])

  // Sync panel collapse/expand with TopBar toggle
  const collapseLeft = useRef(false)
  const collapseRight = useRef(false)

  useEffect(() => {
    if (collapseLeft.current === leftPanelOpen) return
    collapseLeft.current = !leftPanelOpen
    if (!leftPanelOpen) {
      leftRef.current?.resize(0)
    } else {
      const layout = loadLayout()
      leftRef.current?.resize(layout?.[0] ?? DEFAULT_LAYOUT[0])
    }
  }, [leftPanelOpen, leftRef])

  useEffect(() => {
    if (collapseRight.current === rightPanelOpen) return
    collapseRight.current = !rightPanelOpen
    if (!rightPanelOpen) {
      rightRef.current?.resize(0)
    } else {
      const layout = loadLayout()
      rightRef.current?.resize(layout?.[2] ?? DEFAULT_LAYOUT[2])
    }
  }, [rightPanelOpen, rightRef])

  const handleLayoutChange = useCallback((layout: Layout) => {
    const sizes = [
      Math.round(layout['panel-left'] ?? DEFAULT_LAYOUT[0]),
      Math.round(layout['panel-center'] ?? DEFAULT_LAYOUT[1]),
      Math.round(layout['panel-right'] ?? DEFAULT_LAYOUT[2]),
    ]
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
            defaultSize={initialLayout.current[0]}
            minSize={0}
          >
            <LeftPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-center"
            defaultSize={initialLayout.current[1]}
            minSize={20}
          >
            <CenterPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-right"
            panelRef={rightRef}
            defaultSize={initialLayout.current[2]}
            minSize={0}
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

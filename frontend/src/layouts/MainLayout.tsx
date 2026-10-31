import { useRef, useEffect, useCallback } from 'react'
import {
  Panel,
  Group,
  Separator,
  usePanelRef,
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

// 侧栏用固定像素宽度，把剩余空间全部留给中间工作区。
// 之前用 38/40/22 百分比，窗口一宽侧栏就跟着膨胀（2048 宽时左栏约 778px），
// 中间栏反而被挤到 819px，Agent 审阅/代码区两侧出现大片空白。
const DEFAULT_LAYOUT = { left: 320, right: 360 }

function loadLayout(): { left: number; right: number } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      // 兼容旧版百分比数组：不能直接当像素用，丢弃后走新默认值
      if (parsed && !Array.isArray(parsed) && typeof parsed.left === 'number' && typeof parsed.right === 'number') {
        return parsed
      }
    }
  } catch { /* ignore */ }
  return DEFAULT_LAYOUT
}

function saveLayout(layout: { left: number; right: number }) {
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
  const initialLayout = useRef(loadLayout())

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

  const handleLayoutChange = useCallback(() => {
    // 百分比会随窗口尺寸变化，存下来恢复会失真；这里直接读面板的实际像素宽度。
    // 折叠中的面板宽度为 0，此时保留上一次保存值，避免下次启动仍是收起状态。
    const left = leftRef.current?.isCollapsed() ? null : leftRef.current?.getSize().inPixels
    const right = rightRef.current?.isCollapsed() ? null : rightRef.current?.getSize().inPixels
    const prev = loadLayout()
    saveLayout({
      left: left ? Math.round(left) : prev.left,
      right: right ? Math.round(right) : prev.right,
    })
  }, [leftRef, rightRef])

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
            defaultSize={initialLayout.current.left}
            minSize={240}
            maxSize={480}
            collapsible
            collapsedSize={0}
          >
            <LeftPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-center"
            minSize={420}
          >
            <CenterPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-right"
            panelRef={rightRef}
            defaultSize={initialLayout.current.right}
            minSize={280}
            maxSize={560}
            collapsible
            collapsedSize={0}
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

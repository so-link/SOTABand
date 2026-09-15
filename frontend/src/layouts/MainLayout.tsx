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

// 注意：v1 期间存在「百分比被当成像素」的 bug，旧值（如 [3, 95, 2]）已不可信，
// 因此换用 v2 键名让历史脏数据失效，避免侧边栏被恢复成 3% 左右的异常宽度。
const STORAGE_KEY = 'maia-layout-v2'

const DEFAULT_LAYOUT = [20, 60, 20] // left, center, right in %

function loadLayout(): number[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      // 校验：必须是 3 个百分比，且侧边栏不能是 0（0 表示「折叠」而非偏好宽度）
      if (
        Array.isArray(parsed) &&
        parsed.length === 3 &&
        parsed.every((n) => typeof n === 'number' && n > 0 && n < 100)
      ) {
        return parsed
      }
    }
  } catch { /* ignore */ }
  return DEFAULT_LAYOUT
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
  const initialLayout = useRef(loadLayout())
  const lastGoodLayout = useRef(initialLayout.current)

  useEffect(() => {
    loadTree()
    fetchAllResources()
  }, [loadTree, fetchAllResources])

  // 同步侧边栏折叠/展开（首次挂载也会执行一次，使面板与 store 初始状态一致）。
  // 注意：右侧栏默认收起，expand() 依赖「折叠前记住的尺寸」，对一开始就折叠的
  // 面板会回退到 minSize(0%)，源码里 0 又兜底成 1，导致弹出只有 1% 宽。
  // 因此改用 resize() 显式恢复到上次「完全展开」时记录的宽度。
  useEffect(() => {
    if (leftPanelOpen) leftRef.current?.resize(`${lastGoodLayout.current[0]}%`)
    else leftRef.current?.collapse()
  }, [leftPanelOpen, leftRef])

  useEffect(() => {
    if (rightPanelOpen) rightRef.current?.resize(`${lastGoodLayout.current[2]}%`)
    else rightRef.current?.collapse()
  }, [rightPanelOpen, rightRef])

  const handleLayoutChange = useCallback((layout: Layout) => {
    const sizes = [
      Math.round(layout['panel-left'] ?? DEFAULT_LAYOUT[0]),
      Math.round(layout['panel-center'] ?? DEFAULT_LAYOUT[1]),
      Math.round(layout['panel-right'] ?? DEFAULT_LAYOUT[2]),
    ]
    // 侧边栏折叠时布局值为 0，0 表示「隐藏」而非用户偏好宽度；
    // 有面板折叠时跳过保存，让 localStorage 始终保留一份「完全展开」的有效布局。
    const anyCollapsed = sizes[0] === 0 || sizes[2] === 0
    if (!anyCollapsed) {
      lastGoodLayout.current = sizes
    }
    saveLayout(lastGoodLayout.current)
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
            minSize="0%"
            collapsible
          >
            <LeftPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-center"
            defaultSize={`${initialLayout.current[1]}%`}
            minSize="20%"
          >
            <CenterPanel />
          </Panel>

          <ResizeHandle />

          <Panel
            id="panel-right"
            panelRef={rightRef}
            defaultSize={`${initialLayout.current[2]}%`}
            minSize="0%"
            collapsible
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

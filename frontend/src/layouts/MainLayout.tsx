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
    if (raw) {
      const parsed = JSON.parse(raw)
      // 只接受完整的三元素布局，避免损坏数据把 defaultSize 变成 undefined
      if (Array.isArray(parsed) && parsed.length === 3 && parsed.every((n) => typeof n === 'number')) {
        return parsed
      }
    }
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
  const persisted = useRef(loadLayout())
  const initialLayout = useRef(persisted.current ?? DEFAULT_LAYOUT)
  // 记住上一次的「有效」宽度（百分比）。0 只代表「已折叠」，绝不能覆盖记忆值，
  // 否则折叠一次后展开会读回 0 → 面板永久收起。
  const lastLeft = useRef(
    (persisted.current?.[0] ?? 0) > 0 ? persisted.current![0] : DEFAULT_LAYOUT[0],
  )
  const lastRight = useRef(
    (persisted.current?.[2] ?? 0) > 0 ? persisted.current![2] : DEFAULT_LAYOUT[2],
  )

  useEffect(() => {
    loadTree()
    fetchAllResources()
  }, [loadTree, fetchAllResources])

  // 把面板宽度同步到 ui-store 的开关状态。
  //
  // 两个曾经踩过的坑：
  //  1. PanelHandle.resize() 的 number 被当作**像素**，只有字符串才带单位
  //     （库内 bt()：number → "px"；string 按后缀解析，无后缀默认 "%"）。
  //     所以展开必须传 `"38%"` 而不是 `38`，否则会缩成 38px 一条缝。
  //  2. 折叠时 onLayoutChanged 会把 0 写进 localStorage，展开若再读回该值就会
  //     resize(0)。因此宽度存在 lastLeft/lastRight 里，只更新非 0 值。
  // 另外首帧 panelRef 可能尚未挂上，这里用 requestAnimationFrame 重试几帧。
  useEffect(() => {
    let cancelled = false
    let tries = 0
    const apply = () => {
      if (cancelled) return
      const handle = leftRef.current
      if (!handle) {
        if (tries++ < 10) requestAnimationFrame(apply)
        return
      }
      handle.resize(leftPanelOpen ? `${lastLeft.current}%` : 0)
    }
    apply()
    return () => { cancelled = true }
  }, [leftPanelOpen, leftRef])

  useEffect(() => {
    let cancelled = false
    let tries = 0
    const apply = () => {
      if (cancelled) return
      const handle = rightRef.current
      if (!handle) {
        if (tries++ < 10) requestAnimationFrame(apply)
        return
      }
      handle.resize(rightPanelOpen ? `${lastRight.current}%` : 0)
    }
    apply()
    return () => { cancelled = true }
  }, [rightPanelOpen, rightRef])

  const handleLayoutChange = useCallback((layout: Layout) => {
    const sizes = [
      Math.round(layout['panel-left'] ?? DEFAULT_LAYOUT[0]),
      Math.round(layout['panel-center'] ?? DEFAULT_LAYOUT[1]),
      Math.round(layout['panel-right'] ?? DEFAULT_LAYOUT[2]),
    ]
    // 只记非 0 宽度：折叠不会抹掉用户拖出来的宽度
    if (sizes[0] > 0) lastLeft.current = sizes[0]
    if (sizes[2] > 0) lastRight.current = sizes[2]
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
            minSize="20%"
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

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
import { useChatStore } from '@/stores/chat-store'
import { useWorkspaceToolStore } from '@/stores/workspace-tool-store'

const STORAGE_KEY = 'maia-layout'
// 布局数据的结构/默认值版本。调整下面的 DEFAULT_LAYOUT 时必须一并 bump，
// 否则老浏览器里存着旧默认值的人永远看不到新默认（前端更新不会动 localStorage）。
const LAYOUT_VERSION = 2

// 侧栏用固定像素宽度，把剩余空间全部留给中间工作区。
// 之前用 38/40/22 百分比，窗口一宽侧栏就跟着膨胀（2048 宽时左栏约 778px），
// 中间栏反而被挤到 819px，Agent 审阅/代码区两侧出现大片空白。
const DEFAULT_LAYOUT = { left: 240, right: 280 }
// 与 Panel 的 minSize 保持一致：历史数据里若存过 0 或过小的宽度，
// 恢复后会得到一个「已经是折叠态」的面板，与 leftPanelOpen 的 true 自相矛盾。
// 两侧默认即最小宽度（DEFAULT === MIN）：侧栏只放清单/文件列表，
// 窄一点能把空间让给中间工作区。
const MIN_LAYOUT = { left: 240, right: 280 }

function loadLayout(): { left: number; right: number } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      // 版本不符（旧版默认值 / 更早的百分比数组）直接丢弃，用当前默认值重建
      if (parsed?.v !== LAYOUT_VERSION) return DEFAULT_LAYOUT
      if (typeof parsed.left === 'number' && typeof parsed.right === 'number') {
        return {
          left: parsed.left >= MIN_LAYOUT.left ? parsed.left : DEFAULT_LAYOUT.left,
          right: parsed.right >= MIN_LAYOUT.right ? parsed.right : DEFAULT_LAYOUT.right,
        }
      }
    }
  } catch { /* ignore */ }
  return DEFAULT_LAYOUT
}

function saveLayout(layout: { left: number; right: number }) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ v: LAYOUT_VERSION, ...layout }))
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
    // 从后端恢复对话历史（SQLite 持久化）
    useChatStore.getState().loadMessagesFromApi()
    // 工作区工具清单在启动时就加载：状态栏 TOOLS 与左栏「工具空间」用的是同一份数据，
    // 不能等到用户切到「资源空间」才拉取，否则首屏会显示 0。
    useWorkspaceToolStore.getState().fetchFromApi()
  }, [loadTree, fetchAllResources])

  // Sync panel collapse/expand with TopBar toggle
  const prevLeftOpen = useRef(leftPanelOpen)
  const prevRightOpen = useRef(rightPanelOpen)

  // 记住展开时的实际宽度。react-resizable-panels 的 expand() 仅在面板尺寸
  // 严格等于 collapsedSize(0) 时生效；拖拽折叠后残留的亚像素宽度会让它
  // 变成空操作，表现为「点一次没反应、要再点一次」。改用 resize(宽度) 恢复。
  const lastLeftWidth = useRef(initialLayout.current.left)
  const lastRightWidth = useRef(initialLayout.current.right)

  // react-resizable-panels v4 已移除 onCollapse/onExpand，只能用 onResize
  // 判断真实折叠状态；否则拖拽折叠后面板已收起，而 leftPanelOpen 仍是 true，
  // 第一次点按钮反而把状态改成 false（看起来「没反应」）。
  const syncLeftOpen = useCallback(() => {
    const p = leftRef.current
    if (!p) return
    const open = !p.isCollapsed()
    const s = useUIStore.getState()
    if (s.leftPanelOpen !== open) s.setLeftPanelOpen(open)
  }, [leftRef])

  const syncRightOpen = useCallback(() => {
    const p = rightRef.current
    if (!p) return
    const open = !p.isCollapsed()
    const s = useUIStore.getState()
    if (s.rightPanelOpen !== open) s.setRightPanelOpen(open)
  }, [rightRef])

  useEffect(() => {
    if (prevLeftOpen.current === leftPanelOpen) return
    prevLeftOpen.current = leftPanelOpen
    if (leftPanelOpen) leftRef.current?.resize(lastLeftWidth.current)
    else leftRef.current?.collapse()
  }, [leftPanelOpen, leftRef])

  useEffect(() => {
    if (prevRightOpen.current === rightPanelOpen) return
    prevRightOpen.current = rightPanelOpen
    if (rightPanelOpen) rightRef.current?.resize(lastRightWidth.current)
    else rightRef.current?.collapse()
  }, [rightPanelOpen, rightRef])

  const handleLayoutChange = useCallback(() => {
    // 百分比会随窗口尺寸变化，存下来恢复会失真；这里直接读面板的实际像素宽度。
    // 折叠中的面板宽度为 0，此时保留上一次保存值，避免下次启动仍是收起状态。
    const left = leftRef.current?.isCollapsed() ? null : leftRef.current?.getSize().inPixels
    const right = rightRef.current?.isCollapsed() ? null : rightRef.current?.getSize().inPixels
    if (left && left >= MIN_LAYOUT.left) lastLeftWidth.current = Math.round(left)
    if (right && right >= MIN_LAYOUT.right) lastRightWidth.current = Math.round(right)
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
            onResize={syncLeftOpen}
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
            onResize={syncRightOpen}
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


import { create } from 'zustand'
import { useResourceStore } from '@/stores/resource-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import {
  devicesWsUrl,
  type DeviceSnapshot,
  type DeviceWsMessage,
  type GpuProviderKind,
  type HistoryPoint,
  type HostInfo,
} from '@/services/api/device'

/** 设备空间中的条目：CPU / 内存 / 第 n 块 GPU / 外存 */
export type DeviceKey = 'cpu' | 'memory' | 'disk' | `gpu-${number}`

const HISTORY_SECONDS = 600
const MAX_RETRY_MS = 15_000
const IDLE_CLOSE_MS = 1_000

interface DeviceState {
  connection: 'idle' | 'connecting' | 'open' | 'closed'
  host: HostInfo | null
  gpuProvider: GpuProviderKind | null
  driverVersion: string
  latest: DeviceSnapshot | null
  history: HistoryPoint[]
  selectedDevice: DeviceKey | null

  /** 订阅实时数据；多个组件共用一条 WebSocket，返回取消订阅函数 */
  subscribe: () => () => void
  selectDevice: (key: DeviceKey | null) => void
}

// WebSocket 连接是模块级单例：顶栏与设备空间同时订阅时只建一条连接
let ws: WebSocket | null = null
let refs = 0
let retryMs = 1_000
let retryTimer: ReturnType<typeof setTimeout> | undefined
let idleTimer: ReturnType<typeof setTimeout> | undefined

export const useDeviceStore = create<DeviceState>((set, get) => {
  const connect = () => {
    set({ connection: 'connecting' })
    const sock = new WebSocket(devicesWsUrl())
    ws = sock

    sock.onopen = () => {
      retryMs = 1_000
      set({ connection: 'open' })
    }
    sock.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as DeviceWsMessage
      if (msg.type === 'init') {
        set({
          host: msg.host,
          gpuProvider: msg.gpu_provider,
          driverVersion: msg.driver_version,
          latest: msg.latest,
          history: msg.history,
        })
      } else {
        const cutoff = msg.point.ts - HISTORY_SECONDS
        set({
          latest: msg.data,
          history: [...get().history.filter((p) => p.ts >= cutoff), msg.point],
        })
      }
    }
    sock.onclose = () => {
      if (ws !== sock) return // 已被主动替换 / 关闭
      ws = null
      set({ connection: 'closed' })
      if (refs > 0) {
        retryTimer = setTimeout(connect, retryMs)
        retryMs = Math.min(retryMs * 2, MAX_RETRY_MS)
      }
    }
  }

  const teardown = () => {
    clearTimeout(retryTimer)
    const sock = ws
    ws = null
    sock?.close()
    set({ connection: 'idle' })
  }

  return {
    connection: 'idle',
    host: null,
    gpuProvider: null,
    driverVersion: '',
    latest: null,
    history: [],
    selectedDevice: null,

    subscribe: () => {
      refs += 1
      clearTimeout(idleTimer)
      if (!ws && refs === 1) connect()
      return () => {
        refs -= 1
        // 延迟关闭：StrictMode 的二次挂载、切换面板时不反复重连
        if (refs === 0) idleTimer = setTimeout(teardown, IDLE_CLOSE_MS)
      }
    },

    selectDevice: (key) => {
      // 右栏同一时间只展示一个对象：选中设备时取消资源选中
      if (key) useResourceStore.getState().selectResource(null)
      set({ selectedDevice: key })
    },
  }
})

// 用户转而选中资源或文件时，右栏让位给它们
useResourceStore.subscribe((s, prev) => {
  if (s.selectedResource && s.selectedResource !== prev.selectedResource) {
    useDeviceStore.setState({ selectedDevice: null })
  }
})
useFileTreeStore.subscribe((s, prev) => {
  if (s.selectedFile && s.selectedFile !== prev.selectedFile) {
    useDeviceStore.setState({ selectedDevice: null })
  }
})

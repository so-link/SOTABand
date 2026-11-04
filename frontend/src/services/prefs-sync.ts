/**
 * 界面偏好的跨设备同步：localStorage 仍是本地缓存（保证首屏不闪），
 * 后端 `/api/auth/prefs`（落在当前用户自己的 platform.db）是权威源。
 *
 * 只在**本地存有令牌**时才读写后端。单用户模式（.env 未配置 SOTABAND_ROOT_PASSWORD）
 * 下匿名请求即超级用户，没有令牌，此时完全跳过同步——否则匿名 PUT /prefs 会 401，
 * 进而触发全局登出回调把用户踢出界面。
 *
 * 时序：登录后 load 一次（远端有值的 key 覆盖本地并应用到 store）→ 之后本地
 * 变化防抖 800ms 推送 → 登出立即停止并清空待推送队列（防止把上一个用户的
 * 偏好推给下一个用户）。任何失败静默忽略，只 console.warn。
 */
import { authApi, getStoredToken } from '@/services/api/auth'
import { useUIStore, type Theme } from '@/stores/ui-store'
import { useChatStore } from '@/stores/chat-store'

/** 后端约定的 key → 本地 localStorage key。顺序即推送顺序。 */
const ITEMS = [
  { key: 'ui.theme', ls: 'sotaband-theme', json: false },
  { key: 'ui.layout', ls: 'maia-layout', json: true },
  { key: 'ui.views', ls: 'sotaband-ui', json: true },
  { key: 'ui.expandedSections', ls: 'sotaband-expanded-sections', json: true },
  { key: 'chat.confirmPolicy', ls: 'sota.platform_confirm_policy', json: false },
] as const

const PUSH_DEBOUNCE_MS = 800
/** 本地写入没有事件可监听（同页签 storage 不触发），只能轮询快照比对。 */
const WATCH_INTERVAL_MS = 1500
const MAX_PUSH_RETRY = 3
/** 与后端 user_prefs.VALUE_MAX_CHARS 对齐，超了后端必然 400。 */
const VALUE_MAX_CHARS = 16_000

let timer: ReturnType<typeof setInterval> | null = null
let pushTimer: ReturnType<typeof setTimeout> | null = null
let pending: Record<string, unknown> = {}
let retries = 0
/** key → 上次成功同步（或已放弃）的本地序列化值，用于判断"本地变了"。 */
let snapshot: Record<string, string> = {}

function readLocal(item: (typeof ITEMS)[number]): unknown {
  try {
    const raw = localStorage.getItem(item.ls)
    if (raw === null || raw === '') return undefined
    if (!item.json) return raw
    try { return JSON.parse(raw) } catch { return undefined }
  } catch {
    return undefined
  }
}

function writeLocal(item: (typeof ITEMS)[number], value: unknown) {
  try {
    localStorage.setItem(item.ls, item.json ? JSON.stringify(value) : String(value))
  } catch { /* 隐私模式等写入失败不影响主流程 */ }
}

function serialize(item: (typeof ITEMS)[number]): string {
  const v = readLocal(item)
  if (v === undefined) return ''
  try { return item.json ? JSON.stringify(v) : String(v) } catch { return '' }
}

/** 远端值写进 localStorage 后，把它应用到内存中的 store。 */
function applyToStores(key: string, value: unknown) {
  try {
    switch (key) {
      case 'ui.theme': {
        if (value !== 'dark' && value !== 'light') return
        document.documentElement.setAttribute('data-theme', value)
        useUIStore.setState({ theme: value as Theme })
        break
      }
      case 'ui.views': {
        // zustand persist 存的整个 JSON：{ state: { activeView, openViews, leftPanelTab }, version }
        const state = (value as { state?: Record<string, unknown> })?.state
        if (!state) return
        const patch: Record<string, unknown> = {}
        if (typeof state.activeView === 'string') patch.activeView = state.activeView
        if (Array.isArray(state.openViews)) patch.openViews = state.openViews
        if (state.leftPanelTab === 'files' || state.leftPanelTab === 'resources') patch.leftPanelTab = state.leftPanelTab
        useUIStore.setState(patch as never)
        break
      }
      case 'chat.confirmPolicy': {
        if (value !== 'auto' && value !== 'manual') return
        useChatStore.setState({ confirmPolicy: value })
        break
      }
      // ui.layout / ui.expandedSections 由各自组件在挂载时读 localStorage，
      // 这里只落盘，下次刷新生效（load 早于组件挂载的时机才可能命中）。
      default:
        break
    }
  } catch (e) {
    console.warn('[prefs] 应用远端偏好失败', key, e)
  }
}

function takeDirty(): Record<string, unknown> {
  const dirty: Record<string, unknown> = {}
  for (const item of ITEMS) {
    const now = serialize(item)
    if (!now) continue
    if (snapshot[item.key] === now) continue
    if (now.length > VALUE_MAX_CHARS) {
      snapshot[item.key] = now // 记住"已放弃"，否则每轮都重新判定为脏
      console.warn('[prefs] 偏好过大，跳过同步', item.key)
      continue
    }
    snapshot[item.key] = now
    try {
      // 统一走 JSON 解析后的值；raw 类型（主题/确认策略）本身就是字符串
      dirty[item.key] = item.json ? JSON.parse(now) : now
    } catch { /* 解析失败保持脏状态，下轮再试 */ }
  }
  return dirty
}

function schedulePush() {
  if (pushTimer) clearTimeout(pushTimer)
  pushTimer = setTimeout(flush, PUSH_DEBOUNCE_MS)
}

async function flush() {
  pushTimer = null
  if (!getStoredToken()) return
  const payload = pending
  pending = {}
  if (Object.keys(payload).length === 0) { retries = 0; return }
  try {
    await authApi.putPrefs(payload)
    retries = 0
  } catch (e) {
    // 失败的值保留在 snapshot 之外即可在下一轮轮询重新入队；连续失败多次则放弃，
    // 避免后端异常时无限重推。
    if (++retries < MAX_PUSH_RETRY) {
      for (const k of Object.keys(payload)) delete snapshot[k]
      pending = { ...payload, ...pending }
      schedulePush()
    } else {
      console.warn('[prefs] 推送偏好失败，暂停重试', e)
    }
  }
}

async function load() {
  const { prefs } = await authApi.getPrefs()
  const remote = prefs && typeof prefs === 'object' ? prefs : {}
  const seed: Record<string, unknown> = {}
  for (const item of ITEMS) {
    const local = readLocal(item)
    if (remote[item.key] !== undefined) {
      // 远端是权威源：覆盖本地并应用。远端没有的 key 不动。
      writeLocal(item, remote[item.key])
      applyToStores(item.key, remote[item.key])
    } else if (local !== undefined) {
      // 首次登录（远端还没有这项）：把本地现值种进后端
      const text = serialize(item)
      if (text && text.length <= VALUE_MAX_CHARS) {
        snapshot[item.key] = text
        seed[item.key] = item.json ? local : text
      }
    }
  }
  if (Object.keys(seed).length > 0) {
    pending = { ...seed, ...pending }
    schedulePush()
  }
}

function tick() {
  if (!getStoredToken()) { stopPrefsSync(); return }
  const dirty = takeDirty()
  if (Object.keys(dirty).length === 0) return
  pending = { ...pending, ...dirty }
  schedulePush()
}

/** 登录成功后调用；无令牌（单用户模式）直接返回，不做任何请求。 */
export function startPrefsSync() {
  if (typeof window === 'undefined') return
  stopPrefsSync()
  if (!getStoredToken()) return
  snapshot = {}
  void load()
    .catch((e) => console.warn('[prefs] 拉取远端偏好失败', e))
    .finally(() => {
      // load 结束后再建快照：此时 localStorage 已含远端值，脏判定才准确
      if (!getStoredToken()) return
      snapshot = {}
      for (const item of ITEMS) snapshot[item.key] = serialize(item)
      if (timer) clearInterval(timer)
      timer = setInterval(tick, WATCH_INTERVAL_MS)
    })
}

/** 登出 / 令牌过期：停止轮询并丢弃未推送的偏好。 */
export function stopPrefsSync() {
  if (timer) { clearInterval(timer); timer = null }
  if (pushTimer) { clearTimeout(pushTimer); pushTimer = null }
  pending = {}
  snapshot = {}
  retries = 0
}

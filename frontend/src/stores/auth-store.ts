import { create } from 'zustand'
import {
  authApi, getStoredToken, setStoredToken, getSuperToken, setSuperToken,
  setOnUnauthorized, type AuthUser,
} from '@/services/api/auth'
import { startPrefsSync, stopPrefsSync } from '@/services/prefs-sync'
import { useUIStore } from '@/stores/ui-store'

interface AuthState {
  user: AuthUser | null
  checked: boolean
  loginOpen: boolean
  error: string
  busy: boolean
  /** 代管模式：正在以系统用户（uid=100）短时令牌操作，横幅据此显示。 */
  impersonating: boolean
  /** 代管令牌本地推算的到期时间戳（ms），仅供横幅显示剩余时间。 */
  impersonateExpiresAt: number | null

  refresh: () => Promise<void>
  login: (username: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  changePassword: (oldPw: string, newPw: string) => Promise<boolean>
  updateDisplayName: (name: string) => Promise<boolean>
  openLogin: () => void
  closeLogin: () => void
  /** 未登录时弹登录框并返回 false；已登录返回 true。用于拦截上传/导入/新建等操作入口。 */
  requireLogin: () => boolean
  /** 切换到系统用户身份（代管公共资源）。失败抛错由调用方展示。 */
  impersonateSystem: () => Promise<void>
  /** 退出代管，切回超管（单用户模式回到匿名超管）。可并发安全重入。 */
  exitImpersonate: () => Promise<void>
}

/** 退出代管的并发去重：401 回调可能在同一时刻被多个失败请求同时触发。 */
let exitInFlight: Promise<void> | null = null

/**
 * 清除所有「按上一个账号」加载的运行时数据。
 *
 * 登出/令牌失效时调用：文件树、资源列表、任务历史、工具空间、仓库、
 * 对话与编辑草稿都带着旧账号的内容，换人后必须丢弃；否则新账号一进门
 * 就看到了别人的文件与对话。用户偏好（主题/布局）不属于业务数据，不清理。
 */
async function clearUserScopedData(): Promise<void> {
  useUIStore.getState().closeAllViews()
  try {
    const [
      { useChatStore },
      { useFileTreeStore },
      { useResourceStore },
      { useWorkspaceToolStore },
      { useRepositoryStore },
      { useDatasetRepositoryStore },
      { useToolEditorStore },
      { useDatasetEditorStore },
      { useAgentEditorStore },
    ] = await Promise.all([
      import('@/stores/chat-store'),
      import('@/stores/file-tree-store'),
      import('@/stores/resource-store'),
      import('@/stores/workspace-tool-store'),
      import('@/stores/repository-store'),
      import('@/stores/dataset-repository-store'),
      import('@/stores/tool-editor-store'),
      import('@/stores/dataset-editor-store'),
      import('@/stores/agent-editor-store'),
    ])
    useChatStore.getState().resetForUserSwitch()
    useFileTreeStore.getState().resetForUserSwitch()
    useResourceStore.getState().resetForUserSwitch()
    useWorkspaceToolStore.getState().resetForUserSwitch()
    useRepositoryStore.getState().resetForUserSwitch()
    useDatasetRepositoryStore.getState().resetForUserSwitch()
    useToolEditorStore.getState().reset()
    useDatasetEditorStore.getState().reset()
    useAgentEditorStore.getState().reset()
  } catch { /* 单个 store 清理失败不阻断登出 */ }
}

/** 按新账号重新拉取首屏数据（登录成功后调用，此时令牌已是新用户的）。 */
async function reloadUserScopedData(): Promise<void> {
  try {
    const [
      { useChatStore },
      { useFileTreeStore },
      { useResourceStore },
      { useWorkspaceToolStore },
    ] = await Promise.all([
      import('@/stores/chat-store'),
      import('@/stores/file-tree-store'),
      import('@/stores/resource-store'),
      import('@/stores/workspace-tool-store'),
    ])
    void useFileTreeStore.getState().loadTree()
    void useResourceStore.getState().fetchAllResources()
    void useWorkspaceToolStore.getState().fetchFromApi()
    void useChatStore.getState().loadMessagesFromApi()
  } catch { /* 拉取失败时界面保持空态，可手动刷新 */ }
}

/** 令牌换血后的统一收尾：丢旧账号数据 → 拉新身份 → 按新身份重灌首屏。
 *  login / impersonate / exitImpersonate 三条换身份路径共用。 */
async function swapIdentity(): Promise<void> {
  await clearUserScopedData()
  await useAuthStore.getState().refresh()
  void reloadUserScopedData()
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  checked: false,
  loginOpen: false,
  error: '',
  busy: false,
  impersonating: false,
  impersonateExpiresAt: null,

  async refresh() {
    try {
      const me = await authApi.me()
      const user = me.authenticated && me.user ? me.user : null
      // 配了管理员密码却拿不到身份（刷新后本地没有令牌 / 令牌已失效）：
      // 首屏直接把登录框摆出来，而不是让人对着一片空界面自己找登录入口。
      // 没配密码时后端匿名即返回超管身份（user 非空），刷新后自动登录。
      const mustLogin = !user && me.superuser_configured === true
      // 页面刷新后内存态丢失：代管标记从 /me 回灌（expiresAt 无法得知，
      // 横幅退化为不显示剩余时间）。
      set({
        user, checked: true, loginOpen: mustLogin,
        error: mustLogin ? '请先登录' : '',
        impersonating: !!user?.acting_as_system,
      })
      // 已配置管理员密码且未登录：清空所有按账号加载的内容与标签，首屏保持空。
      // 单用户模式（未配密码）后端会直接返回超管身份，不会走到这里。
      if (!user) await clearUserScopedData()
    } catch {
      set({ user: null, checked: true })
    }
  },

  async login(username, password) {
    set({ busy: true, error: '' })
    try {
      const { token, user } = await authApi.login(username, password)
      setStoredToken(token)
      // 换人登录：先丢掉上一账号的列表/草稿/对话，再按新身份拉取首屏数据
      await clearUserScopedData()
      set({ user, busy: false, loginOpen: false })
      void reloadUserScopedData()
      return true
    } catch (e) {
      set({ busy: false, error: e instanceof Error ? e.message : '登录失败' })
      return false
    }
  },

  async logout() {
    stopPrefsSync()
    await authApi.logout()
    setStoredToken('')
    // 代管中直接登出：超管旁槽令牌一并作废，否则会残留可复用的旧会话
    setSuperToken('')
    // 登出即换人：所有已打开标签与按旧账号加载的数据全部丢弃；
    // 注意顺序——先 stopPrefsSync 再清理，清理动作不会作为上一用户偏好推给服务端。
    await clearUserScopedData()
    set({ user: null, loginOpen: true, error: '', impersonating: false, impersonateExpiresAt: null })
  },

  async changePassword(oldPw, newPw) {
    set({ error: '' })
    try {
      await authApi.changePassword(oldPw, newPw)
      return true
    } catch (e) {
      set({ error: e instanceof Error ? e.message : '修改密码失败' })
      return false
    }
  },

  async updateDisplayName(name) {
    set({ error: '' })
    try {
      const { user } = await authApi.updateProfile(name)
      // 后端返回用户表原始条目（snake_case 的 display_name），统一按 /me 的
      // camelCase 口径合并进本地 user，否则读到的永远是 undefined。
      const updated = (user as { display_name?: string }).display_name ?? user.displayName ?? name
      set((s) => ({
        user: s.user ? { ...s.user, username: user.username ?? s.user.username, displayName: updated } : s.user,
      }))
      return true
    } catch (e) {
      set({ error: e instanceof Error ? e.message : '修改显示名失败' })
      return false
    }
  },

  async impersonateSystem() {
    const r = await authApi.actAsSystem()
    // 超管令牌挪进旁槽暂存（单用户模式没有令牌则为空），活跃槽换发系统令牌
    setSuperToken(getStoredToken())
    setStoredToken(r.token)
    set({ impersonating: true, impersonateExpiresAt: Date.now() + r.expires_in * 1000 })
    await swapIdentity()
  },

  async exitImpersonate() {
    if (exitInFlight) return exitInFlight
    exitInFlight = (async () => {
      // 代管令牌作废直接丢；超管令牌从旁槽迁回（单用户模式迁回空 = 匿名超管）
      setStoredToken(getSuperToken())
      setSuperToken('')
      set({ impersonating: false, impersonateExpiresAt: null })
      await swapIdentity()
    })()
    try { await exitInFlight } finally { exitInFlight = null }
  },

  openLogin: () => set({ loginOpen: true, error: '' }),
  closeLogin: () => set({ loginOpen: false, error: '' }),

  requireLogin: () => {
    if (get().user) return true
    set({ loginOpen: true, error: '' })
    return false
  },
}))

// 身份变化驱动偏好同步：拿到身份且有令牌（多用户模式）才开启；
// 身份消失（登出/令牌过期）立即停止并丢弃待推送队列，避免把上一个用户
// 的偏好推给下一个用户。单用户模式没有令牌，startPrefsSync 内部直接跳过。
// 代管态（acting_as_system）同样必须停：否则超管在代管期间的界面改动
// 会被推到系统用户（uid=100）的库里，退出后超管自己的偏好却被推平。
let syncedUid: string | null = null
useAuthStore.subscribe((s) => {
  const uid = s.user && !s.user.acting_as_system ? s.user.id : null
  if (uid === syncedUid) return
  syncedUid = uid
  if (uid) startPrefsSync()
  else stopPrefsSync()
})

// 令牌过期或未登录操作（401）时全局弹出登录框；启动时拉取一次当前身份。
setOnUnauthorized(({ expired }) => {
  // 代管态的 401 = 系统短时令牌过期/失效：自动退回超管，而不是把人踢下线
  if (getSuperToken()) {
    void useAuthStore.getState().exitImpersonate().then(() => {
      useAuthStore.setState({ error: '代管已过期，已切回超级用户身份' })
    })
    return
  }
  const hadToken = !!getStoredToken()
  stopPrefsSync()
  setStoredToken('')
  syncedUid = null
  // 与主动登出同口径：先清掉按旧账号加载的数据，再弹登录框
  void clearUserScopedData()
  useAuthStore.setState({
    user: null,
    loginOpen: true,
    error: expired || hadToken ? '登录已过期，请重新登录' : '请先登录后再操作',
  })
})

if (typeof window !== 'undefined') {
  void useAuthStore.getState().refresh()
}

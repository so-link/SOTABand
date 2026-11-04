import { create } from 'zustand'
import { authApi, setStoredToken, setOnUnauthorized, type AuthUser } from '@/services/api/auth'
import { startPrefsSync, stopPrefsSync } from '@/services/prefs-sync'

interface AuthState {
  user: AuthUser | null
  checked: boolean
  loginOpen: boolean
  error: string
  busy: boolean

  refresh: () => Promise<void>
  login: (username: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  changePassword: (oldPw: string, newPw: string) => Promise<boolean>
  updateDisplayName: (name: string) => Promise<boolean>
  openLogin: () => void
  closeLogin: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  checked: false,
  loginOpen: false,
  error: '',
  busy: false,

  async refresh() {
    try {
      const me = await authApi.me()
      set({ user: me.authenticated && me.user ? me.user : null, checked: true })
    } catch {
      set({ user: null, checked: true })
    }
  },

  async login(username, password) {
    set({ busy: true, error: '' })
    try {
      const { token, user } = await authApi.login(username, password)
      setStoredToken(token)
      set({ user, busy: false, loginOpen: false })
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
    set({ user: null })
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

  openLogin: () => set({ loginOpen: true, error: '' }),
  closeLogin: () => set({ loginOpen: false, error: '' }),
}))

// 身份变化驱动偏好同步：拿到身份且有令牌（多用户模式）才开启；
// 身份消失（登出/令牌过期）立即停止并丢弃待推送队列，避免把上一个用户
// 的偏好推给下一个用户。单用户模式没有令牌，startPrefsSync 内部直接跳过。
let syncedUid: string | null = null
useAuthStore.subscribe((s) => {
  const uid = s.user?.id ?? null
  if (uid === syncedUid) return
  syncedUid = uid
  if (uid) startPrefsSync()
  else stopPrefsSync()
})

// 令牌过期（401）时全局弹出登录框；启动时拉取一次当前身份。
setOnUnauthorized(() => {
  stopPrefsSync()
  setStoredToken('')
  syncedUid = null
  useAuthStore.setState({ user: null, loginOpen: true, error: '登录已过期，请重新登录' })
})

if (typeof window !== 'undefined') {
  void useAuthStore.getState().refresh()
}

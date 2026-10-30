import { create } from 'zustand'
import { authApi, setStoredToken, setOnUnauthorized, type AuthUser } from '@/services/api/auth'

interface AuthState {
  user: AuthUser | null
  checked: boolean
  loginOpen: boolean
  error: string
  busy: boolean

  refresh: () => Promise<void>
  login: (username: string, password: string) => Promise<boolean>
  register: (username: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  openLogin: () => void
  closeLogin: () => void
}

export const useAuthStore = create<AuthState>((set, get) => ({
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

  async register(username, password) {
    set({ busy: true, error: '' })
    try {
      await authApi.register(username, password)
      set({ busy: false })
      return await get().login(username, password)
    } catch (e) {
      set({ busy: false, error: e instanceof Error ? e.message : '注册失败' })
      return false
    }
  },

  async logout() {
    await authApi.logout()
    setStoredToken('')
    set({ user: null })
  },

  openLogin: () => set({ loginOpen: true, error: '' }),
  closeLogin: () => set({ loginOpen: false, error: '' }),
}))

// 令牌过期（401）时全局弹出登录框；启动时拉取一次当前身份。
setOnUnauthorized(() => {
  setStoredToken('')
  useAuthStore.setState({ user: null, loginOpen: true, error: '登录已过期，请重新登录' })
})

if (typeof window !== 'undefined') {
  void useAuthStore.getState().refresh()
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

export interface AuthUser {
  id: string
  username: string
  role: string
  displayName?: string
  /** 超级用户（凭据来自 .env）为 true，界面上只读。 */
  builtin?: boolean
}

/** /users 列表里的用户对象——注意后端这里是 snake_case 的 display_name。 */
export interface ManagedUser {
  id: string
  username: string
  display_name?: string
  role: string
  builtin?: boolean
  created_at?: string
}

export interface MeResponse {
  authenticated: boolean
  user?: AuthUser
  identity?: string
  superuser_configured?: boolean
}

const TOKEN_KEY = 'sotaband-token'

export function getStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setStoredToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {}
}

let onUnauthorized: (() => void) | null = null

/** 注册 401 回调（登录过期时由 store 挂上：清令牌、弹登录框）。 */
export function setOnUnauthorized(cb: (() => void) | null) {
  onUnauthorized = cb
}

// 全局 fetch 补丁：只要本地存有令牌，所有 /api 请求自动携带
// Authorization 头——避免逐个改造既有 fetch 调用点。401 时触发全局回调
// （登录过期处理），auth 端点除外：其 401/403 由登录弹窗与账号页内联展示，
// 不弹全局过期框。
if (typeof window !== 'undefined') {
  const origFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : (input as Request).url
    const token = getStoredToken()
    if (token && url.includes('/api/')) {
      const headers = new Headers(init?.headers)
      if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
      init = { ...(init ?? {}), headers }
    }
    const res = await origFetch(input as RequestInfo, init)
    if (res.status === 401 && token && !url.includes('/api/auth/')) onUnauthorized?.()
    return res
  }
}

/** 统一带 JSON body 的请求；错误取 FastAPI 的 detail 文案。 */
async function request(method: string, path: string, body?: unknown) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data as { detail?: string }).detail || `HTTP ${res.status}`)
  return data
}

const post = (path: string, body: unknown) => request('POST', path, body)
const put = (path: string, body: unknown) => request('PUT', path, body)

export const authApi = {
  async me(): Promise<MeResponse> {
    const res = await fetch(`${BASE_URL}/api/auth/me`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },
  async login(username: string, password: string): Promise<{ token: string; user: AuthUser }> {
    return post('/api/auth/login', { username, password })
  },
  async logout() {
    try { await post('/api/auth/logout', {}) } catch { /* 无状态令牌，本地清理即可 */ }
  },
  /** 改自己的显示名（超管 400）。 */
  async updateProfile(displayName: string): Promise<{ user: AuthUser }> {
    return put('/api/auth/me', { displayName })
  },
  /** 改自己的密码（需原密码；超管 400）。 */
  async changePassword(oldPassword: string, newPassword: string): Promise<{ ok: boolean }> {
    return post('/api/auth/password', { oldPassword, newPassword })
  },
  async getPrefs(): Promise<{ prefs: Record<string, unknown> }> {
    return request('GET', '/api/auth/prefs')
  },
  /**
   * 写偏好。请求体是**裸的扁平对象**（`{"ui.theme":"light"}`），后端直接把它
   * 当 prefs 解析，不要再包一层 `{prefs:...}`。
   */
  async putPrefs(values: Record<string, unknown>): Promise<{ saved: string[] }> {
    return put('/api/auth/prefs', values)
  },

  // ── 用户管理（仅超级用户，普通用户 403） ──

  async listUsers(): Promise<{ users: ManagedUser[] }> {
    return request('GET', '/api/auth/users')
  },
  async createUser(username: string, password: string, displayName = ''): Promise<{ user: ManagedUser }> {
    return post('/api/auth/users', { username, password, displayName })
  },
  async renameUser(uid: string, username: string): Promise<{ user: ManagedUser }> {
    return put(`/api/auth/users/${encodeURIComponent(uid)}`, { username })
  },
  async setUserDisplayName(uid: string, displayName: string): Promise<{ user: ManagedUser }> {
    return put(`/api/auth/users/${encodeURIComponent(uid)}/display-name`, { displayName })
  },
  async resetUserPassword(uid: string, password: string): Promise<{ ok: boolean }> {
    return put(`/api/auth/users/${encodeURIComponent(uid)}/password`, { password })
  },
  /** 删除账号；后端**不删用户目录**，返回 home_kept 告知保留位置。 */
  async deleteUser(uid: string): Promise<{ ok: boolean; id: string; home_kept: string }> {
    return request('DELETE', `/api/auth/users/${encodeURIComponent(uid)}`)
  },
}

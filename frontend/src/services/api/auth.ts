const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

export interface AuthUser {
  id: string
  username: string
  role: string
}

export interface MeResponse {
  authenticated: boolean
  user?: AuthUser
  identity?: string
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
// Authorization 头——避免逐个改造既有 fetch 调用点（含 /api/auth/register，
// 管理员添加用户需带身份）。401 时触发全局回调（登录过期处理），
// auth 端点除外：其 401/403 由登录弹窗内联展示，不弹全局过期框。
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

async function post(path: string, body: unknown) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data as { detail?: string }).detail || `HTTP ${res.status}`)
  return data
}

export const authApi = {
  async me(): Promise<MeResponse> {
    const res = await fetch(`${BASE_URL}/api/auth/me`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },
  async login(username: string, password: string): Promise<{ token: string; user: AuthUser }> {
    return post('/api/auth/login', { username, password })
  },
  async register(username: string, password: string, displayName = ''): Promise<{ user: AuthUser }> {
    return post('/api/auth/register', { username, password, displayName })
  },
  async logout() {
    try { await post('/api/auth/logout', {}) } catch { /* 无状态令牌，本地清理即可 */ }
  },
}

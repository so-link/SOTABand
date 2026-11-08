/**
 * 认证状态机（契约 §7.1）。
 *
 * ```text
 * unknown ──探测 /api/auth/session──→ authenticated | unauthenticated
 *                                        │
 *                       401 ────────────┴──→ expired → 跳登录（保留提示）
 * ```
 *
 * ## `unknown` 时不能渲染受保护内容
 *
 * PRD FR-A7 明确要求「受保护页面无未登录闪现」。所以 `App.tsx` 在 `unknown`
 * 阶段渲染的是骨架屏，而不是先画主界面再跳走。
 *
 * ## 为什么没有路由
 *
 * 项目没有 react-router（`App.tsx` 原本只有 5 行，直接渲染 `<MainLayout/>`）。
 * 加路由库只为一个登录页不值得 —— 用状态机在 `App.tsx` 做分流更贴合现有风格。
 */

import { create } from 'zustand'

import { authApi } from '@/services/api/auth'
import { describeError, setUnauthorizedHandler } from '@/services/api/client'
import type { AuthStatus, CurrentUser, LoginPayload, RegisterPayload } from '@/types/auth'

interface AuthState {
  status: AuthStatus
  user: CurrentUser | null
  /** 展示给用户的错误信息（登录失败、会话过期提示等） */
  error: string | null
  /** 提交中：按钮禁用，防重复提交 */
  submitting: boolean

  bootstrap: () => Promise<void>
  login: (payload: LoginPayload) => Promise<boolean>
  register: (payload: RegisterPayload) => Promise<boolean>
  logout: () => Promise<void>
  logoutAll: () => Promise<void>
  /**
   * 重新拉 `/api/v1/me`，刷新当前用户快照。
   *
   * 切换激活租户后必须调用 —— `activeTenantId` / `tenantRole` / `tenants`
   * 都在这个快照里，不刷新的话 UserMenu 会一直显示旧空间。
   *
   * 返回是否刷新成功。**调用方必须处理 false**：旧快照里的 `tenantRole`
   * 可能已经和服务端不一致，继续按它渲染就会"服务端会拒、前端却放行"。
   */
  refresh: () => Promise<boolean>
  clearError: () => void
}

/**
 * 防重复探测的模块级锁。
 *
 * React 18 的 StrictMode 在开发模式下会把 effect 执行两次，`bootstrap` 会被
 * 调用两遍 —— 若不合并，每次启动都会多打一遍 `/session` + `/me`。
 */
let inflightBootstrap: Promise<void> | null = null

export const useAuthStore = create<AuthState>((set, get) => ({
  status: 'unknown',
  user: null,
  error: null,
  submitting: false,

  /**
   * 应用启动时探测一次会话。
   *
   * 只有两种情况会走到"未认证"：探测说没登录，或探测本身失败（后端没起）。
   * 后者也归为未认证 —— 否则后端故障时前端会卡在骨架屏。
   */
  async bootstrap() {
    if (get().status !== 'unknown') return
    if (inflightBootstrap) return inflightBootstrap

    inflightBootstrap = (async () => {
      try {
        const probe = await authApi.probe()
        if (!probe.authenticated) {
          set({ status: 'unauthenticated', user: null })
          return
        }
        const me = await authApi.me()
        set({ status: 'authenticated', user: me })
      } catch {
        set({ status: 'unauthenticated', user: null })
      } finally {
        inflightBootstrap = null
      }
    })()

    return inflightBootstrap
  },

  async login(payload) {
    set({ submitting: true, error: null })
    try {
      await authApi.login(payload)
      // 登录接口只返回简要信息，再拉一次 /me 拿到统一结构（租户列表等）
      const me = await authApi.me()
      set({ status: 'authenticated', user: me, submitting: false, error: null })
      return true
    } catch (err) {
      set({ submitting: false, error: describeError(err) })
      return false
    }
  },

  async register(payload) {
    set({ submitting: true, error: null })
    try {
      await authApi.register(payload)
      const me = await authApi.me()
      set({ status: 'authenticated', user: me, submitting: false, error: null })
      return true
    } catch (err) {
      set({ submitting: false, error: describeError(err) })
      return false
    }
  },

  async logout() {
    await authApi.logout()
    set({ status: 'unauthenticated', user: null, error: null })
  },

  async logoutAll() {
    await authApi.logoutAll()
    set({ status: 'unauthenticated', user: null, error: null })
  },

  async refresh() {
    // 未登录时刷新没有意义，还可能把 unauthenticated 覆盖成别的东西
    if (get().status !== 'authenticated') return false
    try {
      const me = await authApi.me()
      set({ user: me })
      return true
    } catch {
      // 401 已由全局处理器切到 expired；其它错误（网络 / 5xx）以前被静默吞掉，
      // 调用方以为刷新成功 → 继续用旧快照里的角色渲染。现在如实返回 false，
      // 由调用方决定回退（见 collaboration-store 的 switchTenant）。
      return false
    }
  },

  clearError() {
    set({ error: null })
  },
}))

// ── 全局 401 处理 ─────────────────────────────────────────────────────
//
// 任何走 `apiFetch` 的请求返回 401 时把状态机切到 expired。
// 只在"本来是已登录"时才切 —— 登录页上的 401 是密码错，不该覆盖状态。
setUnauthorizedHandler(() => {
  if (useAuthStore.getState().status === 'authenticated') {
    useAuthStore.setState({
      status: 'expired',
      user: null,
      error: '会话已过期，请重新登录',
    })
  }
})

// ── 派生选择器（避免组件里重复写解构）──────────────────────────────

export const selectIsAuthenticated = (s: AuthState) => s.status === 'authenticated'
export const selectDisplayName = (s: AuthState) =>
  s.user?.displayName || s.user?.email || '用户'
export const selectTenantRole = (s: AuthState) => s.user?.tenantRole ?? null

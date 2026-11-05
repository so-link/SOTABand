/**
 * 认证相关类型 —— 对应 docs/contract-08-auth-network.md §1.4 的冻结契约。
 *
 * ⚠️ 字段用 **camelCase**，与后端返回的 snake_case 不同。映射集中在
 * `services/api/auth.ts`，这样后端命名变化不会渗透到组件里。
 */

/** 租户角色（契约 §6 的角色词表） */
export type TenantRole = 'owner' | 'admin' | 'member' | 'viewer'

/** 网络角色与租户角色当前同词表（Step 4 会用到） */
export type NetworkRole = TenantRole

/**
 * 认证状态机（契约 §7.1）。
 *
 * - `unknown`：尚未探测 —— **此时不得渲染任何受保护内容**，否则会出现
 *   "未登录内容闪现"（PRD FR-A7 明确要求）；
 * - `expired`：会话曾有效但已失效 —— 应提示"会话已过期"而非普通未登录。
 */
export type AuthStatus =
  | 'unknown'
  | 'authenticated'
  | 'unauthenticated'
  | 'expired'
  | 'disabled'

export interface TenantSummary {
  id: string
  name: string
  slug: string
  role: TenantRole
}

export interface UserSummary {
  id: string
  email: string | null
  displayName: string
  avatarUrl: string | null
}

/** 当前登录用户（`GET /api/v1/me`） */
export interface CurrentUser {
  userId: string
  email: string | null
  displayName: string | null
  activeTenantId: string
  tenantRole: TenantRole
  tenants: TenantSummary[]
  /**
   * 平台管理员（跨租户的运维身份，与 `tenantRole` 是两套判定）。
   *
   * 全局配置（`GET/POST /api/config/llm`）只对平台管理员开放 —— 租户
   * Owner/Admin 也不行（契约改为「只有平台管理员」）。字段缺失时按 `false`
   * 处理（fail-closed：不显示配置页），真正的拦截仍在后端。
   */
  isPlatformAdmin: boolean
}

/** `GET /api/auth/session` 的探测结果 —— 未登录也是 200 */
export interface SessionProbe {
  authenticated: boolean
  activeTenantId: string | null
  tenantRole: TenantRole | null
}

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload {
  email: string
  password: string
  displayName?: string
}

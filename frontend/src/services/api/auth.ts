/**
 * 认证 API —— `/api/auth/*` 与 `/api/v1/me`。
 *
 * 契约见 `docs/contract-08-auth-network.md` §3.1。
 *
 * ## 后端的字段命名不一致，映射集中在这一层
 *
 * 后端实际返回混用了两种命名：
 *
 * | 接口 | 命名 |
 * |---|---|
 * | `POST /api/auth/register` | snake_case（`display_name`） |
 * | `POST /api/auth/login` | 混合（`activeTenantId` 是 camelCase） |
 * | `GET /api/auth/session` | snake_case |
 * | `GET /api/v1/me` | snake_case |
 *
 * 前端统一用契约 §1.4 定义的 **camelCase** `CurrentUser`，所以映射只写在这里。
 * 好处是后端将来统一命名时，只需要改这一个文件。
 */

import { apiFetch, apiGet, apiPost } from './client'
import type {
  CurrentUser,
  LoginPayload,
  RegisterPayload,
  SessionProbe,
  TenantRole,
  TenantSummary,
} from '@/types/auth'

// ── 后端原始结构（仅供参考，不要泄漏到组件层）────────────────────────

interface RawTenant {
  id: string
  name: string
  slug: string
  role: TenantRole
}

interface RawMe {
  user_id: string
  email: string | null
  display_name: string | null
  active_tenant_id: string
  tenant_role: TenantRole
  tenants?: RawTenant[]
  /** 平台管理员标记。旧部署没有该字段 —— 缺失按 false 处理 */
  is_platform_admin?: boolean
}

interface RawSessionProbe {
  authenticated: boolean
  active_tenant_id: string | null
  tenant_role: TenantRole | null
}

// ── 映射 ──────────────────────────────────────────────────────────────

function mapTenant(raw: RawTenant): TenantSummary {
  return { id: raw.id, name: raw.name, slug: raw.slug, role: raw.role }
}

function mapMe(raw: RawMe): CurrentUser {
  return {
    userId: raw.user_id,
    email: raw.email ?? null,
    displayName: raw.display_name ?? null,
    activeTenantId: raw.active_tenant_id,
    tenantRole: raw.tenant_role,
    tenants: (raw.tenants ?? []).map(mapTenant),
    // 严格等于 true：字段缺失（旧响应）或不是布尔都按「非平台管理员」处理
    isPlatformAdmin: raw.is_platform_admin === true,
  }
}

// ── API ───────────────────────────────────────────────────────────────

export const authApi = {
  /**
   * 注册。成功后后端会直接下发会话 Cookie（业务上等于已登录）。
   *
   * `skipAuthRedirect`：这个接口的 401 不可能发生，但 409（邮箱已注册）等业务
   * 失败不该触发全局"会话过期"逻辑 —— 保险起见统一跳过。
   */
  async register(payload: RegisterPayload): Promise<void> {
    await apiPost('/api/auth/register', payload, { skipAuthRedirect: true })
  },

  /** 登录。失败抛 ApiError(401)，detail 统一为「邮箱或密码不正确」。 */
  async login(payload: LoginPayload): Promise<void> {
    await apiPost('/api/auth/login', payload, { skipAuthRedirect: true })
  },

  /** 退出当前设备（幂等）。即使已失效也不应报错打扰用户。 */
  async logout(): Promise<void> {
    try {
      await apiPost('/api/auth/logout', undefined, { skipAuthRedirect: true })
    } catch {
      // 登出失败不应阻塞用户 —— 本地状态照样清空
    }
  },

  /** 退出全部设备。 */
  async logoutAll(): Promise<void> {
    try {
      await apiPost('/api/auth/logout-all', undefined, { skipAuthRedirect: true })
    } catch {
      // 同上
    }
  },

  /**
   * 会话探测。**未登录返回 200 + authenticated:false**（契约 §3.1 特意要求），
   * 所以这里不会抛 401 —— 前端状态机可以放心调用。
   */
  async probe(): Promise<SessionProbe> {
    const raw = await apiGet<RawSessionProbe>('/api/auth/session')
    return {
      authenticated: raw.authenticated,
      activeTenantId: raw.active_tenant_id,
      tenantRole: raw.tenant_role,
    }
  },

  /** 当前用户 + 激活租户 + 所属租户列表。未登录抛 ApiError(401)。 */
  async me(): Promise<CurrentUser> {
    const raw = await apiFetch<RawMe>('/api/v1/me')
    return mapMe(raw)
  },

  /**
   * 修改密码（PRD FR-A5）。
   *
   * ⚠️ 成功后后端会**吊销该用户全部会话**（含当前这条），所以调用方必须紧接着
   * 把前端状态机切到未登录 —— 否则下一次请求必然 401。
   *
   * 「当前密码不正确」返回 **400 而不是 401**（后端刻意如此）：401 会触发全局
   * 处理器把用户登出，打个错密码就被踢下线是错的。这里同样 skipAuthRedirect
   * 以免任何意外 401 触发全局登出。
   */
  async changePassword(payload: {
    currentPassword: string
    newPassword: string
  }): Promise<{ revokedSessions: number; message: string }> {
    const raw = await apiPost<{ revokedSessions: number; message: string }>(
      '/api/v1/me/password',
      payload,
      { skipAuthRedirect: true }
    )
    return raw
  },
}

/**
 * 租户 API —— `/api/v1/tenants/*` 与 `/api/v1/me/*tenant*`（需求 8.1 / FR-A6）。
 *
 * 契约见 `docs/contract-08-auth-network.md` §3.1。
 *
 * ## 背景：为什么前端需要这个
 *
 * 注册时每人自动获得一个个人租户，而协作网络**属且仅属一个租户**。所以两个人要
 * 共享网络，必须先同租户 —— 这就是「邀请加入空间」+「切换激活空间」的意义。
 */

import { apiDelete, apiFetch, apiGet, apiPost } from './client'
import type { TenantRole, TenantSummary } from '@/types/auth'

// ── 类型 ──────────────────────────────────────────────────────────────

export interface TenantDetail {
  id: string
  name: string
  slug: string
  status: string
  role: TenantRole
  createdAt: string
}

export interface TenantMember {
  userId: string
  displayName: string | null
  email: string | null
  role: TenantRole
  joinedAt: string
}

export interface TenantInvitation {
  id: string
  email: string
  status: string
  expiresAt: string
  createdAt: string
}

export interface CreatedTenantInvitation {
  invitation: TenantInvitation
  /** **明文令牌只此一次可见** —— 库里只存 hash，务必立即展示或复制 */
  token: string
  acceptPath: string
}

// ── 后端原始结构（snake_case）───────────────────────────────────────

interface RawTenantDetail {
  id: string
  name: string
  slug: string
  status: string
  role: TenantRole
  created_at: string
}

interface RawTenantMember {
  user_id: string
  display_name: string | null
  email: string | null
  role: TenantRole
  joined_at: string
}

interface RawTenantInvitation {
  id: string
  email: string
  status: string
  expires_at: string
  created_at: string
}

interface RawCreatedInvitation {
  invitation: RawTenantInvitation
  token: string
  accept_path: string
}

// ── 映射 ──────────────────────────────────────────────────────────────

function mapTenantDetail(raw: RawTenantDetail): TenantDetail {
  return {
    id: raw.id,
    name: raw.name,
    slug: raw.slug,
    status: raw.status,
    role: raw.role,
    createdAt: raw.created_at,
  }
}

function mapMember(raw: RawTenantMember): TenantMember {
  return {
    userId: raw.user_id,
    displayName: raw.display_name,
    email: raw.email,
    role: raw.role,
    joinedAt: raw.joined_at,
  }
}

function mapInvitation(raw: RawTenantInvitation): TenantInvitation {
  return {
    id: raw.id,
    email: raw.email,
    status: raw.status,
    expiresAt: raw.expires_at,
    createdAt: raw.created_at,
  }
}

// ── API ───────────────────────────────────────────────────────────────

export const tenantApi = {
  /** 我所属的全部租户（`GET /api/v1/me/tenants`）。 */
  async listMine(): Promise<TenantSummary[]> {
    return apiGet<TenantSummary[]>('/api/v1/me/tenants')
  },

  /** 创建租户。**不会自动切换激活租户** —— 需要再调 `switchTo`。 */
  async create(name: string): Promise<TenantDetail> {
    const raw = await apiPost<RawTenantDetail>('/api/v1/tenants', { name })
    return mapTenantDetail(raw)
  },

  /** 切换当前会话的激活租户（服务端校验 membership）。 */
  async switchTo(tenantId: string): Promise<TenantDetail> {
    const raw = await apiPost<RawTenantDetail>('/api/v1/me/switch-tenant', {
      tenantId,
    })
    return mapTenantDetail(raw)
  },

  /** 当前激活租户的成员列表。 */
  async members(tenantId: string): Promise<TenantMember[]> {
    const raw = await apiGet<RawTenantMember[]>(`/api/v1/tenants/${tenantId}/members`)
    return raw.map(mapMember)
  },

  /** 邀请邮箱加入租户（Owner/Admin）。返回的 token 只此一次可见。 */
  async invite(tenantId: string, email: string): Promise<CreatedTenantInvitation> {
    const raw = await apiPost<RawCreatedInvitation>(
      `/api/v1/tenants/${tenantId}/invitations`,
      { email }
    )
    return {
      invitation: mapInvitation(raw.invitation),
      token: raw.token,
      acceptPath: raw.accept_path,
    }
  },

  /** 待接受的租户邀请（Owner/Admin）。 */
  async listInvitations(tenantId: string): Promise<TenantInvitation[]> {
    const raw = await apiGet<RawTenantInvitation[]>(
      `/api/v1/tenants/${tenantId}/invitations`
    )
    return raw.map(mapInvitation)
  },

  /** 撤销租户邀请（幂等）。 */
  async revokeInvitation(tenantId: string, invitationId: string): Promise<void> {
    await apiDelete(`/api/v1/tenants/${tenantId}/invitations/${invitationId}`)
  },

  /** 接受租户邀请（需登录 + 邮箱匹配）。 */
  async accept(token: string): Promise<TenantDetail> {
    const raw = await apiFetch<RawTenantDetail>(
      `/api/v1/tenant-invitations/${encodeURIComponent(token)}/accept`,
      { method: 'POST' }
    )
    return mapTenantDetail(raw)
  },
}

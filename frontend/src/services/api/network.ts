/**
 * 协作网络 API —— `/api/v1/networks/*`（需求 8.2）。
 *
 * 契约见 `docs/contract-08-auth-network.md` §3.2。
 *
 * ## 错误语义（前端要按状态码分流，不要解析 message）
 *
 * - `403` —— 已认证、是成员，但角色不允许 → 提示"权限不足"，不重试
 * - `404` —— 不存在，**或存在但你无权知道**（同租户非成员/跨租户）→ 按不存在处理
 * - `409` —— 状态冲突（网络已暂停/归档、并发改角色、移除最后 Owner、重名）→ 提示刷新
 * - `410` —— 邀请已用/已过期
 */

import { apiDelete, apiFetch, apiGet, apiPatch, apiPost } from './client'
import type { TenantRole } from '@/types/auth'

// ── 类型 ──────────────────────────────────────────────────────────────

export type NetworkStatus = 'active' | 'suspended' | 'archived'

export interface NetworkPolicy {
  allowMemberPublish: boolean
  allowViewerInvoke: boolean
}

export interface NetworkItem {
  id: string
  name: string
  status: NetworkStatus
  /** 我在这个网络里的角色 */
  role: TenantRole
  policy: NetworkPolicy
  tenantId: string
  ownerId: string
  createdAt: string
}

export interface NetworkDetail extends NetworkItem {
  memberCount: number
}

export interface NetworkMember {
  userId: string
  displayName: string | null
  email: string | null
  role: TenantRole
  joinedAt: string
  /** 调整角色时回传，用于 compare-and-set（并发冲突返回 409） */
  version: number
}

export interface NetworkInvitation {
  id: string
  email: string
  status: string
  expiresAt: string
  createdAt: string
}

export interface CreatedNetworkInvitation {
  invitation: NetworkInvitation
  /** 只此一次可见 */
  token: string
  acceptPath: string
}

// ── 后端原始结构（snake_case）───────────────────────────────────────

interface RawNetwork {
  id: string
  name: string
  status: NetworkStatus
  role: TenantRole
  policy: Record<string, boolean>
  tenant_id: string
  owner_id: string
  created_at: string
  member_count?: number
}

interface RawMember {
  user_id: string
  display_name: string | null
  email: string | null
  role: TenantRole
  joined_at: string
  version: number
}

interface RawInvitation {
  id: string
  email: string
  status: string
  expires_at: string
  created_at: string
}

interface RawCreatedInvitation {
  invitation: RawInvitation
  token: string
  accept_path: string
}

// ── 映射 ──────────────────────────────────────────────────────────────

function mapPolicy(raw: Record<string, boolean> | undefined): NetworkPolicy {
  return {
    allowMemberPublish: Boolean(raw?.allowMemberPublish),
    allowViewerInvoke: Boolean(raw?.allowViewerInvoke),
  }
}

function mapNetwork(raw: RawNetwork): NetworkItem {
  return {
    id: raw.id,
    name: raw.name,
    status: raw.status,
    role: raw.role,
    policy: mapPolicy(raw.policy),
    tenantId: raw.tenant_id,
    ownerId: raw.owner_id,
    createdAt: raw.created_at,
  }
}

function mapDetail(raw: RawNetwork): NetworkDetail {
  return { ...mapNetwork(raw), memberCount: raw.member_count ?? 0 }
}

function mapMember(raw: RawMember): NetworkMember {
  return {
    userId: raw.user_id,
    displayName: raw.display_name,
    email: raw.email,
    role: raw.role,
    joinedAt: raw.joined_at,
    version: raw.version,
  }
}

function mapInvitation(raw: RawInvitation): NetworkInvitation {
  return {
    id: raw.id,
    email: raw.email,
    status: raw.status,
    expiresAt: raw.expires_at,
    createdAt: raw.created_at,
  }
}

// ── 审计（FR-N9）──────────────────────────────────────────────────────

export interface AuditEventItem {
  id: string
  action: string
  result: string
  actorId: string
  /** user / workload / system —— Agent 发起的调用是 workload */
  actorKind: string
  targetType: string | null
  targetId: string | null
  /** 把同一次请求涉及的多条事件串起来的线索 */
  requestId: string
  createdAt: string
}

interface RawAuditEvent {
  id: string
  action: string
  result: string
  actor_id: string
  actor_kind: string
  target_type: string | null
  target_id: string | null
  request_id: string
  created_at: string
}

function mapAuditEvent(raw: RawAuditEvent): AuditEventItem {
  return {
    id: raw.id,
    action: raw.action,
    result: raw.result,
    actorId: raw.actor_id,
    actorKind: raw.actor_kind,
    targetType: raw.target_type,
    targetId: raw.target_id,
    requestId: raw.request_id,
    createdAt: raw.created_at,
  }
}

// ── API ───────────────────────────────────────────────────────────────

export const networkApi = {
  /** 我所属的网络（当前激活租户下的）。 */
  async list(): Promise<NetworkItem[]> {
    const raw = await apiGet<RawNetwork[]>('/api/v1/networks')
    return raw.map(mapNetwork)
  },

  /** 创建网络，创建者自动成为唯一 Owner。 */
  async create(name: string, policy?: Partial<NetworkPolicy>): Promise<NetworkDetail> {
    const body: Record<string, unknown> = { name }
    if (policy) body.policy = policy
    const raw = await apiPost<RawNetwork>('/api/v1/networks', body)
    return mapDetail(raw)
  },

  /** 网络详情（需要 network.read）。 */
  async get(networkId: string): Promise<NetworkDetail> {
    const raw = await apiGet<RawNetwork>(`/api/v1/networks/${networkId}`)
    return mapDetail(raw)
  },

  /**
   * 更新信息 / 暂停 / 归档 / 恢复。
   *
   * 注意权限差异：`name`/`policy` 要 `network.update`（Owner/Admin），
   * `status` 要 `network.archive`（**Owner 独占**）。
   */
  async patch(
    networkId: string,
    patch: { name?: string; policy?: Partial<NetworkPolicy>; status?: NetworkStatus }
  ): Promise<NetworkItem> {
    const raw = await apiPatch<RawNetwork>(`/api/v1/networks/${networkId}`, patch)
    return mapNetwork(raw)
  },

  /** 成员列表（需要 network.members.read）。 */
  async members(networkId: string): Promise<NetworkMember[]> {
    const raw = await apiGet<RawMember[]>(`/api/v1/networks/${networkId}/members`)
    return raw.map(mapMember)
  },

  /** 调整成员角色（需要 network.members.manage）。version 用于并发 CAS。 */
  async changeRole(
    networkId: string,
    userId: string,
    role: TenantRole,
    expectedVersion?: number
  ): Promise<NetworkMember> {
    const body: Record<string, unknown> = { role }
    if (expectedVersion !== undefined) body.expectedVersion = expectedVersion
    const raw = await apiPatch<RawMember>(
      `/api/v1/networks/${networkId}/members/${userId}`,
      body
    )
    return mapMember(raw)
  },

  /** 移除成员（需要 network.members.manage）。 */
  async removeMember(networkId: string, userId: string): Promise<void> {
    await apiDelete(`/api/v1/networks/${networkId}/members/${userId}`)
  },

  /** 主动退出网络。最后一位 Owner 会被拒（409）。 */
  async leave(networkId: string): Promise<void> {
    await apiPost(`/api/v1/networks/${networkId}/leave`, undefined)
  },

  /** 邀请入网（需要 network.invite.create）。token 只此一次可见。 */
  async invite(networkId: string, email: string): Promise<CreatedNetworkInvitation> {
    const raw = await apiPost<RawCreatedInvitation>(
      `/api/v1/networks/${networkId}/invitations`,
      { email }
    )
    return {
      invitation: mapInvitation(raw.invitation),
      token: raw.token,
      acceptPath: raw.accept_path,
    }
  },

  /** 待接受的邀请（需要 network.invite.create）。 */
  async listInvitations(networkId: string): Promise<NetworkInvitation[]> {
    const raw = await apiGet<RawInvitation[]>(
      `/api/v1/networks/${networkId}/invitations`
    )
    return raw.map(mapInvitation)
  },

  /** 撤销邀请（需要 network.invite.revoke，幂等）。 */
  async revokeInvitation(networkId: string, invitationId: string): Promise<void> {
    await apiDelete(`/api/v1/networks/${networkId}/invitations/${invitationId}`)
  },

  /** 接受网络邀请（需登录 + 邀请邮箱匹配 + 已在同一租户）。 */
  async acceptInvitation(token: string): Promise<NetworkItem> {
    const raw = await apiFetch<RawNetwork>(
      `/api/v1/network-invitations/${encodeURIComponent(token)}/accept`,
      { method: 'POST' }
    )
    return mapNetwork(raw)
  },

  /**
   * 网络审计日志（需要 `network.audit.read` = **Owner/Admin**）。
   *
   * 新的在前；`limit` 上限 200（后端校验）。
   */
  async listAudit(
    networkId: string,
    opts: { limit?: number; offset?: number; action?: string } = {}
  ): Promise<AuditEventItem[]> {
    const params = new URLSearchParams()
    if (opts.limit !== undefined) params.set('limit', String(opts.limit))
    if (opts.offset !== undefined) params.set('offset', String(opts.offset))
    if (opts.action) params.set('action', opts.action)
    const qs = params.toString()
    const raw = await apiGet<RawAuditEvent[]>(
      `/api/v1/networks/${networkId}/audit${qs ? `?${qs}` : ''}`
    )
    return raw.map(mapAuditEvent)
  },
}

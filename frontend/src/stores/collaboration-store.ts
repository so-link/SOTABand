/**
 * 协作状态 —— 租户（空间）与网络（需求 8.1 + 8.2）。
 *
 * ## 为什么租户和网络放在同一个 store
 *
 * 它们**强耦合**：网络属且仅属一个租户，所以切换激活租户后，网络列表必须整体
 * 失效重拉。拆成两个 store 就要在两个 store 之间做失效通知，反而更容易漏。
 *
 * ## 错误处理约定
 *
 * 所有 action 返回 `boolean` 表示成败，失败时把**给用户看的**一句话放进
 * `error`。分流的依据是 HTTP 状态码（见 `services/api/client.ts::describeError`），
 * 不解析 message 文本。
 */

import { create } from 'zustand'

import { describeError } from '@/services/api/client'
import { networkApi } from '@/services/api/network'
import type {
  AuditEventItem,
  NetworkDetail,
  NetworkInvitation,
  NetworkItem,
  NetworkMember,
  NetworkPolicy,
  NetworkStatus,
} from '@/services/api/network'
import { sharingApi } from '@/services/api/sharing'
import type {
  Installation,
  InvokePolicy,
  Publication,
  ShareResourceType,
} from '@/services/api/sharing'
import { tenantApi } from '@/services/api/tenant'
import type { TenantInvitation, TenantMember } from '@/services/api/tenant'
import { useAuthStore } from '@/stores/auth-store'
import { useResourceStore } from '@/stores/resource-store'
import { useUIStore } from '@/stores/ui-store'
import type { TenantRole } from '@/types/auth'

/** 最近生成的一条邀请链接 —— 明文令牌只此一次可见，必须抓住 */
export interface PendingInviteLink {
  /** 'network' | 'tenant' */
  kind: 'network' | 'tenant'
  email: string
  /** 完整可复制的 URL */
  url: string
}

interface CollaborationState {
  // ── 网络 ──
  networks: NetworkItem[]
  networksLoading: boolean
  selectedNetworkId: string | null
  detail: NetworkDetail | null
  members: NetworkMember[]
  invitations: NetworkInvitation[]
  detailLoading: boolean

  // ── 资源共享（FR-N5 / FR-N6）──
  /** 当前选中网络的资源目录 */
  catalog: Publication[]
  catalogLoading: boolean
  /** 我安装到工作空间的资源（跨网络，用户级） */
  installations: Installation[]
  installationsLoading: boolean

  // ── 审计（FR-N9）──
  /** 当前选中网络的审计日志（只有 Owner/Admin 能拉到，其余为空） */
  audit: AuditEventItem[]
  auditLoading: boolean

  // ── 租户（空间）──
  tenantMembers: TenantMember[]
  tenantInvitations: TenantInvitation[]
  tenantLoading: boolean
  switchingTenantId: string | null

  // ── 通用 ──
  /** 给用户看的错误（一句话） */
  error: string | null
  /** 正在进行的操作标识，用于按钮禁用（如 'invite' / 'archive'） */
  busy: string | null
  /** 明文邀请链接：只此一次，展示后应提示用户复制 */
  inviteLink: PendingInviteLink | null

  // ── 网络 action ──
  loadNetworks: () => Promise<void>
  createNetwork: (name: string) => Promise<string | null>
  selectNetwork: (networkId: string | null) => Promise<void>
  refreshDetail: () => Promise<void>
  patchNetwork: (patch: {
    name?: string
    policy?: Partial<NetworkPolicy>
    status?: NetworkStatus
  }) => Promise<boolean>
  changeMemberRole: (userId: string, role: TenantRole) => Promise<boolean>
  removeMember: (userId: string) => Promise<boolean>
  leaveNetwork: () => Promise<boolean>
  inviteMember: (email: string) => Promise<boolean>
  revokeInvitation: (invitationId: string) => Promise<boolean>

  // ── 资源共享 action（FR-N5 / FR-N6）──
  loadCatalog: () => Promise<void>
  publishResource: (resourceType: ShareResourceType, releaseId: string) => Promise<boolean>
  revokePublication: (publicationId: string) => Promise<boolean>
  /**
   * 设置「谁能调用这个资源」（FR-N7 按目标粒度授权）。
   *
   * 成功后刷新目录 —— 策略是网络可见的事实，不该只改本地状态。
   */
  setPublicationInvokePolicy: (
    publicationId: string,
    policy: InvokePolicy,
    allowlist: string[]
  ) => Promise<boolean>
  installResource: (resourceType: ShareResourceType, releaseId: string) => Promise<boolean>
  loadMyInstallations: () => Promise<void>
  uninstallResource: (installationId: string) => Promise<boolean>

  // ── 审计 action（FR-N9）──
  loadAudit: (action?: string) => Promise<void>

  // ── 租户 action ──
  loadTenant: () => Promise<void>
  switchTenant: (tenantId: string) => Promise<boolean>
  createTenant: (name: string) => Promise<boolean>
  inviteToTenant: (email: string) => Promise<boolean>
  revokeTenantInvitation: (invitationId: string) => Promise<boolean>

  // ── 工具 ──
  clearError: () => void
  clearInviteLink: () => void
  /** 退出登录时清空，避免下一个账号看到上一个账号的数据 */
  reset: () => void
}

/** 拼出可复制的邀请链接。用当前 origin，开发期与生产期都不用改代码。 */
function buildInviteUrl(kind: 'network' | 'tenant', token: string): string {
  const param = kind === 'network' ? 'invite' : 'tenantInvite'
  const { origin, pathname } = window.location
  return `${origin}${pathname}?${param}=${encodeURIComponent(token)}`
}

const INITIAL = {
  networks: [] as NetworkItem[],
  networksLoading: false,
  selectedNetworkId: null as string | null,
  detail: null as NetworkDetail | null,
  members: [] as NetworkMember[],
  invitations: [] as NetworkInvitation[],
  detailLoading: false,
  catalog: [] as Publication[],
  catalogLoading: false,
  installations: [] as Installation[],
  installationsLoading: false,
  audit: [] as AuditEventItem[],
  auditLoading: false,
  tenantMembers: [] as TenantMember[],
  tenantInvitations: [] as TenantInvitation[],
  tenantLoading: false,
  switchingTenantId: null as string | null,
  error: null as string | null,
  busy: null as string | null,
  inviteLink: null as PendingInviteLink | null,
}

export const useCollaborationStore = create<CollaborationState>((set, get) => ({
  ...INITIAL,

  // ── 网络 ────────────────────────────────────────────────────────────

  async loadNetworks() {
    set({ networksLoading: true, error: null })
    try {
      const networks = await networkApi.list()
      set({ networks, networksLoading: false })
    } catch (err) {
      set({ networksLoading: false, error: describeError(err) })
    }
  },

  async createNetwork(name) {
    set({ busy: 'create-network', error: null })
    try {
      const created = await networkApi.create(name)
      // 直接插到列表头，避免整表重拉（也保证选中项立即可用）
      set((s) => ({
        networks: [created, ...s.networks],
        busy: null,
        selectedNetworkId: created.id,
      }))
      await get().refreshDetail()
      return created.id
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return null
    }
  },

  async selectNetwork(networkId) {
    set({
      selectedNetworkId: networkId,
      detail: null,
      members: [],
      invitations: [],
      // 切网络必须清空目录与审计 —— 否则会短暂显示上一个网络的数据
      catalog: [],
      audit: [],
    })
    if (networkId) await get().refreshDetail()
  },

  async refreshDetail() {
    const id = get().selectedNetworkId
    if (!id) return

    set({ detailLoading: true, catalogLoading: true, error: null })
    try {
      // 详情 / 成员 / 邀请 / 资源目录并行拉。
      // 邀请需要 invite.create 权限、目录需要 network.read —— 权限不足时静默降级为空，
      // 不当作错误弹给用户（他本来就看不到那一块）。
      const [detail, members, invitations, catalog] = await Promise.all([
        networkApi.get(id),
        networkApi.members(id),
        networkApi.listInvitations(id).catch(() => [] as NetworkInvitation[]),
        sharingApi.listCatalog(id).catch(() => [] as Publication[]),
      ])
      set({
        detail,
        members,
        invitations,
        catalog,
        detailLoading: false,
        catalogLoading: false,
      })
    } catch (err) {
      set({ detailLoading: false, catalogLoading: false, error: describeError(err) })
    }
  },

  async patchNetwork(patch) {
    const id = get().selectedNetworkId
    if (!id) return false

    set({ busy: 'patch-network', error: null })
    try {
      const updated = await networkApi.patch(id, patch)
      set((s) => ({
        busy: null,
        networks: s.networks.map((n) => (n.id === id ? { ...n, ...updated } : n)),
      }))
      await get().refreshDetail()
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async changeMemberRole(userId, role) {
    const id = get().selectedNetworkId
    if (!id) return false
    const target = get().members.find((m) => m.userId === userId)

    set({ busy: `role:${userId}`, error: null })
    try {
      // 回传 version 做 compare-and-set：并发改角色时后端返回 409，
      // 而不是让两个人的修改互相覆盖（契约 §4）。
      await networkApi.changeRole(id, userId, role, target?.version)
      await get().refreshDetail()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async removeMember(userId) {
    const id = get().selectedNetworkId
    if (!id) return false

    set({ busy: `remove:${userId}`, error: null })
    try {
      await networkApi.removeMember(id, userId)
      await get().refreshDetail()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async leaveNetwork() {
    const id = get().selectedNetworkId
    if (!id) return false

    set({ busy: 'leave', error: null })
    try {
      await networkApi.leave(id)
      set((s) => ({
        busy: null,
        networks: s.networks.filter((n) => n.id !== id),
        selectedNetworkId: null,
        detail: null,
        members: [],
        invitations: [],
      }))
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async inviteMember(email) {
    const id = get().selectedNetworkId
    if (!id) return false

    set({ busy: 'invite', error: null, inviteLink: null })
    try {
      const created = await networkApi.invite(id, email)
      set({
        busy: null,
        inviteLink: {
          kind: 'network',
          email,
          url: buildInviteUrl('network', created.token),
        },
      })
      await get().refreshDetail()
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async revokeInvitation(invitationId) {
    const id = get().selectedNetworkId
    if (!id) return false

    set({ busy: `revoke:${invitationId}`, error: null })
    try {
      await networkApi.revokeInvitation(id, invitationId)
      await get().refreshDetail()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  // ── 资源共享（FR-N5 / FR-N6）────────────────────────────────────────

  async loadCatalog() {
    const id = get().selectedNetworkId
    if (!id) return

    set({ catalogLoading: true, error: null })
    try {
      const catalog = await sharingApi.listCatalog(id)
      set({ catalog, catalogLoading: false })
    } catch (err) {
      set({ catalogLoading: false, error: describeError(err) })
    }
  },

  async publishResource(resourceType, releaseId) {
    const id = get().selectedNetworkId
    if (!id) return false

    set({ busy: 'publish', error: null })
    try {
      await sharingApi.publish(resourceType, releaseId, 'network', id)
      await get().loadCatalog()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async revokePublication(publicationId) {
    set({ busy: `revoke-pub:${publicationId}`, error: null })
    try {
      await sharingApi.revoke(publicationId)
      await get().loadCatalog()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async setPublicationInvokePolicy(publicationId, policy, allowlist) {
    set({ busy: `invoke-policy:${publicationId}`, error: null })
    try {
      await sharingApi.setInvokePolicy(publicationId, {
        invokePolicy: policy,
        invokeAllowlist: allowlist,
      })
      await get().loadCatalog()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async installResource(resourceType, releaseId) {    const id = get().selectedNetworkId
    if (!id) return false

    set({ busy: `install:${resourceType}:${releaseId}`, error: null })
    try {
      await sharingApi.install(resourceType, releaseId, id)
      // 安装后刷新「我的安装」列表 —— 目录本身不变（安装不影响发布状态）
      await get().loadMyInstallations()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async loadMyInstallations() {
    set({ installationsLoading: true, error: null })
    try {
      const installations = await sharingApi.listMyInstallations()
      set({ installations, installationsLoading: false })
    } catch (err) {
      set({ installationsLoading: false, error: describeError(err) })
    }
  },

  async uninstallResource(installationId) {
    set({ busy: `uninstall:${installationId}`, error: null })
    try {
      await sharingApi.uninstall(installationId)
      await get().loadMyInstallations()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  // ── 审计（FR-N9）────────────────────────────────────────────────────

  async loadAudit(action) {
    const id = get().selectedNetworkId
    if (!id) return

    set({ auditLoading: true })
    try {
      // 权限不足（member/viewer）时后端返回 403 —— 静默降级为空，
      // 因为这一块本来就不该给他们看，不是错误
      const audit = await networkApi
        .listAudit(id, { limit: 20, action })
        .catch(() => [] as AuditEventItem[])
      set({ audit, auditLoading: false })
    } catch {
      set({ auditLoading: false, audit: [] })
    }
  },

  // ── 租户 ────────────────────────────────────────────────────────────

  async loadTenant() {
    const activeTenantId = useAuthStore.getState().user?.activeTenantId
    if (!activeTenantId) return

    set({ tenantLoading: true, error: null })
    try {
      const [tenantMembers, tenantInvitations] = await Promise.all([
        tenantApi.members(activeTenantId),
        // 普通成员看不到邀请列表（403），静默降级为空
        tenantApi.listInvitations(activeTenantId).catch(() => [] as TenantInvitation[]),
      ])
      set({ tenantMembers, tenantInvitations, tenantLoading: false })
    } catch (err) {
      set({ tenantLoading: false, error: describeError(err) })
    }
  },

  async switchTenant(tenantId) {
    const previous = useAuthStore.getState().user
    const previousTenantId = previous?.activeTenantId ?? null

    set({ switchingTenantId: tenantId, error: null })

    // 切换失败也可能"服务端已切、响应丢了"，所以不能只看 switchTo 的结果 ——
    // 下面统一用 /me 确认真实身份。
    let switchError: string | null = null
    try {
      await tenantApi.switchTo(tenantId)
    } catch (err) {
      switchError = describeError(err)
    }

    // 刷新用户快照（activeTenantId / tenantRole / tenants 都在 /me 里）。
    // 刷新失败时**绝不能**继续用旧快照里的 tenantRole 渲染：旧角色可能高于新租户
    // 里的真实角色，用户点下去就是 403/404（服务端拒、前端放行）。
    if (!(await useAuthStore.getState().refresh())) {
      // 先把服务器与本地一起退回切换前（能退就退），退不回去就让会话过期 ——
      // 身份确认不了时宁可重新登录，也不按旧角色继续渲染。
      const rolledBack =
        previousTenantId && previousTenantId !== tenantId
          ? await tenantApi
              .switchTo(previousTenantId)
              .then(() => useAuthStore.getState().refresh())
              .catch(() => false)
          : false

      const message = rolledBack
        ? '切换空间后刷新身份失败，已退回原空间，请稍后重试'
        : '切换空间后无法确认身份，请重新登录'
      if (!rolledBack) {
        useAuthStore.setState({ status: 'expired', user: null, error: message })
      }
      set({ switchingTenantId: null, error: message })
      return false
    }

    // /me 确认当前激活租户仍是旧的（例如 403 无权切换到该租户）→ 按失败处理。
    // 以服务端确认的 activeTenantId 为准：switchTo 报错但实际已切过去时，
    // 这里仍然按成功走，界面不会停在旧租户。
    if (useAuthStore.getState().user?.activeTenantId !== tenantId) {
      set({ switchingTenantId: null, error: switchError ?? '切换空间失败，请稍后重试' })
      return false
    }

    // 租户变了，网络与会话内的资源视图必然整体不同 —— 清掉再重拉。
    // 尤其 catalog / installations：不清会出现"上一个租户的目录还在"的错觉。
    set({
      switchingTenantId: null,
      selectedNetworkId: null,
      detail: null,
      members: [],
      invitations: [],
      catalog: [],
      installations: [],
      audit: [],
      networks: [],
    })
    await get().loadNetworks()
    return true
  },

  async createTenant(name) {
    set({ busy: 'create-tenant', error: null })
    try {
      await tenantApi.create(name)
      // 创建后**不自动切换**（后端语义如此），刷新让新租户出现在列表里
      await useAuthStore.getState().refresh()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async inviteToTenant(email) {
    const activeTenantId = useAuthStore.getState().user?.activeTenantId
    if (!activeTenantId) return false

    set({ busy: 'invite-tenant', error: null, inviteLink: null })
    try {
      const created = await tenantApi.invite(activeTenantId, email)
      set({
        busy: null,
        inviteLink: {
          kind: 'tenant',
          email,
          url: buildInviteUrl('tenant', created.token),
        },
      })
      await get().loadTenant()
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  async revokeTenantInvitation(invitationId) {
    const activeTenantId = useAuthStore.getState().user?.activeTenantId
    if (!activeTenantId) return false

    set({ busy: `revoke-tenant:${invitationId}`, error: null })
    try {
      await tenantApi.revokeInvitation(activeTenantId, invitationId)
      await get().loadTenant()
      set({ busy: null })
      return true
    } catch (err) {
      set({ busy: null, error: describeError(err) })
      return false
    }
  },

  // ── 工具 ────────────────────────────────────────────────────────────

  clearError() {
    set({ error: null })
  },

  clearInviteLink() {
    set({ inviteLink: null })
  },

  reset() {
    set({ ...INITIAL })
  },
}))

/** 退出登录时清空协作状态，防止串号。在 auth store 的 logout 之后调用。 */
export function resetCollaborationState() {
  useCollaborationStore.getState().reset()
}

// 监听认证状态：从"已登录"变成别的状态（登出 / 会话过期 / 账号被禁用）时，
// 把**所有**与账号绑定的前端状态清空。
//
// 为什么用订阅而不是在每个登出入口手动调：登出路径有多条（主动退出、退出全部
// 设备、401 过期、账号被禁用、切换空间时身份刷新失败），漏一条就会让下一个登录的
// 账号看到上一个账号的网络列表、资源列表、页签标题 —— 那是最难察觉也最严重的一类 bug。
useAuthStore.subscribe((state, prevState) => {
  if (prevState.status === 'authenticated' && state.status !== 'authenticated') {
    useCollaborationStore.getState().reset()
    // 资源列表 / 选中项 / 详情缓存
    useResourceStore.getState().reset()
    // 页签与激活视图（顺带丢弃工具详情草稿）
    useUIStore.getState().resetWorkspace()
  }
})

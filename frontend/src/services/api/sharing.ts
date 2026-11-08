/**
 * 网络资源共享与调用 API —— `/api/v1/*-releases/*`、`/network-catalog`、`/installations`
 *
 * 契约见 `docs/contract-08-auth-network.md` §3.3。
 *
 * ## 三层概念别搞混
 *
 * ```text
 * 发布（publication）：把某个资源版本开放给某个范围
 * 安装（installation）：把已发布的资源装进自己的工作空间
 * 凭证（credential）：Agent 用它在网络内调用其他资源（前端暂无 UI，属运维面）
 * ```
 *
 * 本文件只覆盖前两层 —— 凭证与调用是给 Agent 进程用的，不是给人点按钮用的。
 */

import { apiDelete, apiGet, apiPatch, apiPost } from './client'

export type ShareResourceType = 'agent' | 'tool' | 'data'
export type PublicationScope = 'private' | 'tenant' | 'network' | 'public'

/**
 * 谁能调用这个资源（FR-N7 按目标粒度授权）。
 *
 * - `network`（默认）：网络内所有有 `network.agent.invoke` 权限的成员都能调
 * - `allowlist`：只有 `invokeAllowlist` 里的成员能调
 */
export type InvokePolicy = 'network' | 'allowlist'

// ── 类型 ──────────────────────────────────────────────────────────────

export interface Publication {
  id: string
  resourceType: ShareResourceType
  /**
   * 资源版本 id —— registry.json 里的版本号，**不是 UUID**。
   * 它属于"跨系统逻辑引用"：数据库无法校验其存在（连外键都做不到），
   * 资源被删后查询侧要优雅降级。
   */
  releaseId: string
  scope: PublicationScope
  scopeId: string | null
  status: 'active' | 'revoked'
  publishedBy: string
  createdAt: string
  /** 谁能调用（见 {@link InvokePolicy}） */
  invokePolicy: InvokePolicy
  /** `invokePolicy='allowlist'` 时生效：允许调用的成员 user_id 列表 */
  invokeAllowlist: string[]
}

export interface Installation {
  id: string
  publicationId: string
  status: 'active' | 'revoked'
  createdAt: string
  /** 资源可能已下架 —— 此时为 null，前端要显示「资源已下架」而不是崩掉 */
  resourceType: ShareResourceType | null
  releaseId: string | null
}

// ── 后端原始结构（snake_case）───────────────────────────────────────

interface RawPublication {
  id: string
  resource_type: ShareResourceType
  release_id: string
  scope: PublicationScope
  scope_id: string | null
  status: 'active' | 'revoked'
  published_by: string
  created_at: string
  // 老数据/老后端可能没有这两项，mapper 里兜默认值
  invoke_policy?: InvokePolicy
  invoke_allowlist?: string[] | null
}

interface RawInstallation {
  id: string
  publication_id: string
  status: 'active' | 'revoked'
  created_at: string
  resource_type: ShareResourceType | null
  release_id: string | null
}

// ── 映射 ──────────────────────────────────────────────────────────────

function mapPublication(raw: RawPublication): Publication {
  return {
    id: raw.id,
    resourceType: raw.resource_type,
    releaseId: raw.release_id,
    scope: raw.scope,
    scopeId: raw.scope_id,
    status: raw.status,
    publishedBy: raw.published_by,
    createdAt: raw.created_at,
    invokePolicy: raw.invoke_policy ?? 'network',
    invokeAllowlist: raw.invoke_allowlist ?? [],
  }
}

function mapInstallation(raw: RawInstallation): Installation {
  return {
    id: raw.id,
    publicationId: raw.publication_id,
    status: raw.status,
    createdAt: raw.created_at,
    resourceType: raw.resource_type,
    releaseId: raw.release_id,
  }
}

// ── API ───────────────────────────────────────────────────────────────

export const sharingApi = {
  /** 某个网络的资源目录（需要 `network.read`）。 */
  async listCatalog(
    networkId: string,
    resourceType?: ShareResourceType
  ): Promise<Publication[]> {
    const params = new URLSearchParams({ networkId })
    if (resourceType) params.set('resourceType', resourceType)
    const raw = await apiGet<RawPublication[]>(`/api/v1/network-catalog?${params}`)
    return raw.map(mapPublication)
  },

  /** 发布资源到网络（需要 `network.resource.publish`，受 allowMemberPublish 约束）。 */
  async publish(
    resourceType: ShareResourceType,
    releaseId: string,
    scope: PublicationScope,
    scopeId?: string
  ): Promise<Publication> {
    const body: Record<string, unknown> = { scope }
    if (scopeId) body.scopeId = scopeId
    const raw = await apiPost<RawPublication>(
      `/api/v1/${resourceType}-releases/${encodeURIComponent(releaseId)}/publications`,
      body
    )
    return mapPublication(raw)
  },

  /** 撤回发布（发布者本人或网络 Owner/Admin）。 */
  async revoke(publicationId: string): Promise<void> {
    await apiDelete(`/api/v1/publications/${publicationId}`)
  },

  /**
   * 设置「谁能调用这个资源」（FR-N7 按目标粒度授权）。
   *
   * - `network`：网络内所有有 invoke 权限的成员都能调
   * - `allowlist`：只有名单里的成员能调；名单里的人必须**当前**是该网络成员，
   *   且名单不能为空（空名单等于谁都调不了，后端会 400）
   */
  async setInvokePolicy(
    publicationId: string,
    payload: { invokePolicy: InvokePolicy; invokeAllowlist?: string[] }
  ): Promise<Publication> {
    const raw = await apiPatch<RawPublication>(
      `/api/v1/publications/${publicationId}/invoke-policy`,
      {
        invokePolicy: payload.invokePolicy,
        invokeAllowlist: payload.invokeAllowlist ?? [],
      }
    )
    return mapPublication(raw)
  },

  /** 从网络目录安装到自己的工作空间（幂等）。 */
  async install(
    resourceType: ShareResourceType,
    releaseId: string,
    networkId: string
  ): Promise<Installation> {
    const params = new URLSearchParams({ networkId })
    const raw = await apiPost<RawInstallation>(
      `/api/v1/${resourceType}-releases/${encodeURIComponent(releaseId)}/installations?${params}`
    )
    return mapInstallation(raw)
  },

  /** 我安装到工作空间的资源。 */
  async listMyInstallations(): Promise<Installation[]> {
    const raw = await apiGet<RawInstallation[]>('/api/v1/me/installations')
    return raw.map(mapInstallation)
  },

  /** 卸载（仅安装者本人）。 */
  async uninstall(installationId: string): Promise<void> {
    await apiDelete(`/api/v1/installations/${installationId}`)
  },
}

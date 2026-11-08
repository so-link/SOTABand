/**
 * 协作网络与空间管理 —— 需求 8.2 的前端入口。
 *
 * ## 布局
 *
 * 顶部两个子页签：
 * - **协作网络**：左列表 + 右详情（成员、角色、邀请、状态机）
 * - **空间**：当前激活租户的成员与邀请（把人拉进同一租户，是共享网络的前提）
 *
 * ## 权限 → UI 可见性
 *
 * 前端的显隐**只是体验**，真正的判定在后端（契约 §6 的判定链）。这里按相同的
 * 真值表（契约 §5.2）推导，避免"点了才报 403"：
 *
 * | 能力 | owner | admin | member | viewer |
 * |---|---|---|---|---|
 * | 改信息 / 管成员 / 邀请 | ✓ | ✓ | ✗ | ✗ |
 * | 暂停 / 归档 / 恢复（Owner 独占） | ✓ | ✗ | ✗ | ✗ |
 *
 * ## 为什么所有交互元素都带 `data-testid`
 *
 * 端到端测试用稳定标识而不是文本选择器 —— 文案会改，testid 不会。这也让
 * `.planning/project-status/verify_network.py` 不依赖任何中文文本。
 */

import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Archive,
  Check,
  Copy,
  Crown,
  Loader2,
  Network as NetworkIcon,
  Package,
  Pause,
  Play,
  Plus,
  RefreshCw,
  ScrollText,
  Share2,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { NetworkItem, NetworkStatus } from '@/services/api/network'
import type { InvokePolicy, ShareResourceType } from '@/services/api/sharing'
import { useAuthStore } from '@/stores/auth-store'
import { useCollaborationStore } from '@/stores/collaboration-store'
import { useResourceStore } from '@/stores/resource-store'
import type { TenantRole } from '@/types/auth'

// ── 文案映射 ──────────────────────────────────────────────────────────

const ROLE_LABEL: Record<TenantRole, string> = {
  owner: '所有者',
  admin: '管理员',
  member: '成员',
  viewer: '访客',
}

const STATUS_LABEL: Record<NetworkStatus, string> = {
  active: '运行中',
  suspended: '已暂停',
  archived: '已归档',
}

const STATUS_VARIANT: Record<NetworkStatus, 'success' | 'warning' | 'default'> = {
  active: 'success',
  suspended: 'warning',
  archived: 'default',
}

/** 能改信息 / 管成员 / 邀请（真值表：owner、admin） */
function canManage(role: TenantRole | null | undefined): boolean {
  return role === 'owner' || role === 'admin'
}

/** 能暂停/归档/恢复（真值表：**Owner 独占**） */
function canArchive(role: TenantRole | null | undefined): boolean {
  return role === 'owner'
}

// ── 主组件 ────────────────────────────────────────────────────────────

export function NetworksView() {
  const [tab, setTab] = useState<'networks' | 'space' | 'installed'>('networks')

  return (
    <div className="flex h-full flex-col" data-testid="networks-view">
      <div className="flex shrink-0 items-center gap-1 border-b border-maia-border/30 px-3 py-1.5">
        <SubTab
          active={tab === 'networks'}
          onClick={() => setTab('networks')}
          testId="tab-networks"
          icon={<NetworkIcon className="h-3.5 w-3.5" />}
          label="协作网络"
        />
        <SubTab
          active={tab === 'space'}
          onClick={() => setTab('space')}
          testId="tab-space"
          icon={<Users className="h-3.5 w-3.5" />}
          label="空间"
        />
        <SubTab
          active={tab === 'installed'}
          onClick={() => setTab('installed')}
          testId="tab-installed"
          icon={<Package className="h-3.5 w-3.5" />}
          label="我的安装"
        />
      </div>

      <div className="min-h-0 flex-1">
        {tab === 'networks' && <NetworksTab />}
        {tab === 'space' && <SpaceTab />}
        {tab === 'installed' && <InstalledTab />}
      </div>
    </div>
  )
}

function SubTab({
  active,
  onClick,
  testId,
  icon,
  label,
}: {
  active: boolean
  onClick: () => void
  testId: string
  icon: React.ReactNode
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] font-semibold tracking-wider transition-colors ${
        active
          ? 'bg-maia-accent-light text-maia-accent'
          : 'text-maia-text-muted hover:bg-maia-sidebar-hover hover:text-maia-text-secondary'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}

/** 错误条 —— 所有 store.error 都从这里展示，避免每个面板各写一遍 */
function ErrorBar() {
  const error = useCollaborationStore((s) => s.error)
  const clearError = useCollaborationStore((s) => s.clearError)
  if (!error) return null

  return (
    <div
      data-testid="collab-error"
      className="mx-3 mt-2 flex items-start gap-2 rounded border border-maia-danger/30 bg-maia-danger-bg px-2.5 py-1.5"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-maia-danger" />
      <span className="flex-1 text-[11px] leading-relaxed text-maia-danger">{error}</span>
      <button
        type="button"
        onClick={clearError}
        className="shrink-0 text-maia-danger/70 hover:text-maia-danger"
        aria-label="关闭错误提示"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

/** 邀请链接展示 —— 明文令牌只此一次可见，必须给复制按钮 */
function InviteLinkBox() {
  const inviteLink = useCollaborationStore((s) => s.inviteLink)
  const clearInviteLink = useCollaborationStore((s) => s.clearInviteLink)
  const [copied, setCopied] = useState(false)

  if (!inviteLink) return null

  async function handleCopy() {
    if (!inviteLink) return
    try {
      await navigator.clipboard.writeText(inviteLink.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      // 剪贴板 API 在非安全上下文会失败（http 非 localhost）。降级为选中文本。
      const input = document.querySelector<HTMLInputElement>(
        '[data-testid="invite-link-input"]'
      )
      input?.select()
    }
  }

  return (
    <div
      data-testid="invite-link-box"
      className="mt-2 rounded border border-maia-accent-border bg-maia-accent-light/40 px-2.5 py-2"
    >
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-wider text-maia-accent">
          邀请链接（只显示这一次）
        </span>
        <button
          type="button"
          onClick={clearInviteLink}
          className="text-maia-text-muted hover:text-maia-text"
          aria-label="关闭邀请链接"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
      <p className="mb-1.5 text-[10px] leading-relaxed text-maia-text-muted">
        发给 {inviteLink.email}。令牌只存哈希，服务端无法再次显示 —— 关掉后只能重新邀请。
      </p>
      <div className="flex gap-1.5">
        <input
          data-testid="invite-link-input"
          readOnly
          value={inviteLink.url}
          onFocus={(e) => e.currentTarget.select()}
          className="min-w-0 flex-1 rounded border border-maia-border bg-maia-bg px-2 py-1 font-mono text-[10px] text-maia-text-secondary"
        />
        <Button size="sm" variant="outline" onClick={handleCopy} data-testid="copy-invite-btn">
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? '已复制' : '复制'}
        </Button>
      </div>
    </div>
  )
}

// ── 协作网络 tab ──────────────────────────────────────────────────────

function NetworksTab() {
  const networks = useCollaborationStore((s) => s.networks)
  const networksLoading = useCollaborationStore((s) => s.networksLoading)
  const selectedNetworkId = useCollaborationStore((s) => s.selectedNetworkId)
  const loadNetworks = useCollaborationStore((s) => s.loadNetworks)
  const selectNetwork = useCollaborationStore((s) => s.selectNetwork)
  const loadMyInstallations = useCollaborationStore((s) => s.loadMyInstallations)

  useEffect(() => {
    void loadNetworks()
    // 目录条目要显示「已安装」状态，所以这里也要拉一次我的安装
    void loadMyInstallations()
  }, [loadNetworks, loadMyInstallations])

  return (
    <div className="flex h-full">
      <NetworkListPanel
        networks={networks}
        loading={networksLoading}
        selectedId={selectedNetworkId}
        onSelect={selectNetwork}
      />
      <div className="min-w-0 flex-1 overflow-y-auto">
        <ErrorBar />
        {selectedNetworkId ? <NetworkDetailPanel /> : <EmptyDetail />}
      </div>
    </div>
  )
}

function NetworkListPanel({
  networks,
  loading,
  selectedId,
  onSelect,
}: {
  networks: NetworkItem[]
  loading: boolean
  selectedId: string | null
  onSelect: (id: string | null) => void
}) {
  const createNetwork = useCollaborationStore((s) => s.createNetwork)
  const busy = useCollaborationStore((s) => s.busy)
  const [name, setName] = useState('')

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    const id = await createNetwork(trimmed)
    if (id) setName('')
  }

  return (
    <div className="flex w-64 shrink-0 flex-col border-r border-maia-border/30">
      <form onSubmit={handleCreate} className="shrink-0 border-b border-maia-border/30 p-2.5">
        <label className="mb-1 block text-[10px] font-semibold tracking-wider text-maia-text-muted">
          新建网络
        </label>
        <div className="flex gap-1.5">
          <input
            data-testid="net-name-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：飞控算法组"
            maxLength={128}
            className="min-w-0 flex-1 rounded border border-maia-border bg-maia-bg px-2 py-1 text-[11px] text-maia-text placeholder:text-maia-text-muted/60 focus:border-maia-accent-border focus:outline-none"
          />
          <Button
            type="submit"
            size="sm"
            data-testid="net-create-btn"
            disabled={!name.trim() || busy === 'create-network'}
          >
            {busy === 'create-network' ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Plus className="h-3 w-3" />
            )}
          </Button>
        </div>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto p-1.5" data-testid="net-list">
        {loading && networks.length === 0 && (
          <div className="flex items-center gap-2 p-2 text-[11px] text-maia-text-muted">
            <Loader2 className="h-3 w-3 animate-spin" /> 加载中…
          </div>
        )}

        {!loading && networks.length === 0 && (
          <p className="p-2 text-[11px] leading-relaxed text-maia-text-muted" data-testid="net-list-empty">
            你还不属于任何网络。在左上角新建一个，或让空间里的同事邀请你。
          </p>
        )}

        {networks.map((network) => (
          <button
            key={network.id}
            type="button"
            onClick={() => onSelect(network.id)}
            data-testid="net-item"
            data-network-id={network.id}
            className={`mb-1 flex w-full items-start gap-2 rounded px-2 py-1.5 text-left transition-colors ${
              selectedId === network.id
                ? 'bg-maia-accent-light/60 text-maia-accent'
                : 'text-maia-text-secondary hover:bg-maia-sidebar-hover'
            }`}
          >
            <NetworkIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[11px] font-semibold">{network.name}</span>
              <span className="mt-0.5 flex items-center gap-1.5">
                <span className="text-[9px] tracking-wider text-maia-text-muted">
                  {ROLE_LABEL[network.role]}
                </span>
                <Badge variant={STATUS_VARIANT[network.status]}>
                  {STATUS_LABEL[network.status]}
                </Badge>
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

function EmptyDetail() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      <NetworkIcon className="h-8 w-8 text-maia-text-muted/40" />
      <p className="text-[11px] text-maia-text-muted">从左侧选择一个网络查看详情</p>
    </div>
  )
}

function NetworkDetailPanel() {
  const detail = useCollaborationStore((s) => s.detail)
  const detailLoading = useCollaborationStore((s) => s.detailLoading)
  const refreshDetail = useCollaborationStore((s) => s.refreshDetail)

  if (detailLoading && !detail) {
    return (
      <div className="flex items-center gap-2 p-4 text-[11px] text-maia-text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载详情…
      </div>
    )
  }
  if (!detail) return null

  const role = detail.role
  // 写操作只在 active 网络可用：suspended / archived 时服务端一律 409。
  // 状态机按钮（暂停/恢复/归档）单独按 Owner 判定 —— 否则暂停之后就没人能恢复了。
  const manage = canManage(role) && detail.status === 'active'
  const archive = canArchive(role)

  return (
    <div className="p-4">
      {/* 标题 + 状态 */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2
              className="truncate text-[15px] font-semibold text-maia-text-heading"
              data-testid="net-detail-title"
            >
              {detail.name}
            </h2>
            <Badge variant={STATUS_VARIANT[detail.status]} data-testid="net-detail-status">
              {STATUS_LABEL[detail.status]}
            </Badge>
            {role === 'owner' && (
              <Badge variant="accent">
                <Crown className="mr-0.5 h-2.5 w-2.5" />
                所有者
              </Badge>
            )}
          </div>
          <p className="mt-1 text-[10px] tracking-wide text-maia-text-muted">
            <span data-testid="net-member-count">{detail.memberCount}</span> 位成员 ·
            我的角色：<span data-testid="net-my-role">{ROLE_LABEL[role]}</span>
          </p>
        </div>
        <Button
          size="icon"
          variant="ghost"
          onClick={() => void refreshDetail()}
          title="刷新"
          data-testid="net-refresh-btn"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      {detail.status !== 'active' && (
        <p
          data-testid="net-status-notice"
          className="mb-3 rounded border border-maia-warning/30 bg-maia-warning-bg px-2.5 py-1.5 text-[10px] leading-relaxed text-maia-warning"
        >
          网络{STATUS_LABEL[detail.status]}：{detail.status === 'suspended' ? '只读可查看，写操作会被拒绝（409）。' : '普通成员不可见，仅所有者与管理员可查看。'}
        </p>
      )}

      {/* 状态动作（Owner 独占） */}
      {archive && (
        <div className="mb-4 flex flex-wrap gap-1.5">
          {detail.status === 'active' && (
            <Button
              size="sm"
              variant="outline"
              data-testid="net-suspend-btn"
              onClick={() => void useCollaborationStore.getState().patchNetwork({ status: 'suspended' })}
            >
              <Pause className="h-3 w-3" /> 暂停
            </Button>
          )}
          {(detail.status === 'suspended' || detail.status === 'archived') && (
            <Button
              size="sm"
              variant="outline"
              data-testid="net-restore-btn"
              onClick={() => void useCollaborationStore.getState().patchNetwork({ status: 'active' })}
            >
              <Play className="h-3 w-3" /> 恢复
            </Button>
          )}
          {detail.status !== 'archived' && (
            <Button
              size="sm"
              variant="ghost"
              data-testid="net-archive-btn"
              onClick={() => {
                if (window.confirm('归档后普通成员将看不到这个网络。确定归档？')) {
                  void useCollaborationStore.getState().patchNetwork({ status: 'archived' })
                }
              }}
            >
              <Archive className="h-3 w-3" /> 归档
            </Button>
          )}
        </div>
      )}

      <MembersSection manage={manage} myRole={role} />
      <CatalogSection
        role={role}
        policyAllowsMemberPublish={Boolean(detail.policy?.allowMemberPublish)}
        networkActive={detail.status === 'active'}
      />
      {manage && <InviteSection />}
      <AuditSection />
      <DangerSection active={detail.status === 'active'} />
    </div>
  )
}

// ── 成员 ──────────────────────────────────────────────────────────────

function MembersSection({ manage, myRole }: { manage: boolean; myRole: TenantRole }) {
  const members = useCollaborationStore((s) => s.members)
  const busy = useCollaborationStore((s) => s.busy)
  const changeMemberRole = useCollaborationStore((s) => s.changeMemberRole)
  const removeMember = useCollaborationStore((s) => s.removeMember)
  const myId = useAuthStore((s) => s.user?.userId)

  return (
    <section className="mb-4">
      <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-maia-text-secondary">
        <Users className="h-3.5 w-3.5" /> 成员
      </h3>
      <div className="overflow-hidden rounded border border-maia-border" data-testid="net-members">
        {members.map((member) => {
          const isSelf = member.userId === myId
          // 服务端对 Admin 有两条真值表表达不了的额外限制（见 core/network/service.py
          // ``_assert_admin_may_act_on``），前端必须同口径，否则点了只会拿到 404/409：
          //   - Admin 不可对 Owner 行使任何成员管理动作（对 Admin 来说该成员"不存在"）；
          //   - Admin 不可改 / 删自己，必须走「退出网络」。
          const lockedByRole = myRole !== 'owner' && member.role === 'owner'
          const lockedBySelf = myRole === 'admin' && isSelf
          const rowManage = manage && !lockedByRole && !lockedBySelf

          return (
            <div
              key={member.userId}
              data-testid="net-member-row"
              data-user-id={member.userId}
              className="flex items-center gap-2 border-b border-maia-border/40 px-2.5 py-1.5 last:border-b-0"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] text-maia-text">
                  {member.displayName || member.email || member.userId}
                </span>
                {member.email && member.displayName && (
                  <span className="block truncate text-[9px] text-maia-text-muted">{member.email}</span>
                )}
              </span>

              {rowManage ? (
                <select
                  data-testid="net-role-select"
                  value={member.role}
                  disabled={busy === `role:${member.userId}`}
                  onChange={(e) =>
                    void changeMemberRole(member.userId, e.target.value as TenantRole)
                  }
                  className="rounded border border-maia-border bg-maia-bg px-1.5 py-0.5 text-[10px] text-maia-text-secondary focus:border-maia-accent-border focus:outline-none"
                >
                  {/* owner 选项只对 Owner 显示：Admin 授予 Owner 会被后端 403 */}
                  {myRole === 'owner' && <option value="owner">所有者</option>}
                  <option value="admin">管理员</option>
                  <option value="member">成员</option>
                  <option value="viewer">访客</option>
                </select>
              ) : (
                <Badge variant={member.role === 'owner' ? 'accent' : 'default'}>
                  {ROLE_LABEL[member.role]}
                </Badge>
              )}

              {rowManage && !isSelf && (
                <Button
                  size="icon"
                  variant="ghost"
                  data-testid="net-remove-member-btn"
                  disabled={busy === `remove:${member.userId}`}
                  onClick={() => {
                    if (window.confirm(`移除成员 ${member.displayName || member.email}？`)) {
                      void removeMember(member.userId)
                    }
                  }}
                  title="移除成员"
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── 邀请 ──────────────────────────────────────────────────────────────

function InviteSection() {
  const invitations = useCollaborationStore((s) => s.invitations)
  const busy = useCollaborationStore((s) => s.busy)
  const inviteMember = useCollaborationStore((s) => s.inviteMember)
  const revokeInvitation = useCollaborationStore((s) => s.revokeInvitation)
  const [email, setEmail] = useState('')

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = email.trim()
    if (!trimmed) return
    const ok = await inviteMember(trimmed)
    if (ok) setEmail('')
  }

  return (
    <section className="mb-4">
      <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-maia-text-secondary">
        <UserPlus className="h-3.5 w-3.5" /> 邀请加入
      </h3>
      <form onSubmit={handleInvite} className="flex gap-1.5">
        <input
          data-testid="net-invite-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="对方注册用的邮箱"
          className="min-w-0 flex-1 rounded border border-maia-border bg-maia-bg px-2 py-1 text-[11px] text-maia-text placeholder:text-maia-text-muted/60 focus:border-maia-accent-border focus:outline-none"
        />
        <Button
          type="submit"
          size="sm"
          data-testid="net-invite-btn"
          disabled={!email.trim() || busy === 'invite'}
        >
          {busy === 'invite' ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <UserPlus className="h-3 w-3" />
          )}
          邀请
        </Button>
      </form>

      <InviteLinkBox />

      {invitations.length > 0 && (
        <ul className="mt-2 space-y-1" data-testid="net-invitation-list">
          {invitations.map((inv) => (
            <li
              key={inv.id}
              data-testid="net-invitation-row"
              className="flex items-center gap-2 rounded border border-maia-border/60 px-2 py-1"
            >
              <span className="min-w-0 flex-1 truncate text-[10px] text-maia-text-muted">
                待接受：{inv.email}
              </span>
              <button
                type="button"
                data-testid="net-revoke-invitation-btn"
                disabled={busy === `revoke:${inv.id}`}
                onClick={() => void revokeInvitation(inv.id)}
                className="shrink-0 text-[10px] text-maia-text-muted hover:text-maia-danger"
              >
                撤销
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ── 危险操作 ──────────────────────────────────────────────────────────

function DangerSection({ active }: { active: boolean }) {
  const leaveNetwork = useCollaborationStore((s) => s.leaveNetwork)
  const busy = useCollaborationStore((s) => s.busy)

  return (
    <section className="border-t border-maia-border/40 pt-3">
      <Button
        size="sm"
        variant="ghost"
        data-testid="net-leave-btn"
        // 退出也是写操作：网络非 active 时后端 409（service.py 手动补的状态门禁）
        disabled={busy === 'leave' || !active}
        title={active ? undefined : '网络已暂停/归档，无法退出'}
        onClick={() => {
          if (window.confirm('退出这个网络？之后需要重新被邀请才能加入。')) {
            void leaveNetwork()
          }
        }}
        className="hover:text-maia-danger"
      >
        退出网络
      </Button>
      <p className="mt-1 text-[10px] text-maia-text-muted">
        最后一位所有者不能退出，需先把所有权转给他人或归档网络。
      </p>
    </section>
  )
}

// ── 资源共享：发布、目录、安装（FR-N5 / FR-N6）──────────────────────

const RESOURCE_TYPE_LABEL: Record<ShareResourceType, string> = {
  agent: 'Agent',
  tool: '工具',
  data: '数据集',
}

/** 谁能发布到网络：Owner/Admin，或 member 且网络开了 allowMemberPublish。 */
function canPublish(
  role: TenantRole | null | undefined,
  policyAllowsMemberPublish: boolean
): boolean {
  if (role === 'owner' || role === 'admin') return true
  return role === 'member' && policyAllowsMemberPublish
}

function CatalogSection({
  role,
  policyAllowsMemberPublish,
  networkActive,
}: {
  role: TenantRole
  policyAllowsMemberPublish: boolean
  networkActive: boolean
}) {
  const catalog = useCollaborationStore((s) => s.catalog)
  const catalogLoading = useCollaborationStore((s) => s.catalogLoading)
  const installations = useCollaborationStore((s) => s.installations)
  const busy = useCollaborationStore((s) => s.busy)
  const members = useCollaborationStore((s) => s.members)
  const publishResource = useCollaborationStore((s) => s.publishResource)
  const revokePublication = useCollaborationStore((s) => s.revokePublication)
  const installResource = useCollaborationStore((s) => s.installResource)
  const setPublicationInvokePolicy = useCollaborationStore(
    (s) => s.setPublicationInvokePolicy
  )
  const currentUserId = useAuthStore((s) => s.user?.userId)

  // ── 「谁能调用」内联编辑器（FR-N7 按目标粒度授权）──
  // 服务端允许**发布者本人**或网络 Owner/Admin（core/sharing/service.py::
  // set_publication_invoke_policy），前端按同一口径显示入口。
  const [policyEditingId, setPolicyEditingId] = useState<string | null>(null)
  const [draftPolicy, setDraftPolicy] = useState<InvokePolicy>('network')
  const [draftAllowlist, setDraftAllowlist] = useState<string[]>([])

  function openPolicyEditor(pubId: string, policy: InvokePolicy, allowlist: string[]) {
    setPolicyEditingId(pubId)
    setDraftPolicy(policy)
    setDraftAllowlist(allowlist)
  }

  function toggleAllowlisted(userId: string) {
    setDraftAllowlist((prev) =>
      prev.includes(userId) ? prev.filter((x) => x !== userId) : [...prev, userId]
    )
  }

  async function savePolicy(pubId: string) {
    const ok = await setPublicationInvokePolicy(pubId, draftPolicy, draftAllowlist)
    if (ok) setPolicyEditingId(null)
  }

  const agentResources = useResourceStore((s) => s.agentResources)
  const toolResources = useResourceStore((s) => s.toolResources)
  const dataResources = useResourceStore((s) => s.dataResources)
  const fetchAllResources = useResourceStore((s) => s.fetchAllResources)

  const [resourceType, setResourceType] = useState<ShareResourceType>('agent')
  const [releaseId, setReleaseId] = useState('')

  useEffect(() => {
    // 供发布表单的候选列表。失败也不影响主流程（还能手输 id），故不处理错误。
    void fetchAllResources()
  }, [fetchAllResources])

  const publishable = canPublish(role, policyAllowsMemberPublish) && networkActive

  // 已安装判定：installation 记录的是 publicationId，这里做集合查找
  const installedPublicationIds = new Set(installations.map((i) => i.publicationId))

  // 候选资源（用于 datalist 自动补全）。不同资源类型来自不同 store 分片。
  const candidates =
    resourceType === 'agent'
      ? agentResources
      : resourceType === 'tool'
        ? toolResources
        : dataResources

  async function handlePublish(e: React.FormEvent) {
    e.preventDefault()
    const id = releaseId.trim()
    if (!id) return
    const ok = await publishResource(resourceType, id)
    if (ok) setReleaseId('')
  }

  return (
    <section className="mb-4">
      <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-maia-text-secondary">
        <Share2 className="h-3.5 w-3.5" /> 资源目录
        {catalogLoading && <Loader2 className="h-3 w-3 animate-spin" />}
      </h3>

      {/* 发布表单 —— 只有有发布权的人在网络 active 时才显示 */}
      {publishable ? (
        <form onSubmit={handlePublish} className="mb-2 flex items-center gap-1.5">
          <select
            data-testid="publish-type-select"
            value={resourceType}
            onChange={(e) => {
              setResourceType(e.target.value as ShareResourceType)
              setReleaseId('')
            }}
            className="shrink-0 rounded border border-maia-border bg-maia-bg px-1.5 py-1 text-[10px] text-maia-text-secondary focus:border-maia-accent-border focus:outline-none"
          >
            <option value="agent">Agent</option>
            <option value="tool">工具</option>
            <option value="data">数据集</option>
          </select>
          <input
            data-testid="publish-release-input"
            value={releaseId}
            onChange={(e) => setReleaseId(e.target.value)}
            list="publish-candidates"
            placeholder="选择或输入资源 id"
            className="min-w-0 flex-1 rounded border border-maia-border bg-maia-bg px-2 py-1 text-[11px] text-maia-text placeholder:text-maia-text-muted/60 focus:border-maia-accent-border focus:outline-none"
          />
          {/* datalist 提供自动补全，但不强制从列表选 —— release_id 允许任意版本号 */}
          <datalist id="publish-candidates">
            {candidates.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </datalist>
          <Button
            type="submit"
            size="sm"
            data-testid="publish-btn"
            disabled={!releaseId.trim() || busy === 'publish'}
          >
            {busy === 'publish' ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Share2 className="h-3 w-3" />
            )}
            发布
          </Button>
        </form>
      ) : (
        <p className="mb-2 text-[10px] leading-relaxed text-maia-text-muted">
          {networkActive
            ? '你没有发布权限（需要网络所有者/管理员；或由管理员开启「允许成员发布」策略）。'
            : '网络当前不可写，无法发布资源。'}
        </p>
      )}

      <div className="overflow-hidden rounded border border-maia-border" data-testid="catalog-list">
        {catalog.length === 0 && !catalogLoading && (
          <p
            data-testid="catalog-empty"
            className="px-2.5 py-2 text-[10px] leading-relaxed text-maia-text-muted"
          >
            该网络还没有共享资源。发布后，网络成员就能在这里看到并安装。
          </p>
        )}

        {catalog.map((pub) => {
          const installed = installedPublicationIds.has(pub.id)
          const revoking = busy === `revoke-pub:${pub.id}`
          const installing = busy === `install:${pub.resourceType}:${pub.releaseId}`
          const isMine = Boolean(currentUserId) && pub.publishedBy === currentUserId
          // 撤回发布 / 设置调用范围：发布者本人 **或** 网络 Owner/Admin
          // （服务端 revoke_publication / set_publication_invoke_policy 同口径）。
          // 另加 networkActive：suspended/archived 时写操作一律 409（admin 路径会被
          // 状态守卫拦下），暂停期间统一不给写入口，与状态提示条一致。
          const canTouchPublication = networkActive && (isMine || canManage(role))
          const savingPolicy = busy === `invoke-policy:${pub.id}`
          const editing = policyEditingId === pub.id
          return (
            <div
              key={pub.id}
              data-testid="catalog-row"
              data-release-id={pub.releaseId}
              data-resource-type={pub.resourceType}
              data-invoke-policy={pub.invokePolicy}
              className="border-b border-maia-border/40 last:border-b-0"
            >
              <div className="flex items-center gap-2 px-2.5 py-1.5">
              <Badge variant="default" className="shrink-0">
                {RESOURCE_TYPE_LABEL[pub.resourceType]}
              </Badge>
              <span className="min-w-0 flex-1 truncate text-[11px] text-maia-text">
                {pub.releaseId}
              </span>

              {/* 谁能调用 —— allowlist 才显示徽标，network 是默认状态不占位置 */}
              {pub.invokePolicy === 'allowlist' && (
                <Badge
                  variant="warning"
                  className="shrink-0"
                  data-testid="catalog-policy-badge"
                >
                  指定成员 ({pub.invokeAllowlist.length})
                </Badge>
              )}
              {canTouchPublication && (
                <Button
                  size="icon"
                  variant="ghost"
                  data-testid="catalog-policy-btn"
                  title="设置谁能调用"
                  disabled={savingPolicy}
                  onClick={() =>
                    editing
                      ? setPolicyEditingId(null)
                      : openPolicyEditor(pub.id, pub.invokePolicy, pub.invokeAllowlist)
                  }
                >
                  {savingPolicy ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <ShieldCheck className="h-3 w-3" />
                  )}
                </Button>
              )}
              <Button
                size="sm"
                variant={installed ? 'ghost' : 'outline'}
                data-testid="catalog-install-btn"
                // 安装是写操作：网络非 active 时后端 authorize 直接 409
                disabled={installed || installing || !networkActive}
                title={networkActive ? undefined : '网络已暂停/归档，无法安装'}
                onClick={() => void installResource(pub.resourceType, pub.releaseId)}
              >
                {installing ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : installed ? (
                  <Check className="h-3 w-3" />
                ) : (
                  <Package className="h-3 w-3" />
                )}
                {installed ? '已安装' : '安装'}
              </Button>
              {canTouchPublication && (
                <Button
                  size="icon"
                  variant="ghost"
                  data-testid="catalog-revoke-btn"
                  disabled={revoking}
                  title="撤回发布"
                  onClick={() => {
                    if (window.confirm(`撤回「${pub.releaseId}」的发布？已安装的成员不会自动卸载。`)) {
                      void revokePublication(pub.id)
                    }
                  }}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              )}
              </div>

              {/* 「谁能调用」编辑器 —— 只有发布者点开时才渲染 */}
              {editing && (
                <div
                  data-testid="policy-editor"
                  className="border-t border-maia-border/40 bg-maia-bg/40 px-2.5 py-2"
                >
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="text-[10px] tracking-wider text-maia-text-secondary">
                      谁能调用这个资源
                    </span>
                    <select
                      data-testid="policy-select"
                      value={draftPolicy}
                      onChange={(e) => setDraftPolicy(e.target.value as InvokePolicy)}
                      className="rounded border border-maia-border bg-maia-bg px-1.5 py-1 text-[10px] text-maia-text-secondary focus:border-maia-accent-border focus:outline-none"
                    >
                      <option value="network">网络内所有有权限的成员</option>
                      <option value="allowlist">仅指定成员</option>
                    </select>
                  </div>

                  {draftPolicy === 'allowlist' && (
                    <>
                      <p className="mb-1 text-[10px] leading-relaxed text-maia-text-muted">
                        只允许勾选的成员调用（他们的 Agent 才能调你的资源）。名单不能为空。
                      </p>
                      <div
                        className="mb-1.5 max-h-32 overflow-y-auto rounded border border-maia-border"
                        data-testid="policy-member-list"
                      >
                        {members.length === 0 && (
                          <p className="px-2 py-1.5 text-[10px] text-maia-text-muted">
                            网络里还没有其他成员
                          </p>
                        )}
                        {members.map((m) => (
                          <label
                            key={m.userId}
                            data-testid="policy-member-row"
                            data-user-id={m.userId}
                            className="flex cursor-pointer items-center gap-2 border-b border-maia-border/30 px-2 py-1 last:border-b-0"
                          >
                            <input
                              type="checkbox"
                              data-testid="policy-member-checkbox"
                              checked={draftAllowlist.includes(m.userId)}
                              onChange={() => toggleAllowlisted(m.userId)}
                              className="h-3 w-3 accent-maia-accent"
                            />
                            <span className="min-w-0 flex-1 truncate text-[10px] text-maia-text-secondary">
                              {m.displayName || m.email || m.userId}
                            </span>
                          </label>
                        ))}
                      </div>
                    </>
                  )}

                  <div className="flex justify-end gap-1.5">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setPolicyEditingId(null)}
                      disabled={savingPolicy}
                    >
                      取消
                    </Button>
                    <Button
                      size="sm"
                      data-testid="policy-save-btn"
                      disabled={savingPolicy}
                      onClick={() => void savePolicy(pub.id)}
                    >
                      {savingPolicy && <Loader2 className="h-3 w-3 animate-spin" />}
                      保存
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── 我的安装 tab（跨网络的用户级视图）───────────────────────────────

function InstalledTab() {
  const installations = useCollaborationStore((s) => s.installations)
  const installationsLoading = useCollaborationStore((s) => s.installationsLoading)
  const loadMyInstallations = useCollaborationStore((s) => s.loadMyInstallations)
  const uninstallResource = useCollaborationStore((s) => s.uninstallResource)
  const busy = useCollaborationStore((s) => s.busy)

  useEffect(() => {
    void loadMyInstallations()
  }, [loadMyInstallations])

  return (
    <div className="h-full overflow-y-auto p-4">
      <ErrorBar />

      <h2 className="mb-1 text-[15px] font-semibold text-maia-text-heading">我的安装</h2>
      <p className="mb-3 text-[10px] leading-relaxed tracking-wide text-maia-text-muted">
        这里是从各个协作网络安装到你工作空间的资源。卸下后，用这些资源签发的 Agent
        凭证会在「下一次调用」时立即失效。
      </p>

      {installationsLoading && installations.length === 0 && (
        <div className="flex items-center gap-2 text-[11px] text-maia-text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载中…
        </div>
      )}

      {!installationsLoading && installations.length === 0 && (
        <p
          data-testid="installed-empty"
          className="rounded border border-maia-border px-3 py-2 text-[11px] leading-relaxed text-maia-text-muted"
        >
          还没有安装任何资源。到「协作网络 → 某个网络 → 资源目录」里点「安装」。
        </p>
      )}

      <div className="space-y-1" data-testid="installed-list">
        {installations.map((inst) => (
          <div
            key={inst.id}
            data-testid="installed-row"
            data-installation-id={inst.id}
            className="flex items-center gap-2 rounded border border-maia-border px-2.5 py-1.5"
          >
            {inst.resourceType ? (
              <Badge variant="accent" className="shrink-0">
                {RESOURCE_TYPE_LABEL[inst.resourceType]}
              </Badge>
            ) : (
              <Badge variant="warning" className="shrink-0">
                已下架
              </Badge>
            )}
            <span className="min-w-0 flex-1 truncate text-[11px] text-maia-text">
              {/* 资源被清理后 publication 可能是孤儿，这里要优雅降级 */}
              {inst.releaseId ?? '（资源记录已删除）'}
            </span>
            <Button
              size="sm"
              variant="ghost"
              data-testid="uninstall-btn"
              disabled={busy === `uninstall:${inst.id}`}
              onClick={() => {
                if (window.confirm(`卸载「${inst.releaseId ?? '该资源'}」？`)) {
                  void uninstallResource(inst.id)
                }
              }}
              className="hover:text-maia-danger"
            >
              {busy === `uninstall:${inst.id}` ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Trash2 className="h-3 w-3" />
              )}
              卸载
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── 审计日志（FR-N9）─────────────────────────────────────────────────

const RESULT_VARIANT: Record<string, 'success' | 'danger' | 'warning'> = {
  success: 'success',
  denied: 'danger',
  failure: 'warning',
}

/** `2026-10-27T08:15:30+00:00` → `10-27 08:15:30`（本地时区由浏览器处理） */
function formatAuditTime(iso: string): string {
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
      d.getMinutes()
    )}:${pad(d.getSeconds())}`
  } catch {
    return iso
  }
}

/**
 * 审计日志 —— **只有 Owner/Admin 能拉到数据**（权限码 ``network.audit.read``）。
 *
 * 非管理员挂载时后端会返回 403，store 静默降级为空数组；这里据此**整块不渲染**，
 * 免得给用户看一个空壳。
 */
function AuditSection() {
  const audit = useCollaborationStore((s) => s.audit)
  const auditLoading = useCollaborationStore((s) => s.auditLoading)
  const loadAudit = useCollaborationStore((s) => s.loadAudit)
  const selectedNetworkId = useCollaborationStore((s) => s.selectedNetworkId)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let alive = true
    void loadAudit().then(() => {
      if (alive) setLoaded(true)
    })
    return () => {
      alive = false
    }
  }, [loadAudit, selectedNetworkId])

  // 非管理员：接口 403 → 空数组。直接不渲染这一块。
  if (loaded && audit.length === 0) return null

  return (
    <section className="mb-4">
      <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-maia-text-secondary">
        <ScrollText className="h-3.5 w-3.5" /> 审计日志
        {auditLoading && <Loader2 className="h-3 w-3 animate-spin" />}
        <button
          type="button"
          data-testid="audit-refresh-btn"
          onClick={() => void loadAudit()}
          className="ml-auto text-maia-text-muted transition-colors hover:text-maia-text-secondary"
          title="刷新"
        >
          <RefreshCw className="h-3 w-3" />
        </button>
      </h3>
      <div
        className="max-h-56 overflow-y-auto rounded border border-maia-border"
        data-testid="audit-list"
      >
        {audit.length === 0 && (
          <p className="px-2.5 py-2 text-[10px] text-maia-text-muted">暂无审计记录</p>
        )}
        {audit.map((event) => (
          <div
            key={event.id}
            data-testid="audit-row"
            data-action={event.action}
            data-actor-kind={event.actorKind}
            className="flex items-center gap-2 border-b border-maia-border/40 px-2.5 py-1 last:border-b-0"
          >
            <span className="shrink-0 font-mono text-[9px] text-maia-text-muted">
              {formatAuditTime(event.createdAt)}
            </span>
            <span className="min-w-0 flex-1 truncate text-[10px] text-maia-text-secondary">
              {event.action}
            </span>
            <Badge variant="default" className="shrink-0">
              {event.actorKind === 'workload' ? 'Agent' : '用户'}
            </Badge>
            <Badge variant={RESULT_VARIANT[event.result] ?? 'default'} className="shrink-0">
              {event.result}
            </Badge>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-maia-text-muted">仅显示最近 20 条。</p>
    </section>
  )
}

// ── 空间 tab（租户成员与邀请）────────────────────────────────────────

function SpaceTab() {
  const user = useAuthStore((s) => s.user)
  const tenantMembers = useCollaborationStore((s) => s.tenantMembers)
  const tenantInvitations = useCollaborationStore((s) => s.tenantInvitations)
  const tenantLoading = useCollaborationStore((s) => s.tenantLoading)
  const loadTenant = useCollaborationStore((s) => s.loadTenant)
  const inviteToTenant = useCollaborationStore((s) => s.inviteToTenant)
  const revokeTenantInvitation = useCollaborationStore((s) => s.revokeTenantInvitation)
  const busy = useCollaborationStore((s) => s.busy)
  const switchTenant = useCollaborationStore((s) => s.switchTenant)
  const switchingTenantId = useCollaborationStore((s) => s.switchingTenantId)

  const [email, setEmail] = useState('')

  useEffect(() => {
    void loadTenant()
  }, [loadTenant, user?.activeTenantId])

  const tenant = useMemo(
    () => user?.tenants.find((t) => t.id === user.activeTenantId),
    [user]
  )

  const manage = canManage(user?.tenantRole)

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = email.trim()
    if (!trimmed) return
    const ok = await inviteToTenant(trimmed)
    if (ok) setEmail('')
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      <ErrorBar />

      <h2 className="mb-1 text-[15px] font-semibold text-maia-text-heading" data-testid="space-name">
        {tenant?.name ?? '当前空间'}
      </h2>
      <p className="mb-3 text-[10px] tracking-wide text-maia-text-muted">
        你在本空间的角色：{user?.tenantRole ? ROLE_LABEL[user.tenantRole] : '—'}
        。协作网络属于空间，所以要把人拉进同一空间才能共享网络。
      </p>

      {/* 我所属的空间 —— 切换入口 */}
      <section className="mb-4">
        <h3 className="mb-1.5 text-[11px] font-semibold tracking-wider text-maia-text-secondary">
          我所属的空间
        </h3>
        <div className="space-y-1" data-testid="space-list">
          {user?.tenants.map((t) => {
            const isActive = t.id === user.activeTenantId
            return (
              <div
                key={t.id}
                data-testid="space-item"
                data-tenant-id={t.id}
                className="flex items-center gap-2 rounded border border-maia-border px-2.5 py-1.5"
              >
                <span className="min-w-0 flex-1 truncate text-[11px] text-maia-text">
                  {t.name}
                  <span className="ml-1.5 text-[9px] text-maia-text-muted">
                    {ROLE_LABEL[t.role]}
                  </span>
                </span>
                {isActive ? (
                  <Badge variant="accent">当前</Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    data-testid="space-switch-btn"
                    disabled={switchingTenantId === t.id}
                    onClick={() => void switchTenant(t.id)}
                  >
                    {switchingTenantId === t.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Play className="h-3 w-3" />
                    )}
                    切换
                  </Button>
                )}
              </div>
            )
          })}
        </div>
      </section>

      {/* 空间成员 */}
      <section className="mb-4">
        <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-maia-text-secondary">
          <Users className="h-3.5 w-3.5" /> 空间成员
          {tenantLoading && <Loader2 className="h-3 w-3 animate-spin" />}
        </h3>
        <div className="overflow-hidden rounded border border-maia-border" data-testid="space-members">
          {tenantMembers.map((m) => (
            <div
              key={m.userId}
              data-testid="space-member-row"
              data-user-id={m.userId}
              className="flex items-center gap-2 border-b border-maia-border/40 px-2.5 py-1.5 last:border-b-0"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] text-maia-text">
                  {m.displayName || m.email || m.userId}
                </span>
                {m.email && m.displayName && (
                  <span className="block truncate text-[9px] text-maia-text-muted">{m.email}</span>
                )}
              </span>
              <Badge variant={m.role === 'owner' ? 'accent' : 'default'}>
                {ROLE_LABEL[m.role]}
              </Badge>
            </div>
          ))}
        </div>
      </section>

      {/* 邀请进空间 —— 只有 owner/admin 能邀请 */}
      {manage ? (
        <section>
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-maia-text-secondary">
            <UserPlus className="h-3.5 w-3.5" /> 邀请加入空间
          </h3>
          <form onSubmit={handleInvite} className="flex gap-1.5">
            <input
              data-testid="space-invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="对方注册用的邮箱"
              className="min-w-0 flex-1 rounded border border-maia-border bg-maia-bg px-2 py-1 text-[11px] text-maia-text placeholder:text-maia-text-muted/60 focus:border-maia-accent-border focus:outline-none"
            />
            <Button
              type="submit"
              size="sm"
              data-testid="space-invite-btn"
              disabled={!email.trim() || busy === 'invite-tenant'}
            >
              {busy === 'invite-tenant' ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <UserPlus className="h-3 w-3" />
              )}
              邀请
            </Button>
          </form>

          <InviteLinkBox />

          {tenantInvitations.length > 0 && (
            <ul className="mt-2 space-y-1" data-testid="space-invitation-list">
              {tenantInvitations.map((inv) => (
                <li
                  key={inv.id}
                  data-testid="space-invitation-row"
                  className="flex items-center gap-2 rounded border border-maia-border/60 px-2 py-1"
                >
                  <span className="min-w-0 flex-1 truncate text-[10px] text-maia-text-muted">
                    待接受：{inv.email}
                  </span>
                  <button
                    type="button"
                    data-testid="space-revoke-invitation-btn"
                    disabled={busy === `revoke-tenant:${inv.id}`}
                    onClick={() => void revokeTenantInvitation(inv.id)}
                    className="shrink-0 text-[10px] text-maia-text-muted hover:text-maia-danger"
                  >
                    撤销
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <p className="text-[10px] text-maia-text-muted" data-testid="space-invite-forbidden">
          只有空间的所有者或管理员可以邀请新成员。
        </p>
      )}
    </div>
  )
}

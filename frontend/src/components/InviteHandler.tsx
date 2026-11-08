/**
 * 邀请接受处理器 —— 让邀请链接**真的能用**。
 *
 * ## 问题
 *
 * 后端把邀请的明文令牌在创建时返回给邀请人，令牌库里只存 SHA-256。
 * 邀请人把它拼成链接发给被邀请人（`?invite=<token>` 或 `?tenantInvite=<token>`）。
 * 被邀请人点开链接时，前端必须把它们接住并调接受接口 —— 否则这个链接就是死链。
 *
 * ## 为什么要立刻清掉 URL 参数
 *
 * 接受接口对**同一令牌只能成功一次**（第二次返回 410）。如果参数留在 URL 里，
 * 一次刷新就会再提交一遍，用户会看到"邀请已失效"这种莫名其妙的提示。
 * 所以解析后马上 `history.replaceState`，并**在提交成功前**就清掉。
 *
 * ## 两种邀请的区别
 *
 * - `tenantInvite` —— 加入空间。接受后要刷新 `/me`，因为租户列表变了。
 * - `invite` —— 加入网络。前提是**调用者已经在该网络所属的空间里**，
 *   否则后端返回 404（跨租户不可见）。
 */

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, X } from 'lucide-react'

import { ApiError, describeError } from '@/services/api/client'
import { networkApi } from '@/services/api/network'
import { tenantApi } from '@/services/api/tenant'
import { useAuthStore } from '@/stores/auth-store'

interface BannerState {
  ok: boolean
  message: string
}

/** 把接受失败的原因翻译成对人有用的一句话（状态码优先，不解析后端文案） */
function explainAcceptError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 410) return '邀请已被使用或已过期，请让对方重新邀请。'
    if (err.status === 403) return '这个邀请不是发给当前账号的，请用被邀请的邮箱登录。'
    if (err.status === 404) return '邀请无效，或你还不属于该网络所在的空间。'
    if (err.status === 409) return err.detail || '当前状态无法加入，请稍后再试。'
  }
  return describeError(err)
}

export function InviteHandler() {
  const [banner, setBanner] = useState<BannerState | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const networkToken = params.get('invite')
    const tenantToken = params.get('tenantInvite')
    if (!networkToken && !tenantToken) return

    // 先从 URL 抹掉，避免刷新重复提交（同一令牌二次使用会 410）
    params.delete('invite')
    params.delete('tenantInvite')
    const query = params.toString()
    window.history.replaceState(
      {},
      '',
      `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`
    )

    async function run() {
      try {
        if (tenantToken) {
          const tenant = await tenantApi.accept(tenantToken)
          // 租户列表 / activeTenantId 在 /me 里，必须刷新
          await useAuthStore.getState().refresh()
          setBanner({
            ok: true,
            message: `已加入空间「${tenant.name}」。到「协作网络 → 空间」可以切换过去。`,
          })
          return
        }
        if (networkToken) {
          const network = await networkApi.acceptInvitation(networkToken)
          setBanner({ ok: true, message: `已加入网络「${network.name}」。` })
        }
      } catch (err) {
        setBanner({ ok: false, message: explainAcceptError(err) })
      }
    }

    void run()
  }, [])

  if (!banner) return null

  return (
    <div
      data-testid="invite-banner"
      className="fixed right-4 top-14 z-[100] flex max-w-sm items-start gap-2 rounded-lg border border-maia-border bg-maia-surface px-3 py-2.5 shadow-2xl"
    >
      {banner.ok ? (
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-maia-success" />
      ) : (
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-maia-danger" />
      )}
      <span
        data-testid="invite-banner-text"
        className={`flex-1 text-[11px] leading-relaxed ${
          banner.ok ? 'text-maia-text' : 'text-maia-danger'
        }`}
      >
        {banner.message}
      </span>
      <button
        type="button"
        onClick={() => setBanner(null)}
        className="shrink-0 text-maia-text-muted hover:text-maia-text"
        aria-label="关闭提示"
        data-testid="invite-banner-close"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

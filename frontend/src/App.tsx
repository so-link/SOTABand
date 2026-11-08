/**
 * 应用入口 —— 认证状态分流。
 *
 * ## 为什么不用路由库
 *
 * 项目本来没有 react-router（改造前这里只有 5 行，直接渲染 `<MainLayout/>`）。
 * 为一个登录页引入路由库会带来"路由守卫怎么写""刷新丢失路径"等一整套新问题，
 * 而收益很小。用状态机分流更贴合现有风格，也更容易保证 PRD FR-A7 的要求：
 *
 * > 受保护页面**无未登录闪现**
 *
 * 关键在于 `unknown` 阶段渲染骨架屏 —— 不能先画主界面再跳走。
 */

import { useEffect } from 'react'
import { Loader2 } from 'lucide-react'

import { MainLayout } from '@/layouts/MainLayout'
import { InviteHandler } from '@/components/InviteHandler'
import { useAuthStore } from '@/stores/auth-store'
import { LoginView } from '@/views/LoginView'

/** 会话探测期间的占位。刻意"什么都没有"，避免未登录内容闪现。 */
function AuthBootstrap() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-maia-bg">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-maia-accent-border bg-maia-accent-light">
        <Loader2 className="h-5 w-5 animate-spin text-maia-accent" />
      </div>
      <span className="text-[11px] tracking-wider text-maia-text-muted">正在恢复会话…</span>
    </div>
  )
}

export default function App() {
  const status = useAuthStore((s) => s.status)
  const bootstrap = useAuthStore((s) => s.bootstrap)

  useEffect(() => {
    // bootstrap 内部有并发去重（StrictMode 下 effect 会跑两次）
    void bootstrap()
  }, [bootstrap])

  if (status === 'unknown') return <AuthBootstrap />
  if (status !== 'authenticated') return <LoginView />
  return (
    <>
      <MainLayout />
      {/* 接住邀请链接（?invite= / ?tenantInvite=）—— 必须在已认证后才挂载 */}
      <InviteHandler />
    </>
  )
}

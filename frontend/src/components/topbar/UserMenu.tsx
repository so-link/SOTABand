/**
 * 顶栏用户菜单 —— 显示当前登录用户，提供退出入口。
 *
 * 登录功能落地后，顶栏原来那个硬编码的 `<Avatar fallback="J" />` 会一直显示
 * 同一个字母，等于**看不出是谁登录了**。这个组件把它换成真实用户，
 * 同时给"退出登录 / 退出全部设备"一个自然的位置。
 *
 * 用状态 + 点击外部关闭实现下拉，不引额外依赖（项目里没有装 dropdown-menu）。
 */

import { useEffect, useRef, useState } from 'react'
import { KeyRound, LogOut, Network, ShieldOff } from 'lucide-react'

import { ChangePasswordDialog } from '@/components/account/ChangePasswordDialog'
import { Avatar } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import { selectDisplayName, useAuthStore } from '@/stores/auth-store'
import { useUIStore } from '@/stores/ui-store'
import type { TenantRole } from '@/types/auth'

const ROLE_LABEL: Record<TenantRole, string> = {
  owner: '所有者',
  admin: '管理员',
  member: '成员',
  viewer: '访客',
}

function initialOf(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '?'
  // 中文取名首字，英文取首字母
  return Array.from(trimmed)[0].toUpperCase()
}

export function UserMenu() {
  const displayName = useAuthStore(selectDisplayName)
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const logoutAll = useAuthStore((s) => s.logoutAll)
  const setActiveView = useUIStore((s) => s.setActiveView)

  const [open, setOpen] = useState(false)
  // 改密码弹窗独立于下拉菜单的开关状态 —— 菜单会先关闭，弹窗要留着
  const [changePasswordOpen, setChangePasswordOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  // 点击外部 / 按 Esc 关闭
  useEffect(() => {
    if (!open) return

    function onPointerDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }

    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const role = user?.tenantRole
  const tenant = user?.tenants.find((t) => t.id === user.activeTenantId)

  async function handleLogout() {
    setOpen(false)
    await logout()
  }

  async function handleLogoutAll() {
    setOpen(false)
    const ok = window.confirm('将退出所有设备的登录状态，确定继续？')
    if (ok) await logoutAll()
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={displayName}
        className={cn(
          'rounded-full transition-all duration-150',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-maia-accent/40',
          open && 'ring-2 ring-maia-accent/40'
        )}
      >
        <Avatar fallback={initialOf(displayName)} className="h-6 w-6 text-[10px] tracking-wider" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-1.5 w-56 overflow-hidden rounded-lg border border-maia-border bg-maia-surface shadow-2xl"
        >
          <div className="border-b border-maia-border px-3 py-2.5">
            <div className="truncate text-[12px] font-semibold tracking-wide text-maia-text-heading">
              {displayName}
            </div>
            {user?.email && (
              <div className="mt-0.5 truncate text-[10px] tracking-wide text-maia-text-muted">
                {user.email}
              </div>
            )}
          </div>

          {(tenant || role) && (
            <div className="border-b border-maia-border px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                {tenant && (
                  <span className="truncate text-[10px] tracking-wide text-maia-text-secondary">
                    {tenant.name}
                  </span>
                )}
                {role && (
                  <span className="shrink-0 rounded border border-maia-accent-border bg-maia-accent-light px-1.5 py-0.5 text-[9px] tracking-wider text-maia-accent">
                    {ROLE_LABEL[role]}
                  </span>
                )}
              </div>
            </div>
          )}

          <button
            type="button"
            role="menuitem"
            data-testid="open-networks-menu"
            onClick={() => {
              setOpen(false)
              setActiveView('networks')
            }}
            className="flex w-full items-center gap-2 border-t border-maia-border px-3 py-2 text-left text-[11px] tracking-wide text-maia-text-secondary transition-colors hover:bg-maia-sidebar-hover hover:text-maia-text"
          >
            <Network className="h-3.5 w-3.5" />
            协作网络与空间
          </button>

          <button
            type="button"
            role="menuitem"
            data-testid="open-change-password"
            onClick={() => {
              setOpen(false)
              setChangePasswordOpen(true)
            }}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] tracking-wide text-maia-text-secondary transition-colors hover:bg-maia-sidebar-hover hover:text-maia-text"
          >
            <KeyRound className="h-3.5 w-3.5" />
            修改密码
          </button>

          <button
            type="button"
            role="menuitem"
            onClick={handleLogout}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] tracking-wide text-maia-text-secondary transition-colors hover:bg-maia-sidebar-hover hover:text-maia-text"
          >
            <LogOut className="h-3.5 w-3.5" />
            退出登录
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={handleLogoutAll}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] tracking-wide text-maia-text-secondary transition-colors hover:bg-maia-sidebar-hover hover:text-maia-danger"
          >
            <ShieldOff className="h-3.5 w-3.5" />
            退出全部设备
          </button>
        </div>
      )}

      {/* 改密码弹窗 —— 具体逻辑在组件内，这里只负责开关 */}
      <ChangePasswordDialog
        open={changePasswordOpen}
        onClose={() => setChangePasswordOpen(false)}
      />
    </div>
  )
}

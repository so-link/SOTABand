"""权限码与角色真值表 —— 契约 §5 的**唯一真值源**。

本模块把 ``docs/contract-08-auth-network.md`` §5.2 的真值表用代码表达出来。
**权限判定只允许走这里**，不要在路由里写 ``if role == "owner"`` —— 那是最容易
产生规则漂移的写法（改一处漏一处）。

## 角色 → 权限

| 权限码 | owner | admin | member | viewer |
|---|:---:|:---:|:---:|:---:|
| ``network.read`` | ✅ | ✅ | ✅ | ✅ |
| ``network.update`` | ✅ | ✅ | ❌ | ❌ |
| ``network.archive`` | ✅ | ❌ | ❌ | ❌ |
| ``network.members.read`` | ✅ | ✅ | ✅ | ✅ |
| ``network.members.manage`` | ✅ | ✅¹ | ❌ | ❌ |
| ``network.invite.create`` | ✅ | ✅ | ❌ | ❌ |
| ``network.invite.revoke`` | ✅ | ✅ | ❌ | ❌ |
| ``network.resource.publish`` | ✅ | ✅ | 策略² | ❌ |
| ``network.resource.install`` | ✅ | ✅ | ✅ | ✅ |
| ``network.agent.invoke`` | ✅ | ✅ | ✅ | 策略³ |
| ``network.audit.read`` | ✅ | ✅ | ❌ | ❌ |

¹ Admin 的成员管理**受限**：不可对 Owner 行使、不可授予/剥夺 Owner。
  这属于"同一权限码下的额外约束"，在 ``core/network/service.py`` 里检查，
  不在真值表里表达（真值表只回答"角色有没有这个权限码"）。
² ``networks.policy.allowMemberPublish``，默认 ``false``。
³ ``networks.policy.allowViewerInvoke``，默认 ``false``。
"""

from __future__ import annotations

from core.db.models import (
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    NETWORK_ROLE_VIEWER,
)

# ── 权限码（契约 §5.1）────────────────────────────────────────────────

NETWORK_READ = "network.read"
NETWORK_UPDATE = "network.update"
NETWORK_ARCHIVE = "network.archive"
NETWORK_MEMBERS_READ = "network.members.read"
NETWORK_MEMBERS_MANAGE = "network.members.manage"
NETWORK_INVITE_CREATE = "network.invite.create"
NETWORK_INVITE_REVOKE = "network.invite.revoke"
NETWORK_RESOURCE_PUBLISH = "network.resource.publish"
NETWORK_RESOURCE_INSTALL = "network.resource.install"
NETWORK_AGENT_INVOKE = "network.agent.invoke"
NETWORK_AUDIT_READ = "network.audit.read"

ALL_PERMISSIONS = frozenset(
    {
        NETWORK_READ,
        NETWORK_UPDATE,
        NETWORK_ARCHIVE,
        NETWORK_MEMBERS_READ,
        NETWORK_MEMBERS_MANAGE,
        NETWORK_INVITE_CREATE,
        NETWORK_INVITE_REVOKE,
        NETWORK_RESOURCE_PUBLISH,
        NETWORK_RESOURCE_INSTALL,
        NETWORK_AGENT_INVOKE,
        NETWORK_AUDIT_READ,
    }
)

#: 只读权限 —— 网络 suspended 时仍可用；archived 时对非 owner/admin 视作不存在
READ_PERMISSIONS = frozenset({NETWORK_READ, NETWORK_MEMBERS_READ, NETWORK_AUDIT_READ})

# ── 角色 → 基础权限集合（不含策略开关）────────────────────────────────

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    NETWORK_ROLE_OWNER: ALL_PERMISSIONS,
    # Admin：除归档外全部（归档是 Owner 独占，契约 §5.2）
    NETWORK_ROLE_ADMIN: ALL_PERMISSIONS - {NETWORK_ARCHIVE},
    # Member：发布需策略开关，所以不在基础集合里
    NETWORK_ROLE_MEMBER: frozenset(
        {
            NETWORK_READ,
            NETWORK_MEMBERS_READ,
            NETWORK_RESOURCE_INSTALL,
            NETWORK_AGENT_INVOKE,
        }
    ),
    # Viewer：调用需策略开关，所以不在基础集合里
    NETWORK_ROLE_VIEWER: frozenset(
        {NETWORK_READ, NETWORK_MEMBERS_READ, NETWORK_RESOURCE_INSTALL}
    ),
}

#: 「角色 + 权限」若出现在这里，说明它由 policy 开关控制而非角色本身
_POLICY_GATED: dict[tuple[str, str], str] = {
    (NETWORK_ROLE_MEMBER, NETWORK_RESOURCE_PUBLISH): "allowMemberPublish",
    (NETWORK_ROLE_VIEWER, NETWORK_AGENT_INVOKE): "allowViewerInvoke",
}


def role_permissions(role: str) -> frozenset[str]:
    """返回角色的基础权限集合（不考虑策略开关）。未知角色返回空集（默认拒绝）。"""
    return _ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: str, permission: str, *, policy: dict | None = None) -> bool:
    """判定某角色在某网络中是否拥有该权限。

    ``policy`` 是 ``network.policy`` 的内容，用于两个策略开关：
    ``allowMemberPublish`` / ``allowViewerInvoke``。

    **未知角色 / 未知权限一律返回 False**（默认拒绝，契约 §6 最后一句）。
    """
    if permission not in ALL_PERMISSIONS:
        return False

    if permission in role_permissions(role):
        return True

    flag = _POLICY_GATED.get((role, permission))
    if flag is not None:
        return bool((policy or {}).get(flag, False))

    return False


def is_read_permission(permission: str) -> bool:
    return permission in READ_PERMISSIONS


__all__ = [
    "ALL_PERMISSIONS",
    "NETWORK_AGENT_INVOKE",
    "NETWORK_ARCHIVE",
    "NETWORK_AUDIT_READ",
    "NETWORK_INVITE_CREATE",
    "NETWORK_INVITE_REVOKE",
    "NETWORK_MEMBERS_MANAGE",
    "NETWORK_MEMBERS_READ",
    "NETWORK_READ",
    "NETWORK_RESOURCE_INSTALL",
    "NETWORK_RESOURCE_PUBLISH",
    "NETWORK_UPDATE",
    "READ_PERMISSIONS",
    "has_permission",
    "is_read_permission",
    "role_permissions",
]

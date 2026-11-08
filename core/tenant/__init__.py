"""租户（需求 8.1 / FR-A6）—— 租户创建、邀请、加入与切换。

设计依据：

- 契约 §3.1（``/api/v1/tenants``、``/me/switch-tenant``、``/me/tenants``）
- ``docs/design-08-schema.md`` §3.6–§3.7
- 邀请制的取舍见 ``core/db/models/tenant.py`` 模块文档

## 为什么这一步存在

注册时每个用户自动获得**自己的个人租户**，网络又**属且仅属一个租户**
（PRD 开放问题 2）。所以两人要共享一个网络，必须先身处同一个租户 —— 而
「加入他人租户」正是本模块提供的能力。**没有它，8.2 的多用户流程走不通。**
"""

from core.tenant.service import (
    accept_tenant_invitation,
    create_tenant,
    create_tenant_invitation,
    list_tenant_invitations,
    list_tenant_members,
    revoke_tenant_invitation,
    switch_active_tenant,
)

__all__ = [
    "accept_tenant_invitation",
    "create_tenant",
    "create_tenant_invitation",
    "list_tenant_invitations",
    "list_tenant_members",
    "revoke_tenant_invitation",
    "switch_active_tenant",
]

"""网络（需求 8.2）—— 虚拟协作网络。

设计见 ``docs/design-08-02-network.md``；契约见
``docs/contract-08-auth-network.md`` §3.2、§5、§6。

模块划分：

| 模块 | 职责 |
|---|---|
| :mod:`core.auth.permissions` | 权限码与角色真值表（唯一真值源） |
| :mod:`core.auth.authorize` | 契约 §6 的授权判定链 |
| :mod:`core.network.service` | 网络 / 成员 / 邀请的业务规则 |
"""

from core.network.service import (
    accept_invitation,
    change_member_role,
    change_network_status,
    create_invitation,
    create_network,
    get_members,
    leave_network,
    list_invitations,
    list_network_audit,
    list_user_networks,
    remove_member,
    revoke_invitation,
    update_network,
)

__all__ = [
    "accept_invitation",
    "change_member_role",
    "change_network_status",
    "create_invitation",
    "create_network",
    "get_members",
    "leave_network",
    "list_invitations",
    "list_network_audit",
    "list_user_networks",
    "remove_member",
    "revoke_invitation",
    "update_network",
]

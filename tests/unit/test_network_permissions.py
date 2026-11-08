"""角色权限真值表测试 —— 契约 §5.2 的**可执行副本**。

这张表是 ``docs/contract-08-auth-network.md`` §5.2 的直接翻译。任何一行不一致，
要么是代码错了，要么是契约改了但没同步 —— 两种都必须立刻发现。
"""

from __future__ import annotations

import pytest

from core.auth.permissions import (
    ALL_PERMISSIONS,
    NETWORK_AGENT_INVOKE,
    NETWORK_ARCHIVE,
    NETWORK_AUDIT_READ,
    NETWORK_INVITE_CREATE,
    NETWORK_INVITE_REVOKE,
    NETWORK_MEMBERS_MANAGE,
    NETWORK_MEMBERS_READ,
    NETWORK_READ,
    NETWORK_RESOURCE_INSTALL,
    NETWORK_RESOURCE_PUBLISH,
    NETWORK_UPDATE,
    has_permission,
    is_read_permission,
    role_permissions,
)
from core.db.models import (
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    NETWORK_ROLE_VIEWER,
)

ROLES = (
    NETWORK_ROLE_OWNER,
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_VIEWER,
)

#: 契约 §5.2 的真值表（owner, admin, member, viewer）。
#: member 的 publish 与 viewer 的 invoke 是**策略开关**，默认 false，单独测。
TRUTH_TABLE: dict[str, tuple[bool, bool, bool, bool]] = {
    NETWORK_READ: (True, True, True, True),
    NETWORK_UPDATE: (True, True, False, False),
    NETWORK_ARCHIVE: (True, False, False, False),
    NETWORK_MEMBERS_READ: (True, True, True, True),
    NETWORK_MEMBERS_MANAGE: (True, True, False, False),
    NETWORK_INVITE_CREATE: (True, True, False, False),
    NETWORK_INVITE_REVOKE: (True, True, False, False),
    NETWORK_RESOURCE_INSTALL: (True, True, True, True),
    NETWORK_AUDIT_READ: (True, True, False, False),
    # 策略开关关闭时 member/viewer 都没有
    NETWORK_RESOURCE_PUBLISH: (True, True, False, False),
    NETWORK_AGENT_INVOKE: (True, True, True, False),
}


@pytest.mark.parametrize("permission", sorted(TRUTH_TABLE))
def test_truth_table_matches_contract(permission: str) -> None:
    expected = TRUTH_TABLE[permission]
    actual = tuple(
        has_permission(role, permission, policy={}) for role in ROLES
    )
    assert actual == expected, (
        f"权限 {permission} 与契约 §5.2 不一致："
        f"期望 {dict(zip(ROLES, expected))}，实际 {dict(zip(ROLES, actual))}"
    )


def test_all_permissions_are_covered_by_the_truth_table() -> None:
    """新增权限码必须同时更新真值表 —— 否则它就是"默认全拒绝"的隐形黑洞。"""
    missing = ALL_PERMISSIONS - set(TRUTH_TABLE)
    assert not missing, f"这些权限码没有在真值表里覆盖：{missing}"


class TestPolicyGates:
    """两个策略开关（``network.policy``）的行为。"""

    def test_member_cannot_publish_by_default(self) -> None:
        assert not has_permission(
            NETWORK_ROLE_MEMBER, NETWORK_RESOURCE_PUBLISH, policy={}
        )

    def test_member_can_publish_when_policy_allows(self) -> None:
        assert has_permission(
            NETWORK_ROLE_MEMBER,
            NETWORK_RESOURCE_PUBLISH,
            policy={"allowMemberPublish": True},
        )

    def test_viewer_cannot_invoke_by_default(self) -> None:
        assert not has_permission(
            NETWORK_ROLE_VIEWER, NETWORK_AGENT_INVOKE, policy={}
        )

    def test_viewer_can_invoke_when_policy_allows(self) -> None:
        assert has_permission(
            NETWORK_ROLE_VIEWER,
            NETWORK_AGENT_INVOKE,
            policy={"allowViewerInvoke": True},
        )

    def test_policy_does_not_escalate_hard_denials(self) -> None:
        """策略开关不能把"角色根本没有的权限"打开。"""
        assert not has_permission(
            NETWORK_ROLE_VIEWER,
            NETWORK_MEMBERS_MANAGE,
            policy={"allowMemberPublish": True, "allowViewerInvoke": True},
        )
        assert not has_permission(
            NETWORK_ROLE_MEMBER,
            NETWORK_ARCHIVE,
            policy={"allowMemberPublish": True},
        )


class TestDefaultDeny:
    """默认拒绝（契约 §6 最后一句）。"""

    def test_unknown_role_has_no_permissions(self) -> None:
        assert role_permissions("superuser") == frozenset()
        for permission in ALL_PERMISSIONS:
            assert not has_permission("superuser", permission)

    def test_unknown_permission_is_denied(self) -> None:
        assert not has_permission(NETWORK_ROLE_OWNER, "network.sudo")

    def test_none_role_is_denied(self) -> None:
        for permission in ALL_PERMISSIONS:
            assert not has_permission(None, permission)  # type: ignore[arg-type]


class TestReadPermissionClassification:
    """只读权限的分类决定"网络 suspended 时还能不能看"。"""

    def test_read_permissions(self) -> None:
        for permission in (NETWORK_READ, NETWORK_MEMBERS_READ, NETWORK_AUDIT_READ):
            assert is_read_permission(permission)

    def test_write_permissions_are_not_read(self) -> None:
        for permission in (
            NETWORK_UPDATE,
            NETWORK_ARCHIVE,
            NETWORK_MEMBERS_MANAGE,
            NETWORK_INVITE_CREATE,
            NETWORK_INVITE_REVOKE,
            NETWORK_RESOURCE_PUBLISH,
            NETWORK_AGENT_INVOKE,
            NETWORK_RESOURCE_INSTALL,
        ):
            assert not is_read_permission(permission)


def test_archive_is_owner_only() -> None:
    """归档是 Owner 独占 —— 这是 owner 与 admin 的**唯一**区别。

    如果这条测试挂了，说明真值表里 admin 的集合被改动了，要回头核对契约 §5.2。
    """
    owner_perms = role_permissions(NETWORK_ROLE_OWNER)
    admin_perms = role_permissions(NETWORK_ROLE_ADMIN)
    assert owner_perms - admin_perms == {NETWORK_ARCHIVE}

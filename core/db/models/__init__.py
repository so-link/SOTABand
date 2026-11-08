"""ORM 模型包。

**新增模型必须在这里 import** —— Alembic 的 autogenerate 通过 ``Base.metadata``
发现表，而 ``Base.metadata`` 只包含**已被导入过**的模型。漏掉一行 import 的
后果是：迁移里静默少了那张表，直到运行时才报 "no such table"。

批次划分见 ``docs/design-08-schema.md`` §1（表名一律**单数**，阿里开发手册【强制】）：

- 批次 1（本文件当前内容）：``user`` / ``user_identity`` / ``user_credential`` /
  ``session`` / ``audit_event``
- 批次 2：``tenant`` / ``tenant_membership`` / ``tenant_invitation``
- 批次 3：``network`` / ``network_membership`` / ``network_invitation``
- 批次 4：``installation``（用户拍板新增的第 12 张表）
- 批次 5：``publication``（Step 5）
"""

from core.db.models.audit import (
    ACTOR_SYSTEM,
    ACTOR_USER,
    ACTOR_WORKLOAD,
    RESULT_DENIED,
    RESULT_FAILURE,
    RESULT_SUCCESS,
    AuditEvent,
)
from core.db.models.auth import User, UserCredential, UserIdentity, UserSession
from core.db.models.network import (
    DEFAULT_POLICY,
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REJECTED,
    INVITATION_REVOKED,
    INVITATION_STATUSES,
    INVITATION_TTL_DAYS,
    NETWORK_ACTIVE,
    NETWORK_ARCHIVED,
    NETWORK_MEMBERSHIP_ACTIVE,
    NETWORK_MEMBERSHIP_REMOVED,
    NETWORK_MEMBERSHIP_STATUSES,
    NETWORK_ROLE_ADMIN,
    NETWORK_ROLE_MEMBER,
    NETWORK_ROLE_OWNER,
    NETWORK_ROLE_VIEWER,
    NETWORK_ROLES,
    NETWORK_STATUSES,
    NETWORK_SUSPENDED,
    Network,
    NetworkInvitation,
    NetworkMembership,
    default_policy,
)
from core.db.models.sharing import (
    INSTALLATION_ACTIVE,
    INSTALLATION_REVOKED,
    INSTALLATION_STATUSES,
    INVOKE_POLICIES,
    INVOKE_POLICY_ALLOWLIST,
    INVOKE_POLICY_NETWORK,
    PUBLICATION_ACTIVE,
    PUBLICATION_REVOKED,
    PUBLICATION_SCOPE_NETWORK,
    PUBLICATION_SCOPE_PRIVATE,
    PUBLICATION_SCOPE_PUBLIC,
    PUBLICATION_SCOPE_TENANT,
    PUBLICATION_SCOPES,
    PUBLICATION_STATUSES,
    RESOURCE_TYPE_AGENT,
    RESOURCE_TYPE_DATA,
    RESOURCE_TYPE_TOOL,
    RESOURCE_TYPES,
    SCOPES_REQUIRING_SCOPE_ID,
    Installation,
    Publication,
)
from core.db.models.tenant import (
    MEMBERSHIP_ACTIVE,
    MEMBERSHIP_INVITED,
    MEMBERSHIP_REMOVED,
    TENANT_INVITATION_ACCEPTED,
    TENANT_INVITATION_EXPIRED,
    TENANT_INVITATION_PENDING,
    TENANT_INVITATION_REVOKED,
    TENANT_INVITATION_STATUSES,
    TENANT_INVITATION_TTL_DAYS,
    TENANT_ROLE_ADMIN,
    TENANT_ROLE_MEMBER,
    TENANT_ROLE_OWNER,
    TENANT_ROLE_VIEWER,
    TENANT_ROLES,
    Tenant,
    TenantInvitation,
    TenantMembership,
)

# 刻意不按字母序：这里按批次分组，分组本身就是文档。
# 新增模型时请追加到对应批次下，不要重排整个列表。
__all__ = [  # noqa: RUF022
    # 批次 1 —— 认证核心
    "User",
    "UserIdentity",
    "UserCredential",
    "UserSession",
    # 批次 1 —— 审计
    "AuditEvent",
    "ACTOR_USER",
    "ACTOR_WORKLOAD",
    "ACTOR_SYSTEM",
    "RESULT_SUCCESS",
    "RESULT_FAILURE",
    "RESULT_DENIED",
    # 批次 2 —— 租户
    "Tenant",
    "TenantMembership",
    "TENANT_ROLE_OWNER",
    "TENANT_ROLE_ADMIN",
    "TENANT_ROLE_MEMBER",
    "TENANT_ROLE_VIEWER",
    "TENANT_ROLES",
    "MEMBERSHIP_ACTIVE",
    "MEMBERSHIP_INVITED",
    "MEMBERSHIP_REMOVED",
    # 批次 2 —— 租户邀请（Step 3）
    "TenantInvitation",
    "TENANT_INVITATION_PENDING",
    "TENANT_INVITATION_ACCEPTED",
    "TENANT_INVITATION_REVOKED",
    "TENANT_INVITATION_EXPIRED",
    "TENANT_INVITATION_STATUSES",
    "TENANT_INVITATION_TTL_DAYS",
    # 批次 3 —— 网络
    "Network",
    "NetworkMembership",
    "NetworkInvitation",
    "NETWORK_ACTIVE",
    "NETWORK_SUSPENDED",
    "NETWORK_ARCHIVED",
    "NETWORK_STATUSES",
    "NETWORK_ROLE_OWNER",
    "NETWORK_ROLE_ADMIN",
    "NETWORK_ROLE_MEMBER",
    "NETWORK_ROLE_VIEWER",
    "NETWORK_ROLES",
    "NETWORK_MEMBERSHIP_ACTIVE",
    "NETWORK_MEMBERSHIP_REMOVED",
    "NETWORK_MEMBERSHIP_STATUSES",
    "INVITATION_PENDING",
    "INVITATION_ACCEPTED",
    "INVITATION_REJECTED",
    "INVITATION_EXPIRED",
    "INVITATION_REVOKED",
    "INVITATION_STATUSES",
    "INVITATION_TTL_DAYS",
    "DEFAULT_POLICY",
    "default_policy",
    # 批次 4 —— 安装（第 12 张表）
    "Installation",
    "INSTALLATION_ACTIVE",
    "INSTALLATION_REVOKED",
    "INSTALLATION_STATUSES",
    # 批次 5 —— 资源发布
    "Publication",
    "PUBLICATION_ACTIVE",
    "PUBLICATION_REVOKED",
    "PUBLICATION_STATUSES",
    # 批次 7 —— 按目标粒度的调用方白名单（FR-N7）
    "INVOKE_POLICIES",
    "INVOKE_POLICY_ALLOWLIST",
    "INVOKE_POLICY_NETWORK",
    "PUBLICATION_SCOPES",
    "PUBLICATION_SCOPE_PRIVATE",
    "PUBLICATION_SCOPE_TENANT",
    "PUBLICATION_SCOPE_NETWORK",
    "PUBLICATION_SCOPE_PUBLIC",
    "SCOPES_REQUIRING_SCOPE_ID",
    "RESOURCE_TYPES",
    "RESOURCE_TYPE_AGENT",
    "RESOURCE_TYPE_TOOL",
    "RESOURCE_TYPE_DATA",
]

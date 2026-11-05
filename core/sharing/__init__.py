"""资源发布与安装（需求 8.2 共享侧）—— 契约 §3.3。

设计见 ``docs/design-08-02-network.md`` §7；表结构见
``docs/design-08-schema.md`` §3.11–§3.12。

| 模块 | 职责 |
|---|---|
| :mod:`core.db.models.sharing` | ``publication`` / ``installation`` 表 |
| :mod:`core.sharing.service` | 发布、撤回、网络目录、安装、卸载的业务规则 |
"""

from core.sharing.service import (
    InvocationDecision,
    audit_publish_denied,
    authorize_agent_invocation,
    get_catalog_item,
    install_publication,
    issue_credential,
    list_my_installations,
    list_network_catalog,
    publish_resource,
    revoke_installation,
    revoke_publication,
    set_publication_invoke_policy,
)

__all__ = [
    "InvocationDecision",
    "audit_publish_denied",
    "authorize_agent_invocation",
    "get_catalog_item",
    "install_publication",
    "issue_credential",
    "list_my_installations",
    "list_network_catalog",
    "publish_resource",
    "revoke_installation",
    "revoke_publication",
    "set_publication_invoke_policy",
]

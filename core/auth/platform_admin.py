"""平台管理员（部署级、跨租户）判定。

## 为什么需要「平台管理员」这一层

``tenant_role``（owner / admin / member / viewer）描述的是**用户在某个租户内**
的角色。多租户共用一套部署时，每个租户的 Owner 只应管理自己租户内的资源；
而下面这些操作影响的是**整个部署/所有租户**：

- 修改全局 LLM 配置（劫持后所有人的对话都会走攻击者端点）；
- 测试任意 LLM 端点（SSRF 面）；
- 查看、认领他人的数据集（跨租户信息泄露）；
- 配置全局安全开关。

这些只能由**平台管理员**（部署方运维）执行。把 ``tenant_role == owner`` 当成
平台管理员是越权：任何自助注册的用户都会成为自己个人租户的 owner。

## 名单来源

``SOTABAND_PLATFORM_ADMINS``：逗号分隔的 ``users.id``（UUID）列表。
**空 = 没有人是平台管理员**（fail-closed）。名单只从部署环境读取，不落库、
不可由任何 HTTP 接口修改 —— 否则拿到租户管理员就能自我提权。

与「租户管理员」的关系：平台管理员**不自动**获得租户内写权限之外的其它能力，
本模块只回答"是不是平台管理员"，业务门控按「平台管理员优先」判断。
"""

from __future__ import annotations

from config.settings import settings
from core.auth.context import AuthContext

__all__ = ["is_platform_admin", "platform_admin_ids"]


def platform_admin_ids() -> set[str]:
    """当前配置的平台管理员 id 集合（小写归一，便于 UUID 大小写不敏感比较）。"""
    raw = getattr(settings.security, "platform_admins", None) or []
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def is_platform_admin(ctx: AuthContext | None) -> bool:
    """``ctx`` 是否为平台管理员。

    - 未认证 / 无 ``user_id`` → False；
    - 名单为空 → False（默认无人是平台管理员，避免"忘配置 = 人人管理员"）；
    - UUID 大小写不敏感。
    """
    if ctx is None:
        return False
    user_id = str(getattr(ctx, "user_id", "") or "").strip().lower()
    if not user_id:
        return False
    return user_id in platform_admin_ids()

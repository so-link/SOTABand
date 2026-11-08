"""审计事件写入 —— PRD FR-A5 / FR-N9。

## 必须审计的事件（认证相关）

| action | 触发 |
|---|---|
| ``auth.register`` | 注册（成功/失败） |
| ``auth.login`` | 登录（成功/失败/限流） |
| ``auth.logout`` | 登出（单设备/全部设备） |
| ``auth.session.revoked`` | 会话被吊销 |

## 硬性约束

- **禁止记录**密码、密码哈希、会话令牌、Cookie 全文、验证码；
- 审计行只追加，不修改不删除（``audit_event`` 表没有 update 语义）；
- 失败也要记 —— 失败记录正是排查攻击的依据。

## 为什么是同步函数

``session.add()`` 本来就是同步的。审计写入**不单独提交**：它跟随调用方的
事务一起提交，这样"业务成功但审计缺失"和"业务失败但审计已落库"都不会发生
（除非调用方忘了 commit —— 那是调用方的事务边界问题）。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import (
    ACTOR_SYSTEM,
    ACTOR_USER,
    RESULT_DENIED,
    RESULT_FAILURE,
    RESULT_SUCCESS,
    AuditEvent,
)

# ── action 常量（避免各处手拼字符串导致统计口径不一致）──
ACTION_REGISTER = "auth.register"
ACTION_LOGIN = "auth.login"
ACTION_LOGOUT = "auth.logout"
ACTION_LOGOUT_ALL = "auth.logout_all"
ACTION_SESSION_REVOKED = "auth.session.revoked"
#: 改密码成功（PRD FR-A5）。失败尝试用 ``RESULT_DENIED`` 记同一个 action。
ACTION_PASSWORD_CHANGED = "auth.password.changed"


def add_audit_event(
    session: AsyncSession,
    *,
    action: str,
    result: str,
    request_id: str,
    actor_id: str,
    actor_kind: str = ACTOR_USER,
    tenant_id: uuid.UUID | None = None,
    network_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> AuditEvent:
    """把一条审计事件加入当前事务（**不提交**）。

    调用方负责 commit。``request_id`` 用于把一次请求涉及的多条事件串起来。
    """
    event = AuditEvent(
        actor_id=str(actor_id),
        actor_kind=actor_kind,
        tenant_id=tenant_id,
        network_id=network_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        request_id=request_id,
    )
    session.add(event)
    return event


__all__ = [
    "ACTION_LOGIN",
    "ACTION_LOGOUT",
    "ACTION_LOGOUT_ALL",
    "ACTION_PASSWORD_CHANGED",
    "ACTION_REGISTER",
    "ACTION_SESSION_REVOKED",
    "ACTOR_SYSTEM",
    "ACTOR_USER",
    "RESULT_DENIED",
    "RESULT_FAILURE",
    "RESULT_SUCCESS",
    "add_audit_event",
]

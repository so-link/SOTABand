"""破坏性资源操作的审计（FR-N9 补全）

## 为什么需要

8.1/8.2 之前只有认证/租户/网络/共享操作写审计。多用户共用一台服务器后，
「谁改了工具代码 / 删了数据集 / 删了文件 / 改了 LLM 配置」必须有据可查，
否则出问题只能查到"东西没了"。

## 口径

- 只在**操作成功**后调用（失败路径仍由各模块按既有约定处理）；
- 调用即 commit：这些路由本身不涉及复杂事务，审计单独提交可保证
  「业务成功 → 审计一定落库」；
- 禁止记录敏感内容（代码全文、密钥、文件内容）—— 只记 target id。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.audit import add_audit_event
from core.auth.context import AuthContext
from core.db.models import RESULT_SUCCESS

#: action 常量（统计口径统一）
ACTION_RESOURCE_CODE_CHANGED = "resource.code.change"
ACTION_RESOURCE_SPEC_CHANGED = "resource.spec.change"
ACTION_RESOURCE_DELETED = "resource.delete"
ACTION_DATA_DELETED = "data.delete"
ACTION_FILE_DELETED = "file.delete"


async def record_resource_change(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    action: str,
    target_type: str,
    target_id: str,
) -> None:
    """写一条资源变更审计并提交。"""
    tenant_id = None
    if getattr(ctx, "tenant_id", None):
        try:
            tenant_id = uuid.UUID(str(ctx.tenant_id))
        except (ValueError, TypeError):
            tenant_id = None
    add_audit_event(
        session,
        action=action,
        result=RESULT_SUCCESS,
        request_id=getattr(ctx, "request_id", "") or "",
        actor_id=str(ctx.user_id),
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=str(target_id)[:128],
    )
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - 审计失败不应把已完成的业务操作变 500
        import logging

        logging.getLogger("sotaband").warning("审计写入失败（业务已完成）: %s", exc)
        try:
            await session.rollback()
        except Exception:
            pass


__all__ = [
    "ACTION_DATA_DELETED",
    "ACTION_FILE_DELETED",
    "ACTION_RESOURCE_CODE_CHANGED",
    "ACTION_RESOURCE_DELETED",
    "ACTION_RESOURCE_SPEC_CHANGED",
    "record_resource_change",
]

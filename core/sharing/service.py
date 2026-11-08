"""资源发布与安装（需求 8.2 的共享侧）—— 契约 §3.3。

## 这个模块解决什么

网络建起来之后，成员能**互相看到并使用对方的工具/Agent/数据集**。两个动作：

1. **发布（publication）**：把某个资源版本开放给某个范围 ——
   `private`（自己）/ `tenant`（整个租户）/ `network`（指定网络）/ `public`；
2. **安装（installation）**：成员把已被发布的资源装进自己的工作空间。

```text
资源版本 (registry.json)
    │  发布
    ▼
publication ──── scope=network, scope_id=N1 ────→ 网络 N1 的资源目录
    │  安装
    ▼
installation ───→ 成员的工作空间
```

## 授权：全部复用 ``authorize()``

契约 §3.3 的警告很明确：**列表、详情、安装、调用必须复用同一个授权函数**。
只在目录列表里过滤、详情接口靠猜 ID 访问，是典型 IDOR（PRD §8.3）。

所以本模块没有任何"自己写一个 if 判断"的地方：

| 动作 | 权限码 | 说明 |
|---|---|---|
| 发布到网络 | ``network.resource.publish`` | 受 ``policy.allowMemberPublish`` 策略开关约束 |
| 浏览网络目录 | ``network.read`` | |
| 安装到工作空间 | ``network.resource.install`` | 四角色都有（PRD FR-N5） |
| 发布到租户 | 租户 owner/admin | 没有对应的网络权限码，用 ``ctx.tenant_role`` |
| 发布为 private | 已认证即可 | 只影响自己 |

## ``release_id`` 是跨系统引用

资源本体不在数据库里（``resources/*/registry.json`` + 文件系统，ADR-08-08）。
所以这里**无法校验它存在** —— 连外键都做不到。查询侧必须优雅降级：
release 消失时不要把接口打成 500。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.audit import add_audit_event
from core.auth.authorize import authorize, get_active_membership, get_network
from core.auth.context import AuthContext
from core.auth.permissions import (
    NETWORK_AGENT_INVOKE,
    NETWORK_READ,
    NETWORK_RESOURCE_INSTALL,
    NETWORK_RESOURCE_PUBLISH,
    NETWORK_UPDATE,
    has_permission,
)
from core.auth.workload import issue_workload_token
from core.db.base import utcnow
from core.db.models import (
    ACTOR_SYSTEM,
    ACTOR_USER,
    ACTOR_WORKLOAD,
    INSTALLATION_ACTIVE,
    INSTALLATION_REVOKED,
    INVOKE_POLICIES,
    INVOKE_POLICY_ALLOWLIST,
    MEMBERSHIP_ACTIVE,
    NETWORK_ACTIVE,
    NETWORK_MEMBERSHIP_ACTIVE,
    PUBLICATION_ACTIVE,
    PUBLICATION_REVOKED,
    PUBLICATION_SCOPE_NETWORK,
    PUBLICATION_SCOPE_PRIVATE,
    PUBLICATION_SCOPE_PUBLIC,
    PUBLICATION_SCOPE_TENANT,
    PUBLICATION_SCOPES,
    RESOURCE_TYPES,
    RESULT_DENIED,
    RESULT_SUCCESS,
    TENANT_ROLE_ADMIN,
    TENANT_ROLE_OWNER,
    Installation,
    NetworkMembership,
    Publication,
    Tenant,
    TenantMembership,
    User,
)
from core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError

# ── 审计动作 ──────────────────────────────────────────────────────────
ACTION_PUBLISH = "publication.create"
ACTION_REVOKE_PUBLICATION = "publication.revoke"
#: 修改「谁能调用这个资源」（FR-N7 按目标粒度授权）
ACTION_SET_INVOKE_POLICY = "publication.invoke_policy.change"
ACTION_INSTALL = "installation.create"
ACTION_UNINSTALL = "installation.revoke"

#: 能发布到整个租户的角色
_TENANT_PUBLISHERS = (TENANT_ROLE_OWNER, TENANT_ROLE_ADMIN)


def _uid(ctx: AuthContext) -> uuid.UUID:
    return uuid.UUID(ctx.user_id)


def _tid(ctx: AuthContext) -> uuid.UUID:
    return uuid.UUID(ctx.tenant_id)


def _audit(
    session: AsyncSession,
    *,
    action: str,
    ctx: AuthContext,
    request_id: str,
    target_type: str,
    target_id: str,
    network_id: uuid.UUID | None = None,
) -> None:
    add_audit_event(
        session,
        action=action,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=ctx.user_id,
        tenant_id=_tid(ctx),
        network_id=network_id,
        target_type=target_type,
        target_id=target_id,
    )


def _validate_publish_args(
    *, resource_type: str, release_id: str, scope: str, scope_id: uuid.UUID | None
) -> None:
    if resource_type not in RESOURCE_TYPES:
        raise BadRequestError(f"未知资源类型：{resource_type}")
    if scope not in PUBLICATION_SCOPES:
        raise BadRequestError(f"未知发布范围：{scope}")
    if not (release_id or "").strip():
        raise BadRequestError("release_id 不能为空")
    if len(release_id) > 64:
        raise BadRequestError("release_id 最长 64 个字符")

    needs_scope_id = scope in (PUBLICATION_SCOPE_NETWORK, PUBLICATION_SCOPE_TENANT)
    if needs_scope_id and scope_id is None:
        raise BadRequestError(f"发布到 {scope} 范围时必须指定 scope_id")
    if not needs_scope_id and scope_id is not None:
        # 明确拒绝而不是静默忽略：静默忽略会让调用方以为发布到了某个范围
        raise BadRequestError(f"发布到 {scope} 范围时不应指定 scope_id")


async def _assert_can_publish(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    scope: str,
    scope_id: uuid.UUID | None,
) -> None:
    """发布权限按范围分流（见模块文档的表格）。"""
    if scope == PUBLICATION_SCOPE_PRIVATE:
        return  # 只影响自己

    if scope == PUBLICATION_SCOPE_PUBLIC:
        # 公开到所有租户是高风险动作，MVP 只留接口位，不开放
        raise ForbiddenError("暂不支持发布到 public 范围")

    if scope == PUBLICATION_SCOPE_TENANT:
        if ctx.tenant_role not in _TENANT_PUBLISHERS:
            raise ForbiddenError("只有租户所有者或管理员可以发布到整个租户")
        if scope_id != _tid(ctx):
            # 只能发布到自己的激活租户，不能替别的租户决定
            raise ForbiddenError("只能发布到当前激活租户")
        return

    # scope == network：走契约的权限码，受 policy.allowMemberPublish 策略开关约束
    if scope_id is None:  # _validate_publish_args 已保证，这里收窄类型
        raise BadRequestError("发布到 network 范围时必须指定 scope_id")
    await authorize(
        session, ctx, permission=NETWORK_RESOURCE_PUBLISH, network_id=scope_id
    )


# ── 发布 ──────────────────────────────────────────────────────────────


async def _find_release_entry(resource_type: str, release_id: str) -> dict | None:
    """按资源类型在对应 registry 里查 release 条目（查不到返回 None）。

    资源本体不在数据库（``resources/*/registry.json`` + 文件系统），所以
    ``release_id`` 只能逻辑引用。查询侧对"资源消失"要优雅降级（见模块文档），
    但**发布侧必须 fail-closed**：发一个不存在/不属于自己的版本没有正当语义。
    """
    if resource_type == "data":
        from core.resource.registry.data_registry import DataRegistry

        return await DataRegistry().get(release_id)
    if resource_type == "tool":
        from core.resource.registry.tool_registry import ToolRegistry

        return await ToolRegistry().get(release_id)
    if resource_type == "agent":
        from core.resource.registry.agent_registry import AgentRegistry

        return await AgentRegistry().get(release_id)
    return None


async def _assert_can_publish_release(
    ctx: AuthContext, *, resource_type: str, release_id: str
) -> dict:
    """校验「这个资源版本允许被当前用户发布」（管理员评审意见 2 的修复点）。

    发布 = 把资源开放给某个范围（网络/租户），因此**必须**确认发布者有权
    处置这个资源。此前只校验了"发布范围的权限"，漏了"资源归属"，导致任何
    成员都能发布别人的私有数据集/工具，进目录、可安装、可调用 → 信息泄露。

    规则（与 §D2 的写权限口径一致）：

    - ``data``：必须存在且属于当前用户（历史无 owner 条目按"本机有数据"启发式）；
    - ``tool`` / ``agent``：存在且（属于当前用户 或 该条目 public **且** 当前用户是
      租户 Owner/Admin）。工具/Agent 是共享代码资产，内置条目无 owner 且默认
      public，允许任何登录用户发布；但**代他人发布**（目录里署名为代发布者）
      必须由租户管理员做 —— 否则任何成员都能把别人的工具/Agent 挂进网络目录；
    - 不存在：一律 404 fail-closed（不区分"不存在"与"无权"，避免存在性泄露）。

    ## 为什么"public 就放行"不够

    ``ToolRegistry.register`` / ``AgentRegistry.register`` 恒写 ``visibility=public``，
    且没有修改 visibility 的接口 —— 所以「public 放行」实际等价于「任何登录用户
    都能代发」，管理员评审实测确认了这一点。共享资产（读/执行）与代发布
    （署名、下架权归属）是两件事，后者按 D2 的写权限口径收口。
    """
    entry = await _find_release_entry(resource_type, release_id)
    if entry is None:
        raise NotFoundError("资源版本不存在或无权发布")

    me = str(ctx.user_id)
    owner = str(entry.get("owner") or "")

    if resource_type == "data":
        if owner:
            if owner != me:
                raise NotFoundError("资源版本不存在或无权发布")
        else:
            # 历史无 owner 条目：与 DataRegistry.belongs_to 的迁移启发式一致
            from core.resource.registry.data_registry import DataRegistry

            if not DataRegistry().is_present_locally(entry):
                raise NotFoundError("资源版本不存在或无权发布")
        return entry

    # tool / agent：共享资产
    if owner and owner != me:
        from core.user_context import VISIBILITY_PUBLIC, get_visibility

        if get_visibility(entry, resource_type) != VISIBILITY_PUBLIC:
            raise NotFoundError("资源版本不存在或无权发布")
        # 代他人发布 = 用别人的资产在目录里建立署名与下架权，按"管理员兜底"收口
        if getattr(ctx, "tenant_role", None) not in _TENANT_PUBLISHERS:
            raise NotFoundError("资源版本不存在或无权发布")
    return entry


def audit_publish_denied(
    session: AsyncSession,
    *,
    ctx: AuthContext,
    request_id: str,
    resource_type: str,
    release_id: str,
) -> None:
    """记录一次被拒绝的发布尝试（FR-N9：拒绝也要留痕）。

    由路由层在捕获 ``NotFoundError`` 后调用并 commit —— 服务层直接 raise 的话
    审计行会随事务回滚一起丢掉。
    """
    add_audit_event(
        session,
        action=ACTION_PUBLISH,
        result=RESULT_DENIED,
        request_id=request_id,
        actor_id=ctx.user_id,
        tenant_id=_tid(ctx),
        target_type=f"{resource_type}_release",
        target_id=release_id[:64],
    )


async def publish_resource(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    resource_type: str,
    release_id: str,
    scope: str,
    scope_id: uuid.UUID | None = None,
    request_id: str,
) -> Publication:
    """把某个资源版本发布到指定范围。

    **幂等**：同一 ``(resource_type, release_id, scope, scope_id)`` 重复发布不报错，
    返回已有记录（若它曾被撤回，则重新激活）—— **但仅限同一个发布者**。

    ## 为什么必须校验 ``published_by``

    唯一索引把四元组锁成一行，所以"命中已有行"既可能是发布者自己重复发布，也可能是
    **别人**碰巧打了同一个目标：

    - 未校验时，bob 对 alice 已撤回的发布再发一次，会把 alice 的行**重新激活**，
      目录里署名仍是 alice —— bob 借 alice 的名义复活了资源；
    - ``scope=private`` 更糟：carol 对 alice 的私有发布再发 private 会命中同一行，
      响应里直接带出 alice 的 publication id 与 user id（跨租户泄露）。

    因此命中后先比对发布者：非本人时 ``private`` 一律 404（与"不存在"同形，
    不确认这一行存在），其余范围 409（"已发布到该范围"，不返回行内容）。
    """
    release_id = (release_id or "").strip()
    _validate_publish_args(
        resource_type=resource_type,
        release_id=release_id,
        scope=scope,
        scope_id=scope_id,
    )
    await _assert_can_publish_release(
        ctx, resource_type=resource_type, release_id=release_id
    )
    await _assert_can_publish(session, ctx, scope=scope, scope_id=scope_id)

    existing = await _find_publication(
        session,
        resource_type=resource_type,
        release_id=release_id,
        scope=scope,
        scope_id=scope_id,
    )
    if existing is not None:
        if existing.published_by != _uid(ctx):
            if scope == PUBLICATION_SCOPE_PRIVATE:
                # 私有发布只属于发布者本人；不泄露"已有一行"的存在
                raise NotFoundError("资源版本不存在或无权发布")
            raise ConflictError("该资源版本已发布到该范围，请刷新后重试")
        # 重新发布 = 撤销后重新激活，而不是插新行（否则唯一约束会冲突）
        if existing.status != PUBLICATION_ACTIVE:
            existing.status = PUBLICATION_ACTIVE
            existing.gmt_modified = utcnow()
            await session.flush()
        return existing

    publication = Publication(
        resource_type=resource_type,
        release_id=release_id,
        scope=scope,
        scope_id=scope_id,
        published_by=_uid(ctx),
        status=PUBLICATION_ACTIVE,
    )
    session.add(publication)
    try:
        await session.flush()
    except IntegrityError as exc:
        # 并发发布同一目标：唯一约束兜住，回滚后按"已存在"处理
        await session.rollback()
        raise ConflictError("该资源版本已发布到该范围，请刷新后重试") from exc

    _audit(
        session,
        action=ACTION_PUBLISH,
        ctx=ctx,
        request_id=request_id,
        target_type="publication",
        target_id=str(publication.id),
        network_id=scope_id if scope == PUBLICATION_SCOPE_NETWORK else None,
    )
    return publication


async def _find_publication(
    session: AsyncSession,
    *,
    resource_type: str,
    release_id: str,
    scope: str,
    scope_id: uuid.UUID | None,
) -> Publication | None:
    """按四元组查已有发布记录。

    ⚠️ ``scope_id IS NULL`` 必须显式写 —— ``== None`` 在 SQLAlchemy 里会被转成
    ``IS NULL``，但这里用 ``is_(None)`` 更直观，也避免有人改成 ``== None`` 后
    以为能匹配空。
    """
    stmt = select(Publication).where(
        Publication.resource_type == resource_type,
        Publication.release_id == release_id,
        Publication.scope == scope,
    )
    stmt = (
        stmt.where(Publication.scope_id.is_(None))
        if scope_id is None
        else stmt.where(Publication.scope_id == scope_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def revoke_publication(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    publication_id: uuid.UUID,
    request_id: str,
) -> Publication:
    """撤回发布（软删除：``status='revoked'``，行保留以便审计）。

    谁能撤回：发布者本人；或该网络的 Owner/Admin。

    ## 为什么兜底权限是 ``network.update`` 而不是 ``network.resource.publish``

    发布权限受 ``policy.allowMemberPublish`` 开关控制 —— 开关打开后普通 member 也有
    ``network.resource.publish``，于是**任何成员都能下架别人发布的东西**（已实测）。

    "把资源挂进目录"（publish）与"替别人下架"（管理动作）不是同一件事：
    后者属于网络管理，应走 ``network.update``（真值表里只有 Owner/Admin 有）。
    无权限时统一 404，不确认"这里有一条你动不了的发布"。
    """
    publication = await session.get(Publication, publication_id)
    if publication is None:
        raise NotFoundError("发布记录不存在")

    is_publisher = publication.published_by == _uid(ctx)
    if not is_publisher:
        if publication.scope == PUBLICATION_SCOPE_NETWORK and publication.scope_id:
            try:
                # 网络管理员可以下架别人发布到本网络的东西
                await authorize(
                    session,
                    ctx,
                    permission=NETWORK_UPDATE,
                    network_id=publication.scope_id,
                )
            except ForbiddenError as exc:
                # 是成员但没有 network.update：按"不存在"处理（404，不泄露）
                raise NotFoundError("发布记录不存在") from exc
        else:
            # 不是发布者、又不是网络管理员 → 按"不存在"处理，不泄露存在性
            raise NotFoundError("发布记录不存在")

    if publication.status == PUBLICATION_ACTIVE:
        publication.status = PUBLICATION_REVOKED
        publication.gmt_modified = utcnow()
        await session.flush()

    _audit(
        session,
        action=ACTION_REVOKE_PUBLICATION,
        ctx=ctx,
        request_id=request_id,
        target_type="publication",
        target_id=str(publication_id),
    )
    return publication


async def revoke_publications_for_release(
    session: AsyncSession,
    resource_type: str,
    release_id: str,
    *,
    actor_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    request_id: str = "resource.delete",
) -> int:
    """资源**本体被删除**时，把它的全部 active 发布置为 revoked。返回处理行数。

    ## 为什么必须有这一步

    ``release_id`` 是跨系统逻辑引用（资源不在库里），资源删除时数据库**不会**
    级联。悬空的 publication 会继续留在目录里，install / credential / invoke
    仍会成功 —— 等于"删了但没删干净"。更糟的是 ``release_id`` 可以被别人重新
    注册成自己的资源，旧发布就直接挂到了新资源上。

    ## 事务边界

    本函数不提交：由调用方（删除端点）与删除操作的审计一起提交，保证
    "资源删了但发布还 active"不会出现。``actor_id`` 供审计记录是谁删的；
    系统任务（无用户上下文）可传 ``None``。

    ## 为什么 target 仍是 publication 而不是 release

    审计要能按"哪一条发布被下架"对账；``release_id`` 放在 target 里会与
    正常撤回的 target_type 混淆。资源删除本身另有 ``resource.delete`` 审计。
    """
    result = await session.execute(
        select(Publication).where(
            Publication.resource_type == resource_type,
            Publication.release_id == release_id,
            Publication.status == PUBLICATION_ACTIVE,
        )
    )
    publications = list(result.scalars())
    if not publications:
        return 0

    now = utcnow()
    for publication in publications:
        publication.status = PUBLICATION_REVOKED
        publication.gmt_modified = now
    await session.flush()

    for publication in publications:
        add_audit_event(
            session,
            action=ACTION_REVOKE_PUBLICATION,
            result=RESULT_SUCCESS,
            request_id=request_id,
            actor_id=str(actor_id) if actor_id else "system",
            actor_kind=ACTOR_SYSTEM if actor_id is None else ACTOR_USER,
            tenant_id=tenant_id,
            network_id=(
                publication.scope_id
                if publication.scope == PUBLICATION_SCOPE_NETWORK
                else None
            ),
            target_type="publication",
            target_id=str(publication.id),
        )
    await session.flush()
    return len(publications)


# ── 网络资源目录 ──────────────────────────────────────────────────────


async def list_network_catalog(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    resource_type: str | None = None,
) -> list[Publication]:
    """网络资源目录（契约 §3.3 ``GET /api/v1/network-catalog``）。

    **先授权再看数据** —— 不是"查出来再过滤"。后者一旦某个分支忘了过滤就是
    全量泄露。
    """
    await authorize(session, ctx, permission=NETWORK_READ, network_id=network_id)

    stmt = select(Publication).where(
        Publication.scope == PUBLICATION_SCOPE_NETWORK,
        Publication.scope_id == network_id,
        Publication.status == PUBLICATION_ACTIVE,
    )
    if resource_type:
        if resource_type not in RESOURCE_TYPES:
            raise BadRequestError(f"未知资源类型：{resource_type}")
        stmt = stmt.where(Publication.resource_type == resource_type)

    result = await session.execute(stmt.order_by(Publication.gmt_create.desc()))
    return list(result.scalars())


async def get_catalog_item(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    network_id: uuid.UUID,
    resource_type: str,
    release_id: str,
) -> Publication:
    """目录中的一个资源详情。

    与列表**复用同一个 ``authorize()``**（契约 §3.3 的警告），否则猜 ID 就能
    绕过列表的过滤。找不到 → 404（与"不存在"同形）。
    """
    await authorize(session, ctx, permission=NETWORK_READ, network_id=network_id)

    publication = await _find_publication(
        session,
        resource_type=resource_type,
        release_id=release_id,
        scope=PUBLICATION_SCOPE_NETWORK,
        scope_id=network_id,
    )
    if publication is None or publication.status != PUBLICATION_ACTIVE:
        raise NotFoundError("资源不存在或已下架")
    return publication


# ── 安装 ──────────────────────────────────────────────────────────────


async def install_publication(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    publication_id: uuid.UUID,
    request_id: str,
) -> Installation:
    """把已发布的资源装进自己的工作空间。

    权限：必须是该发布范围的成员。
    - ``scope=network`` → ``network.resource.install``（四角色都有，PRD FR-N5）
    - ``scope=private`` → 只有发布者自己能装
    - ``scope=tenant``  → 必须是本租户成员

    **幂等**：重复安装不新增行（``UNIQUE(tenant_id, installed_by, publication_id)``），
    已吊销的会重新激活。
    """
    publication = await session.get(Publication, publication_id)
    if publication is None or publication.status != PUBLICATION_ACTIVE:
        raise NotFoundError("发布记录不存在或已下架")

    if publication.scope == PUBLICATION_SCOPE_NETWORK:
        if publication.scope_id is None:  # DB CHECK 已保证，这里收窄类型
            raise NotFoundError("发布记录不存在")
        await authorize(
            session,
            ctx,
            permission=NETWORK_RESOURCE_INSTALL,
            network_id=publication.scope_id,
        )
    elif publication.scope == PUBLICATION_SCOPE_PRIVATE:
        if publication.published_by != _uid(ctx):
            raise NotFoundError("发布记录不存在")
    elif publication.scope == PUBLICATION_SCOPE_TENANT:
        # 契约允许 tenant 范围；必须校验发布记录确实属于**当前激活租户**，
        # 否则知道 publication id 就能跨租户安装（IDOR）。
        if publication.scope_id != _tid(ctx):
            raise NotFoundError("发布记录不存在")
    else:  # public
        raise ForbiddenError("暂不支持安装 public 范围的资源")

    existing = await _find_installation(
        session, publication_id=publication.id, user_id=_uid(ctx)
    )
    if existing is not None:
        if existing.status != INSTALLATION_ACTIVE:
            existing.status = INSTALLATION_ACTIVE
            existing.gmt_modified = utcnow()
            await session.flush()
        return existing

    installation = Installation(
        tenant_id=_tid(ctx),
        publication_id=publication.id,
        installed_by=_uid(ctx),
        status=INSTALLATION_ACTIVE,
    )
    session.add(installation)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("该资源已安装，请刷新后重试") from exc

    _audit(
        session,
        action=ACTION_INSTALL,
        ctx=ctx,
        request_id=request_id,
        target_type="installation",
        target_id=str(installation.id),
        network_id=(
            publication.scope_id
            if publication.scope == PUBLICATION_SCOPE_NETWORK
            else None
        ),
    )
    return installation


async def _find_installation(
    session: AsyncSession, *, publication_id: uuid.UUID, user_id: uuid.UUID
) -> Installation | None:
    result = await session.execute(
        select(Installation).where(
            Installation.publication_id == publication_id,
            Installation.installed_by == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_my_installations(
    session: AsyncSession, ctx: AuthContext
) -> list[tuple[Installation, Publication | None]]:
    """我的工作空间里已安装的资源。

    用 **outer join**：``publication`` 可能已被物理删除（虽然设计上是软删除，
    但资源被清理时可能出现孤儿行）。inner join 会让这类安装记录**整条消失**，
    用户会以为"我装的东西自己没了"，却查不出原因。
    """
    result = await session.execute(
        select(Installation, Publication)
        .outerjoin(Publication, Publication.id == Installation.publication_id)
        .where(
            Installation.tenant_id == _tid(ctx),
            Installation.installed_by == _uid(ctx),
            Installation.status == INSTALLATION_ACTIVE,
        )
        .order_by(Installation.gmt_create.desc())
    )
    return [(installation, publication) for installation, publication in result.all()]


async def revoke_installation(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    installation_id: uuid.UUID,
    request_id: str,
) -> Installation:
    """卸载（软删除）。

    只有**安装者本人**能卸载自己的安装。别的成员的工作空间不归你管，
    网络管理员也不行 —— 要下架应该去撤回 publication，那会影响所有安装者。
    """
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise NotFoundError("安装记录不存在")
    if installation.installed_by != _uid(ctx) or installation.tenant_id != _tid(ctx):
        # 404 而不是 403：不确认"这里有一条别人的安装记录"
        raise NotFoundError("安装记录不存在")

    if installation.status == INSTALLATION_ACTIVE:
        installation.status = INSTALLATION_REVOKED
        installation.gmt_modified = utcnow()
        await session.flush()

    _audit(
        session,
        action=ACTION_UNINSTALL,
        ctx=ctx,
        request_id=request_id,
        target_type="installation",
        target_id=str(installation_id),
    )
    return installation


# ══════════════════════════════════════════════════════════════════════
# 工作负载身份与 Agent 相互通信（PRD FR-N7 / FR-N8）
# ══════════════════════════════════════════════════════════════════════

ACTION_CREDENTIAL_ISSUE = "installation.credential.issue"
ACTION_AGENT_INVOKE = "network.agent.invoke"


@dataclass(frozen=True)
class InvocationDecision:
    """一次 Agent 调用的**授权判定结果**。

    ## 为什么返回对象而不是直接抛异常

    **失败也要写审计**（PRD FR-N9 / ``core/auth/audit.py`` 的硬性约束）。
    如果服务层判定失败就抛异常，路由层的全局处理器会返回响应，但审计行随事务
    一起丢了 —— 那"被拒绝的调用"就查不到了，而这恰恰是排查攻击最需要的记录。

    所以：服务层**无论允许还是拒绝都写一条审计并返回判定**，路由层负责
    commit（把审计落库）之后再按 ``allowed`` 决定 HTTP 状态码。
    """

    allowed: bool
    #: 拒绝原因（给日志/排查用；对外的 HTTP 语义由路由层按 ``deny_kind`` 决定）
    reason: str | None
    #: 拒绝类型：``not_found`` / ``forbidden`` / ``conflict``
    deny_kind: str | None
    #: 调用方安装实例
    caller_installation_id: uuid.UUID
    #: 调用方所属网络
    network_id: uuid.UUID
    #: 命中的目标发布记录（允许时非空）
    target_publication_id: uuid.UUID | None
    #: 审计事件的 id（便于把响应与审计行对上）
    audit_event_id: str | None


async def issue_credential(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    installation_id: uuid.UUID,
    request_id: str,
) -> tuple[Installation, Publication, str, int]:
    """为**自己的**安装实例签发短期工作负载凭证。

    返回 ``(installation, publication, 明文令牌, 过期 unix 秒)``。

    授权：只有 ``installed_by == 当前用户`` 且 installation 处于 active 才能签发；
    并且**当前**仍满足 ``network.resource.install``（仍是网络 active 成员、网络状态
    允许）。别人拿不到你安装的 Agent 的凭证 —— 否则等于把工作负载身份借给别人用。
    被移出网络后不能再签新凭证（旧凭证另由调用链在每次调用时复核）。
    """
    installation = await session.get(Installation, installation_id)
    if installation is None:
        raise NotFoundError("安装记录不存在")
    if installation.installed_by != _uid(ctx) or installation.tenant_id != _tid(ctx):
        # 404 而不是 403：不确认"这里有一条别人的安装记录"
        raise NotFoundError("安装记录不存在")
    if installation.status != INSTALLATION_ACTIVE:
        raise ConflictError("该安装已被卸载，无法签发凭证")

    publication = await session.get(Publication, installation.publication_id)
    if publication is None or publication.status != PUBLICATION_ACTIVE:
        raise ConflictError("来源资源已下架，无法签发凭证")
    if publication.scope != PUBLICATION_SCOPE_NETWORK or publication.scope_id is None:
        # Agent 相互通信只在网络范围内有意义（PRD FR-N7 整节都在讲"网络内"）
        raise BadRequestError("只有发布到网络的资源才能签发工作负载凭证")

    # ── 签发前复核网络侧的现状（撤权缺口修复）──
    #
    # 此前只校验 installation/publication，不看安装者**现在**还在不在这个网络：
    # 被移出网络（或被降级/网络被暂停）后，已经装好的实例仍能换到新的工作负载
    # 凭证，接着在凭证 TTL 内继续调用 —— 「移除成员 → 下一次调用被拒」的承诺
    # 被绕开了。这里复用 authorize()：网络存在且属于本租户（404）、是 active
    # 成员（404）、网络状态允许（409）、角色有 network.resource.install（403）。
    await authorize(
        session,
        ctx,
        permission=NETWORK_RESOURCE_INSTALL,
        network_id=publication.scope_id,
    )

    token, expires_at = issue_workload_token(installation.id)

    _audit(
        session,
        action=ACTION_CREDENTIAL_ISSUE,
        ctx=ctx,
        request_id=request_id,
        target_type="installation",
        target_id=str(installation.id),
        network_id=publication.scope_id,
    )
    return installation, publication, token, expires_at


async def authorize_agent_invocation(
    session: AsyncSession,
    *,
    caller_installation_id: uuid.UUID,
    network_id: uuid.UUID,
    target_resource_type: str,
    target_release_id: str,
    request_id: str,
    jti: str | None = None,
) -> InvocationDecision:
    """判定"某个安装实例能否调用网络内的某个资源"。**这是 FR-N7 的核心。**

    ## 判定链（顺序即优先级）

    1. 调用方 installation 存在且 **active**；
    2. 它的来源 publication 存在且 **active**；
    3. 来源 publication 确实是**发布到本次请求的网络**（防止跨网络越权）；
    4. 网络存在、**active**（未暂停/归档）；
    5. 安装者仍是该网络的 **active 成员**；
    6. 安装者仍是**发起租户的 active 成员**、租户与用户账号都还是 active；
    7. 该成员角色拥有 ``network.agent.invoke``（受 ``policy.allowViewerInvoke``
       策略开关约束 —— 契约 §5.2 真值表）；
    8. 目标资源在该网络有 **active publication**（= 网络级白名单）；
    9. 若目标 publication 的 ``invoke_policy='allowlist'``，调用方安装者必须在
       ``invoke_allowlist`` 里（= **按目标**粒度的白名单）。

    ## 这条链**同时就是 FR-N8 的撤权机制**

    每一步都**现查数据库**，没有任何缓存或令牌内嵌的权限快照。因此：

    | 撤权动作 | 生效时机 |
    |---|---|
    | 卸载 installation（``revoke_installation``） | 下一次调用（步骤 1 失败） |
    | 撤回 publication（``revoke_publication`` / 资源删除） | 下一次调用（步骤 2/8 失败） |
    | 移除网络成员（``remove_member`` / ``leave_network``） | 下一次调用（步骤 5 失败） |
    | 移除租户成员 / 停用账号（``remove_tenant_member``） | 下一次调用（步骤 6 失败） |
    | 改角色（``change_member_role``） | 下一次调用（步骤 7 失败） |
    | 暂停/归档网络 | 下一次调用（步骤 4 失败） |
    | 凭证过期 | 下一次调用（``verify_workload_token`` 拦截） |

    这正是 PRD 那句"成员退出后，其 Agent 通信在 SLA 内被拒"的落地 ——
    SLA 实际上是**下一次请求**，因为整个链路没有缓存。

    ## 为什么必须校验「来源网络 == 请求网络」

    否则在 N1 装的 Agent 就能拿 N1 的凭证去调 N2 的资源 —— 网络隔离直接失效。
    """
    # ── 1. 调用方 installation ──
    installation = await session.get(Installation, caller_installation_id)
    if installation is None or installation.status != INSTALLATION_ACTIVE:
        return await _deny_invocation(
            session,
            reason="调用方安装记录不存在或已被卸载",
            deny_kind="not_found",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 2. 调用方的来源 publication ──
    caller_pub = await session.get(Publication, installation.publication_id)
    if caller_pub is None or caller_pub.status != PUBLICATION_ACTIVE:
        return await _deny_invocation(
            session,
            reason="调用方来源资源已下架",
            deny_kind="not_found",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 3. 来源 publication 必须属于本次请求的网络 ──
    if caller_pub.scope != PUBLICATION_SCOPE_NETWORK or caller_pub.scope_id != network_id:
        return await _deny_invocation(
            session,
            reason="调用方凭证不属于该网络（跨网络调用被拒）",
            deny_kind="forbidden",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 4. 网络状态 ──
    network = await get_network(session, network_id)
    if network is None:
        return await _deny_invocation(
            session,
            reason="网络不存在",
            deny_kind="not_found",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )
    if network.status != NETWORK_ACTIVE:
        return await _deny_invocation(
            session,
            reason=f"网络当前状态为「{network.status}」，拒绝调用",
            deny_kind="conflict",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 5. 安装者仍是成员？调用方已可能被移除 ──
    membership = await get_active_membership(
        session, network_id=network_id, user_id=installation.installed_by
    )
    if membership is None:
        return await _deny_invocation(
            session,
            reason="安装者已不是该网络成员，调用被拒",
            deny_kind="forbidden",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 6. 安装者仍属于发起租户？──
    #
    # 网络成员关系与租户成员关系是两张独立的表：租户把人移除（或停用账号）
    # 之后，网络 membership 可能还是 active，旧凭证就会在 TTL 内继续被放行。
    # 这是一个真实的撤权缺口（租户管理员以为"人已经清掉了"，Agent 却还能调）。
    tenant_membership = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == installation.tenant_id,
                TenantMembership.user_id == installation.installed_by,
            )
        )
    ).scalar_one_or_none()
    if (
        tenant_membership is None
        or tenant_membership.status != MEMBERSHIP_ACTIVE
    ):
        return await _deny_invocation(
            session,
            reason="安装者已不在该租户，调用被拒",
            deny_kind="forbidden",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    tenant = await session.get(Tenant, installation.tenant_id)
    installer = await session.get(User, installation.installed_by)
    if (
        tenant is None
        or tenant.status != "active"
        or installer is None
        or installer.status != "active"
    ):
        return await _deny_invocation(
            session,
            reason="安装者所在租户或账号已停用，调用被拒",
            deny_kind="forbidden",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 7. 角色权限（受 allowViewerInvoke 策略开关约束）──
    if not has_permission(
        membership.role, NETWORK_AGENT_INVOKE, policy=network.policy or {}
    ):
        return await _deny_invocation(
            session,
            reason=f"角色「{membership.role}」没有 network.agent.invoke 权限",
            deny_kind="forbidden",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 8. 目标必须在同一网络有 active publication（= 白名单）──
    target_pub = await _find_publication(
        session,
        resource_type=target_resource_type,
        release_id=target_release_id,
        scope=PUBLICATION_SCOPE_NETWORK,
        scope_id=network_id,
    )
    if target_pub is None or target_pub.status != PUBLICATION_ACTIVE:
        return await _deny_invocation(
            session,
            reason="目标资源不在该网络的发布目录中（白名单外）",
            deny_kind="not_found",
            caller_installation_id=caller_installation_id,
            network_id=network_id,
            request_id=request_id,
            jti=jti,
            target_type=target_resource_type,
            target_id=target_release_id,
        )

    # ── 9. 目标的调用方白名单（按目标粒度授权，FR-N7）──
    # 批次 5 只做到"目标发布到本网络"这一层（见步骤 8）。这一层允许发布者进一步
    # 限定"只有我点名的成员能调我的资源" —— 因为把 Agent 发布到网络 ≠ 愿意让
    # 所有人调用它。策略为 network 时（默认）直接跳过，保持向后兼容。
    if target_pub.invoke_policy == INVOKE_POLICY_ALLOWLIST:
        allowed = set(target_pub.invoke_allowlist or [])
        if str(installation.installed_by) not in allowed:
            return await _deny_invocation(
                session,
                reason="目标资源设置了调用方白名单，调用者不在名单内",
                deny_kind="forbidden",
                caller_installation_id=caller_installation_id,
                network_id=network_id,
                request_id=request_id,
                jti=jti,
                target_type=target_resource_type,
                target_id=target_release_id,
            )

    # ── 放行：写审计（actor_kind=workload，主体是安装实例而非用户）──
    event = add_audit_event(
        session,
        action=ACTION_AGENT_INVOKE,
        result=RESULT_SUCCESS,
        request_id=request_id,
        actor_id=str(installation.id),
        actor_kind=ACTOR_WORKLOAD,
        tenant_id=installation.tenant_id,
        network_id=network_id,
        target_type=target_resource_type,
        target_id=target_release_id,
    )
    # 必须 flush：`id` 由客户端默认值在 flush 时才生成，不 flush 读到的还是 None，
    # 会让响应里的 audit_event_id 为空，调用方无法与审计行对账。
    await session.flush()
    return InvocationDecision(
        allowed=True,
        reason=None,
        deny_kind=None,
        caller_installation_id=installation.id,
        network_id=network_id,
        target_publication_id=target_pub.id,
        audit_event_id=str(event.id),
    )


async def _deny_invocation(
    session: AsyncSession,
    *,
    reason: str,
    deny_kind: str,
    caller_installation_id: uuid.UUID,
    network_id: uuid.UUID,
    request_id: str,
    jti: str | None,
    target_type: str,
    target_id: str,
) -> InvocationDecision:
    """写一条**被拒绝**的调用审计并返回判定。

    拒绝记录必须留下 —— 它是识别"谁在越权尝试"的唯一依据。
    """
    logging.getLogger(__name__).warning(
        "Agent 调用被拒：caller=%s network=%s target=%s:%s jti=%s 原因=%s",
        caller_installation_id,
        network_id,
        target_type,
        target_id,
        jti or "-",
        reason,
    )

    event = add_audit_event(
        session,
        action=ACTION_AGENT_INVOKE,
        result=RESULT_DENIED,
        request_id=request_id,
        actor_id=str(caller_installation_id),
        actor_kind=ACTOR_WORKLOAD,
        network_id=network_id,
        target_type=target_type,
        target_id=target_id,
    )
    await session.flush()
    return InvocationDecision(
        allowed=False,
        reason=reason,
        deny_kind=deny_kind,
        caller_installation_id=caller_installation_id,
        network_id=network_id,
        target_publication_id=None,
        audit_event_id=str(event.id),
    )


async def set_publication_invoke_policy(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    publication_id: uuid.UUID,
    invoke_policy: str,
    invoke_allowlist: list[str] | None = None,
    request_id: str,
) -> Publication:
    """设置「谁能调用这个资源」（FR-N7 按目标粒度授权）。

    ## 为什么需要它

    「发布到网络」和「允许所有人调用」是两件事。把 Agent 发布到网络是为了让同事
    **看得到**，不代表愿意让所有人的 Agent **调用**它 —— 后者会消耗发布者的算力
    和配额。批次 5 只做到网络级白名单，这一步补上按目标的那一半。

    ## 两种策略

    | invoke_policy | 含义 |
    |---|---|
    | ``network``（默认） | 网络内所有有 ``network.agent.invoke`` 权限的成员都能调 |
    | ``allowlist`` | 只有 ``invoke_allowlist`` 里的成员能调 |

    ## 谁能改

    - 发布者本人；
    - 或该网络的 Owner/Admin（``network.update``）—— 网络管理员需要能兜底处理
      "某人把资源设成没人能调"这类情况。

    ## 为什么白名单项是成员 user_id 而不是 installation_id

    发布者面向的是"同事"。一个成员可能有多个安装实例（不同工作空间各装一份），
    让他逐个维护安装 id 既不可发现也不可维护。判定时用
    ``installation.installed_by`` 反查即可。

    ## 为什么校验成员身份

    否则可以先把一个外部用户的 id 塞进白名单，等他加进网络后立刻就能调用 ——
    绕过了"发布者显式同意"的语义。这里要求名单里的人**当前**就是 active 成员。
    """
    publication = await session.get(Publication, publication_id)
    if publication is None:
        raise NotFoundError("发布记录不存在")
    if publication.status != PUBLICATION_ACTIVE:
        raise ConflictError("该发布已下架，无法修改调用权限")
    if invoke_policy not in INVOKE_POLICIES:
        raise BadRequestError(
            f"invoke_policy 必须是 {' 或 '.join(INVOKE_POLICIES)}，收到 {invoke_policy!r}"
        )
    if publication.scope != PUBLICATION_SCOPE_NETWORK or publication.scope_id is None:
        # private/tenant/public 范围没有"网络内成员"这个概念，白名单无从谈起
        raise BadRequestError("只有发布到协作网络的资源才能设置调用方白名单")

    network_id = publication.scope_id

    # ── 权限：发布者本人，或网络 Owner/Admin ──
    if publication.published_by != _uid(ctx):
        await authorize(session, ctx, permission=NETWORK_UPDATE, network_id=network_id)

    # ── 校验并归一化名单 ──
    normalized: list[str] | None = None
    if invoke_policy == INVOKE_POLICY_ALLOWLIST:
        raw_ids = [str(item).strip() for item in (invoke_allowlist or [])]
        # 去重但保持顺序（前端按成员列表顺序展示，乱序会跳）
        seen: set[str] = set()
        normalized = []
        for item in raw_ids:
            if not item or item in seen:
                continue
            try:
                uuid.UUID(item)
            except ValueError as exc:
                raise BadRequestError(f"非法的用户 id：{item}") from exc
            seen.add(item)
            normalized.append(item)

        if not normalized:
            # 空名单 = 谁都调不了，这一定是配置错误，不能默默接受
            raise BadRequestError("调用方白名单不能为空（否则等于没有任何人能调用）")

        member_rows = await session.execute(
            select(NetworkMembership.user_id).where(
                NetworkMembership.network_id == network_id,
                NetworkMembership.status == NETWORK_MEMBERSHIP_ACTIVE,
            )
        )
        active_members = {str(uid) for uid in member_rows.scalars()}
        outsiders = [uid for uid in normalized if uid not in active_members]
        if outsiders:
            raise BadRequestError(
                "白名单里的用户不是该网络的成员：" + ", ".join(outsiders)
            )

    publication.invoke_policy = invoke_policy
    publication.invoke_allowlist = normalized
    await session.flush()

    _audit(
        session,
        action=ACTION_SET_INVOKE_POLICY,
        ctx=ctx,
        request_id=request_id,
        network_id=network_id,
        target_type="publication",
        target_id=str(publication.id),
    )
    return publication

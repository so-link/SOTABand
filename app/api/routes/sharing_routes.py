"""共享路由 —— ``/api/v1/*-releases/*``、``/network-catalog``、``/installations``

契约 §3.3。

## 路径里为什么有 ``{resource_type}`` 这一段

契约给的是 ``/api/v1/{agent|tool|data}-releases/{release_id}/...``。这样资源的
**类型**是 URL 的一部分，而不是一个可被伪造的 body 字段 —— 减少"类型与发布记录
对不上"的整类错误。

## 授权一律在 service 层

本文件只做 HTTP 适配：解析参数、调 service、序列化。**不在这里写任何权限判断**，
否则列表接口和详情接口很容易各写一套、慢慢漂移成不一致（契约 §3.3 的 IDOR 警告）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_context, get_request_id, get_workload_claims
from app.api.schemas.sharing_schemas import (
    InstallationSummary,
    InvokeRequest,
    InvokeResponse,
    PublicationSummary,
    PublishRequest,
    SetInvokePolicyRequest,
    WorkloadCredentialResponse,
)
from core.agent.invoke_bus import DELIVERY_OK, invocation_bus
from core.auth.context import AuthContext
from core.auth.ratelimit import invoke_limiter
from core.auth.workload import WorkloadClaims
from core.db.base import utcnow
from core.db.models import RESOURCE_TYPES, Publication
from core.db.session import get_db_session
from core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from core.sharing.service import (
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

router = APIRouter()


def _parse_uuid(raw: str, *, what: str = "资源") -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise NotFoundError(f"{what}不存在") from exc


def _parse_resource_type(raw: str) -> str:
    if raw not in RESOURCE_TYPES:
        raise NotFoundError(f"资源类型不存在：{raw}")
    return raw


def _to_publication(pub: Publication) -> PublicationSummary:
    return PublicationSummary(
        id=str(pub.id),
        resource_type=pub.resource_type,
        release_id=pub.release_id,
        scope=pub.scope,
        scope_id=str(pub.scope_id) if pub.scope_id else None,
        status=pub.status,
        published_by=str(pub.published_by),
        created_at=pub.gmt_create.isoformat(),
        invoke_policy=pub.invoke_policy,
        invoke_allowlist=list(pub.invoke_allowlist or []),
    )


# ── 发布 ──────────────────────────────────────────────────────────────


@router.post(
    "/{resource_type}-releases/{release_id}/publications",
    status_code=status.HTTP_201_CREATED,
    response_model=PublicationSummary,
)
async def publish_endpoint(
    resource_type: str,
    release_id: str,
    payload: PublishRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> PublicationSummary:
    """把资源版本发布到 private / tenant / network 范围。

    **幂等**：重复发布返回同一条记录，不会 409。
    """
    rtype = _parse_resource_type(resource_type)
    scope_id = _parse_uuid(payload.scope_id, what="发布范围") if payload.scope_id else None

    try:
        publication = await publish_resource(
            session,
            ctx,
            resource_type=rtype,
            release_id=release_id,
            scope=payload.scope,
            scope_id=scope_id,
            request_id=request_id,
        )
    except AppError:
        # 拒绝也要留痕（FR-N9）：资源归属 404 与范围权限 403 都补一条 denied
        # 审计并先提交，再抛出（服务层直接 raise 会让审计随事务回滚丢失）。
        audit_publish_denied(
            session,
            ctx=ctx,
            request_id=request_id,
            resource_type=rtype,
            release_id=release_id,
        )
        await session.commit()
        raise
    await session.commit()
    return _to_publication(publication)


@router.delete(
    "/publications/{publication_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_publication_endpoint(
    publication_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """撤回发布（软删除，行保留以便审计）。"""
    pub_id = _parse_uuid(publication_id, what="发布记录")
    await revoke_publication(
        session, ctx, publication_id=pub_id, request_id=request_id
    )
    await session.commit()


@router.patch(
    "/publications/{publication_id}/invoke-policy",
    response_model=PublicationSummary,
)
async def set_invoke_policy_endpoint(
    publication_id: str,
    payload: SetInvokePolicyRequest,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> PublicationSummary:
    """设置「谁能调用这个资源」（FR-N7 按目标粒度授权）。

    - ``network``（默认）：网络内所有有 ``network.agent.invoke`` 权限的成员都能调
    - ``allowlist``：只有名单里的成员能调；名单里的人必须**当前**就是该网络成员

    发布者本人可改；网络 Owner/Admin 也可改（兜底）。
    """
    pub_id = _parse_uuid(publication_id, what="发布记录")
    publication = await set_publication_invoke_policy(
        session,
        ctx,
        publication_id=pub_id,
        invoke_policy=payload.invoke_policy,
        invoke_allowlist=payload.invoke_allowlist,
        request_id=request_id,
    )
    await session.commit()
    await session.refresh(publication)
    return _to_publication(publication)


# ── 网络资源目录 ──────────────────────────────────────────────────────


@router.get("/network-catalog", response_model=list[PublicationSummary])
async def list_catalog_endpoint(
    network_id: str = Query(..., alias="networkId"),
    resource_type: str | None = Query(default=None, alias="resourceType"),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[PublicationSummary]:
    """某个网络下已发布的资源目录。"""
    nid = _parse_uuid(network_id, what="网络")
    rtype = _parse_resource_type(resource_type) if resource_type else None
    rows = await list_network_catalog(
        session, ctx, network_id=nid, resource_type=rtype
    )
    return [_to_publication(pub) for pub in rows]


@router.get(
    "/network-catalog/{resource_type}/{release_id}",
    response_model=PublicationSummary,
)
async def get_catalog_item_endpoint(
    resource_type: str,
    release_id: str,
    network_id: str = Query(..., alias="networkId"),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> PublicationSummary:
    """目录中的单个资源。与列表复用同一个 ``authorize()``，避免 IDOR。"""
    rtype = _parse_resource_type(resource_type)
    nid = _parse_uuid(network_id, what="网络")
    publication = await get_catalog_item(
        session, ctx, network_id=nid, resource_type=rtype, release_id=release_id
    )
    return _to_publication(publication)


# ── 安装 ──────────────────────────────────────────────────────────────


@router.post(
    "/{resource_type}-releases/{release_id}/installations",
    status_code=status.HTTP_201_CREATED,
    response_model=InstallationSummary,
)
async def install_endpoint(
    resource_type: str,
    release_id: str,
    network_id: str = Query(..., alias="networkId"),
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> InstallationSummary:
    """从某网络的目录安装资源到自己的工作空间。

    先按 ``(resource_type, release_id, network)`` 定位发布记录 —— 这样路径里
    就不需要出现 publication id，调用方也不必先查一次目录。
    """
    rtype = _parse_resource_type(resource_type)
    nid = _parse_uuid(network_id, what="网络")

    publication = await get_catalog_item(
        session, ctx, network_id=nid, resource_type=rtype, release_id=release_id
    )
    installation = await install_publication(
        session, ctx, publication_id=publication.id, request_id=request_id
    )
    await session.commit()
    return InstallationSummary(
        id=str(installation.id),
        publication_id=str(installation.publication_id),
        status=installation.status,
        created_at=installation.gmt_create.isoformat(),
        resource_type=publication.resource_type,
        release_id=publication.release_id,
    )


@router.get("/me/installations", response_model=list[InstallationSummary])
async def list_installations_endpoint(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[InstallationSummary]:
    """我安装到工作空间的资源。"""
    rows = await list_my_installations(session, ctx)
    out: list[InstallationSummary] = []
    for installation, publication in rows:
        out.append(
            InstallationSummary(
                id=str(installation.id),
                publication_id=str(installation.publication_id),
                status=installation.status,
                created_at=installation.gmt_create.isoformat(),
                # publication 可能已消失（outer join），前端据此显示"资源已下架"
                resource_type=publication.resource_type if publication else None,
                release_id=publication.release_id if publication else None,
            )
        )
    return out


@router.delete(
    "/installations/{installation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def uninstall_endpoint(
    installation_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """卸载（只有安装者本人能卸自己的）。"""
    iid = _parse_uuid(installation_id, what="安装记录")
    await revoke_installation(
        session, ctx, installation_id=iid, request_id=request_id
    )
    await session.commit()


# ══════════════════════════════════════════════════════════════════════
# 工作负载身份与 Agent 相互通信（PRD FR-N7 / FR-N8）
# ══════════════════════════════════════════════════════════════════════


@router.post(
    "/installations/{installation_id}/credential",
    response_model=WorkloadCredentialResponse,
)
async def issue_credential_endpoint(
    installation_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> WorkloadCredentialResponse:
    """为自己的安装实例签发**短期工作负载凭证**（用户身份调用）。

    签发出来的 token 交给这个 Agent 进程，Agent 之后用 ``Authorization: Bearer``
    发起网络内调用 —— **不再携带用户的 Cookie**。

    只有安装者本人能为自己的安装签发；卸载后不能再签。
    """
    iid = _parse_uuid(installation_id, what="安装记录")
    installation, publication, token, expires_at = await issue_credential(
        session, ctx, installation_id=iid, request_id=request_id
    )
    await session.commit()

    now = int(utcnow().timestamp())
    return WorkloadCredentialResponse(
        token=token,
        installation_id=str(installation.id),
        network_id=str(publication.scope_id),
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
        expires_in=max(0, expires_at - now),
    )


@router.post(
    "/installations/{installation_id}/invoke",
    response_model=InvokeResponse,
)
async def invoke_endpoint(
    installation_id: str,
    payload: InvokeRequest,
    claims: WorkloadClaims = Depends(get_workload_claims),
    request_id: str = Depends(get_request_id),
    session: AsyncSession = Depends(get_db_session),
) -> InvokeResponse:
    """Agent 调用网络内资源 —— **本接口只认工作负载令牌，不认用户 Cookie**。

    PRD FR-N7 的两条硬性要求在这里落地：

    1. **不能用用户会话冒充 Agent** → 本接口挂的是 ``get_workload_claims``，
       不读 Cookie。用 Cookie 调用会得到 401。
    2. **只能调用白名单内的目标** → 目标必须在同一网络有 active publication。

    ## 失败也写审计

    服务层无论放行还是拒绝都会写一条 ``network.agent.invoke`` 审计（拒绝的
    ``result='denied'``）。所以这里**先 commit 再抛错** —— 否则审计行会随事务
    一起丢掉，"谁在越权尝试"就查不到了。
    """
    path_installation = _parse_uuid(installation_id, what="安装记录")
    if path_installation != claims.installation_id:
        # 令牌有效，但不属于路径里的这个安装 —— 不允许拿自己的凭证替别人调用
        raise ForbiddenError("该凭证不属于这个安装实例")

    network_id = _parse_uuid(payload.network_id, what="网络")
    target_type = _parse_resource_type(payload.target_resource_type)

    # ── 速率限制：按**调用方安装实例**计数 ──
    # 白名单解决"能不能调"，这里解决"能调多快"。一个跑飞的 Agent
    # (`while true: call(...)`) 会把目标打到过载，而目标方在白名单放行后
    # 没有任何拒绝手段 —— 这是 Agent 间的"吵闹邻居"问题。
    # 放在授权之前：超限的请求连数据库查询都不该做（它本质是 DoS 流量）。
    window = invoke_limiter().hit(str(claims.installation_id))
    if not window.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="调用过于频繁，请稍后再试",
            headers={"Retry-After": str(window.retry_after)},
        )

    decision = await authorize_agent_invocation(
        session,
        caller_installation_id=claims.installation_id,
        network_id=network_id,
        target_resource_type=target_type,
        target_release_id=payload.target_release_id,
        request_id=request_id,
        jti=claims.jti,
    )

    # 先把审计落库（放行与拒绝都要留痕），再按判定决定状态码
    await session.commit()

    if not decision.allowed:
        if decision.deny_kind == "not_found":
            raise NotFoundError(decision.reason or "目标资源不存在")
        if decision.deny_kind == "conflict":
            raise ConflictError(decision.reason or "当前状态无法调用")
        raise ForbiddenError(decision.reason or "无权调用该资源")

    assert decision.target_publication_id is not None  # 放行时必然有值

    # ── 授权通过 → 真正投递给目标 Agent（FR-N7 的"接线"）──
    # 离线/超时都**不是错误**：授权已成立，调用方稍后重试即可。所以这里仍然
    # 返回 200，用 delivered/delivery_status 区分，免得调用方去排查权限。
    delivery_status, response_body = await invocation_bus().deliver(
        resource_type=target_type,
        release_id=payload.target_release_id,
        payload=payload.payload,
    )

    return InvokeResponse(
        allowed=True,
        caller_installation_id=str(decision.caller_installation_id),
        network_id=str(decision.network_id),
        target_publication_id=str(decision.target_publication_id),
        audit_event_id=decision.audit_event_id,
        delivered=delivery_status == DELIVERY_OK,
        delivery_status=delivery_status,
        response=response_body,
    )

"""网络相关的 Pydantic 模型（契约 §3.2）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas.auth_schemas import _validate_email_format

NetworkRoleLiteral = Literal["owner", "admin", "member", "viewer"]
NetworkStatusLiteral = Literal["active", "suspended", "archived"]


class NetworkPolicy(BaseModel):
    """网络策略开关（契约 §2.3）。

    ## 为什么字段是 ``bool | None`` 而不是带默认值的 ``bool``

    契约把「修改信息 / 暂停 / 归档」合成一个 ``PATCH``，``policy`` 因此是**局部更新**
    语义：只传 ``{"allowMemberPublish": true}`` 时，``allowViewerInvoke`` 必须保持
    原值，不能被默认值覆盖。

    路由层目前是把模型 ``model_dump()`` 成完整 dict 再交给 service（``exclude_unset``
    在路由层用不上）。若字段默认 ``False``，dump 出来的两个键永远存在，service 只能
    全量覆盖 —— 实测「只传 allowMemberPublish」会把 allowViewerInvoke 悄悄改回 false。
    把默认值改成 ``None`` 表示"本次没有显式提供"，service 跳过 ``None`` 即等价于
    ``exclude_unset=True`` 的局部合并，且不需要路由层配合。
    """

    allow_member_publish: bool | None = Field(default=None, alias="allowMemberPublish")
    allow_viewer_invoke: bool | None = Field(default=None, alias="allowViewerInvoke")

    model_config = ConfigDict(populate_by_name=True)


class CreateNetworkRequest(BaseModel):
    """创建网络。

    ``policy`` 用 camelCase 别名 —— 前端按契约发 ``allowMemberPublish``，
    不加别名会被 Pydantic 静默丢弃（注册接口踩过同样的坑）。
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., max_length=128, examples=["飞控算法组"])
    policy: NetworkPolicy | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("网络名称不能为空")
        return cleaned


class UpdateNetworkRequest(BaseModel):
    """修改网络信息 / 暂停 / 归档 / 恢复。

    契约 §3.2 把「更新/暂停/归档」合成一个 ``PATCH``，所以三个字段都是可选的，
    但**至少要有一个**（否则是无意义请求）。
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, max_length=128)
    policy: NetworkPolicy | None = None
    status: NetworkStatusLiteral | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("网络名称不能为空")
        return cleaned


class ChangeMemberRoleRequest(BaseModel):
    """调整成员角色。

    ``expected_version`` 来自成员列表里的 ``version`` —— 用于 compare-and-set，
    并发修改时返回 409（契约 §4）。不传则退化为"最后写入者胜"。
    """

    role: NetworkRoleLiteral
    expected_version: int | None = Field(default=None, ge=1, alias="expectedVersion")

    model_config = ConfigDict(populate_by_name=True)


class CreateInvitationRequest(BaseModel):
    """发起邀请。"""

    email: str

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return _validate_email_format(v)


# ── 响应 ──────────────────────────────────────────────────────────────


class NetworkSummary(BaseModel):
    """网络摘要（列表与创建响应）。"""

    id: str
    name: str
    status: str
    role: str
    policy: dict
    tenant_id: str
    owner_id: str
    created_at: str


class NetworkDetail(NetworkSummary):
    """网络详情 —— 额外带成员数，便于前端展示。"""

    member_count: int


class MemberSummary(BaseModel):
    """成员摘要。

    ``version`` 必须返回：前端调角色变更时要原样回传，服务端用它做 CAS。
    """

    user_id: str
    display_name: str | None = None
    email: str | None = None
    role: str
    joined_at: str
    version: int


class InvitationSummary(BaseModel):
    """邀请摘要（**不含令牌**）。"""

    id: str
    email: str
    status: str
    expires_at: str
    created_at: str


class InvitationCreated(BaseModel):
    """邀请创建响应 —— 唯一一次返回明文令牌。

    令牌库里只存 hash，所以**这个字段不会再有第二次机会拿到**。
    前端需要立刻把它拼成邀请链接展示或复制。
    """

    invitation: InvitationSummary
    token: str
    accept_path: str


# ── 审计（FR-N9）──────────────────────────────────────────────────────


class AuditEventSummary(BaseModel):
    """一条审计事件。

    ``request_id`` 是把「一次请求涉及的多条事件」串起来的线索（PRD §11）——
    例如一次改角色操作可能同时产生 ``network.member.role.change`` 和会话吊销两条。
    """

    id: str
    action: str
    result: str
    actor_id: str
    #: user / workload / system —— Agent 发起的调用这里是 workload
    actor_kind: str
    target_type: str | None = None
    target_id: str | None = None
    request_id: str
    created_at: str

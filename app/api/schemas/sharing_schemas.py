"""共享相关的 Pydantic 模型（契约 §3.3）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PublishRequest(BaseModel):
    """发布资源到某个范围。

    ``scopeId`` 用 camelCase 别名接收（前端约定），``scope='network'``/``'tenant'``
    时必填。
    """

    model_config = ConfigDict(populate_by_name=True)

    scope: str = Field(..., examples=["network"])
    scope_id: str | None = Field(default=None, alias="scopeId")


# ── 响应 ──────────────────────────────────────────────────────────────


class PublicationSummary(BaseModel):
    id: str
    resource_type: str
    release_id: str
    scope: str
    scope_id: str | None = None
    status: str
    published_by: str
    created_at: str
    #: 谁能调用：network（网络内有权限的成员）/ allowlist（仅名单内成员）
    invoke_policy: str = "network"
    #: invoke_policy='allowlist' 时生效 —— 允许调用的成员 user_id 列表
    invoke_allowlist: list[str] = []


class SetInvokePolicyRequest(BaseModel):
    """设置「谁能调用这个资源」（FR-N7 按目标粒度授权）。

    用 camelCase 别名 —— 前端按契约发 ``invokePolicy`` / ``invokeAllowlist``，
    不加别名会被 Pydantic 静默丢弃。
    """

    model_config = ConfigDict(populate_by_name=True)

    #: network / allowlist
    invoke_policy: str = Field(..., alias="invokePolicy")
    #: allowlist 策略时必填，且必须是该网络的 active 成员
    invoke_allowlist: list[str] = Field(default_factory=list, alias="invokeAllowlist")


class InstallationSummary(BaseModel):
    id: str
    publication_id: str
    status: str
    created_at: str
    #: 发布信息可能缺失（资源已下架/清理），前端要能优雅降级
    resource_type: str | None = None
    release_id: str | None = None


# ── 工作负载身份（PRD FR-N7 / FR-N8）────────────────────────────────


class WorkloadCredentialResponse(BaseModel):
    """安装凭证 —— Agent 用它发起网络内调用。

    ⚠️ ``token`` 是**短期凭证明文**（默认 10 分钟）。它只用于
    ``Authorization: Bearer``，**不代表权限**：每次调用都会重新从数据库推导授权，
    所以卸载安装 / 撤回发布 / 移除成员的下一次调用立刻失效。
    """

    token: str
    installation_id: str
    network_id: str
    #: ISO 时间，便于前端展示"还剩多久"
    expires_at: str
    expires_in: int


class InvokeRequest(BaseModel):
    """Agent 调用请求（契约 §3.3 ``POST /installations/{id}/invoke``）。"""

    model_config = ConfigDict(populate_by_name=True)

    network_id: str = Field(..., alias="networkId")
    target_resource_type: str = Field(..., alias="targetResourceType")
    #: 目标资源版本 id（``resources/*/registry.json`` 里的版本号，不是 UUID）
    target_release_id: str = Field(..., alias="targetReleaseId")
    #: 要投递给目标 Agent 的调用载荷（FR-N7 接线后才会真正送出去）
    payload: dict[str, Any] = Field(default_factory=dict)


class InvokeResponse(BaseModel):
    """调用结果 —— **授权判定 + 投递结果**两部分。

    ## 为什么授权和投递是两个独立字段

    「你被允许调用它」和「它收到了」是两件事，失败原因完全不同：

    - ``allowed=false`` → 权限/白名单/网络状态问题，重试无用，要改配置；
    - ``allowed=true, delivered=false`` → 授权没问题，只是目标 Agent 没上线
      （``delivery_status='offline'``）或卡住了（``'timeout'``），**稍后重试即可**。

    把它们混成一个布尔值会让调用方去排查错误的方向。

    ⚠️ 关于 PRD §2.4：它把「Agent 底层通信协议（RPC/消息总线传输层）」列为
    **非目标**。这里的投递复用的是仓库已有的 ``MessageBus`` 抽象，不引入
    新的传输层依赖；传输实现可替换。
    """

    allowed: bool
    caller_installation_id: str
    network_id: str
    target_publication_id: str
    #: 本次调用写入审计的事件 id，便于与 ``audit_event`` 表对账
    audit_event_id: str | None = None

    # ── 投递结果（授权通过后才有意义）──
    #: 是否真的把调用送到了目标 Agent
    delivered: bool = False
    #: ok / offline / timeout / denied
    delivery_status: str = "denied"
    #: 目标 Agent 的响应体（``delivered=true`` 时可能为空字典）
    response: dict[str, Any] | None = None

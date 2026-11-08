"""租户相关的 Pydantic 模型（契约 §3.1 + Step 3 补充的邀请接口）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas.auth_schemas import _validate_email_format


class CreateTenantRequest(BaseModel):
    """创建租户（契约 §3.1 ``POST /api/v1/tenants``）。"""

    name: str = Field(..., max_length=128, examples=["飞控团队"])

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("租户名称不能为空")
        return cleaned


class SwitchTenantRequest(BaseModel):
    """切换激活租户（契约 §3.1 ``POST /api/v1/me/switch-tenant``）。

    ``tenantId`` 只是**意向** —— 服务端必须校验调用者在该租户里有 active 成员关系
    （PRD FR-A6）。用别名接收 camelCase，与前端保持一致。
    """

    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str = Field(..., alias="tenantId")

    @field_validator("tenant_id")
    @classmethod
    def check_uuid(cls, v: str) -> str:
        import uuid as _uuid

        try:
            return str(_uuid.UUID(str(v)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("tenantId 必须是合法的 UUID") from exc


class CreateTenantInvitationRequest(BaseModel):
    """发起租户邀请（Owner/Admin）。"""

    email: str

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return _validate_email_format(v)


# ── 响应 ──────────────────────────────────────────────────────────────


class TenantDetail(BaseModel):
    """租户详情。"""

    id: str
    name: str
    slug: str
    status: str
    role: str
    created_at: str


class TenantMemberSummary(BaseModel):
    """租户成员摘要。"""

    user_id: str
    display_name: str | None = None
    email: str | None = None
    role: str
    joined_at: str


class TenantInvitationSummary(BaseModel):
    """租户邀请摘要（**不含令牌**）。"""

    id: str
    email: str
    status: str
    expires_at: str
    created_at: str


class TenantInvitationCreated(BaseModel):
    """邀请创建响应 —— 唯一一次返回明文令牌。

    与网络邀请一致：令牌库里只存 hash，前端要立刻拼成邀请链接展示或复制。
    """

    invitation: TenantInvitationSummary
    token: str
    accept_path: str

"""认证相关的 Pydantic 模型。

刻意不使用 ``EmailStr``：它需要额外安装 ``email-validator``，而本项目已经
依赖不少，为一个邮箱校验再加一个包不值得。用宽松正则在应用层校验即可
（真正的有效性只有发信才能验证，而 MVP 不做邮箱验证 —— ADR-08-01b）。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 宽松邮箱校验：有 @、有域名、域名带点。不追求 RFC 完备。
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: 邮箱最大长度（RFC 5321 的 320）
EMAIL_MAX_LENGTH = 320

#: 显示名最大长度（与 user.display_name 列宽一致）
DISPLAY_NAME_MAX_LENGTH = 128


def _validate_email_format(value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise ValueError("邮箱不能为空")
    if len(v) > EMAIL_MAX_LENGTH:
        raise ValueError(f"邮箱最长 {EMAIL_MAX_LENGTH} 个字符")
    if not _EMAIL_PATTERN.match(v):
        raise ValueError("邮箱格式不正确")
    return v


class RegisterRequest(BaseModel):
    """注册请求。

    ``password`` 的长度策略**不在这里校验** —— 它按 UTF-8 字节计算（中文 1 字
    3 字节），且上限来自 bcrypt 的硬限制，属于认证领域的规则，统一放在
    ``core.auth.passwords.validate_password`` 里，避免两处规则漂移。

    ⚠️ ``display_name`` 用 ``alias="displayName"``：冻结契约 §3.1 规定请求体是
    ``{email, password, displayName?}``（camelCase）。若不加别名，Pydantic 会把
    ``displayName`` 当未知字段**静默丢弃**，显示名悄悄回落成邮箱前缀 ——
    前端看不出错，只是名字不对。
    """

    #: 允许按字段名（display_name）或别名（displayName）两种写法传入
    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(..., examples=["alice@example.com"])
    password: str = Field(..., examples=["Passw0rd2026"])
    display_name: str | None = Field(
        default=None, alias="displayName", max_length=DISPLAY_NAME_MAX_LENGTH
    )

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return _validate_email_format(v)


class LoginRequest(BaseModel):
    """登录请求。"""

    email: str = Field(..., examples=["alice@example.com"])
    password: str = Field(..., examples=["Passw0rd2026"])

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return _validate_email_format(v)


class ChangePasswordRequest(BaseModel):
    """修改密码（PRD FR-A5）。

    与注册请求一致用 camelCase 别名 —— 前端按契约发 ``currentPassword`` /
    ``newPassword``；不加别名会被 Pydantic 静默丢弃（注册接口踩过这个坑）。
    """

    model_config = ConfigDict(populate_by_name=True)

    current_password: str = Field(..., alias="currentPassword")
    new_password: str = Field(..., alias="newPassword")


class ChangePasswordResponse(BaseModel):
    """改密码结果。

    ``revokedSessions`` 是**被吊销的会话数**（含当前这条）。前端拿到后应当主动
    登出并提示用户重新登录 —— 因为当前 Cookie 已经失效了。
    """

    model_config = ConfigDict(populate_by_name=True)

    revoked_sessions: int = Field(..., alias="revokedSessions")
    message: str


class TenantSummary(BaseModel):
    """租户摘要（返回给前端做租户切换用）。"""

    id: str
    name: str
    slug: str
    role: str


class UserSummary(BaseModel):
    """当前用户摘要。"""

    id: str
    email: str | None = None
    display_name: str
    avatar_url: str | None = None


class CurrentUserResponse(BaseModel):
    """``GET /api/v1/me`` 响应（契约 §1.4 的 ``CurrentUser``）。"""

    user_id: str
    email: str | None = None
    display_name: str | None = None
    active_tenant_id: str
    tenant_role: str
    tenants: list[TenantSummary] = Field(default_factory=list)
    #: 是否为**平台管理员**（部署级；来自 ``SOTABAND_PLATFORM_ADMINS``）。
    #: 前端据此门控系统配置页 / 全局 LLM 配置 / "查看他人数据集"等入口。
    #: 注意它**不是** ``tenant_role == owner`` —— 两者语义不同，见
    #: ``core/auth/platform_admin.py``。
    is_platform_admin: bool = False


class SessionSummary(BaseModel):
    """会话摘要（**不含令牌**，只暴露可识别的元信息）。"""

    id: str
    created_at: str
    expires_at: str
    user_agent: str | None = None
    ip: str | None = None
    current: bool = False


class SessionProbeResponse(BaseModel):
    """``GET /api/auth/session`` 响应。

    契约 §3.1 明确：**未登录返回 200 而非 401** —— 这个接口是给前端 Auth 状态机
    探测用的，用 401 会让浏览器控制台刷错误、也会触发拦截器逻辑。
    """

    authenticated: bool
    user: UserSummary | None = None
    active_tenant_id: str | None = None
    tenant_role: str | None = None

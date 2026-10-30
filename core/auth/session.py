"""请求级身份上下文

用 contextvar 携带「当前请求是谁」，供 ``core.user_context.get_current_user_id()``
优先读取。设计要点：

- 本模块**零依赖**（仅 stdlib contextvars），core.user_context 可安全导入它
  而不产生循环依赖。
- 未设置时返回 None，调用方回退到实例身份（env / user.json / local），
  保证无令牌的老客户端与 Agent 子进程行为完全不变。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

_current_user: contextvars.ContextVar["AuthUser | None"] = contextvars.ContextVar(
    "sotaband_current_user", default=None
)


@dataclass(frozen=True)
class AuthUser:
    id: str
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def set_current_user(user: AuthUser | None):
    """设置当前请求身份，返回 token 供 reset 使用。"""
    return _current_user.set(user)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def get_current_user() -> AuthUser | None:
    return _current_user.get()


def current_identity() -> str | None:
    """当前请求身份对应的 user id；匿名请求返回 None。"""
    u = _current_user.get()
    return u.id if u else None

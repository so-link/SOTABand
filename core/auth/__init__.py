"""认证与会话 —— 需求 8.1 的核心实现。

设计见 ``docs/design-08-01-auth.md``；接口契约见
``docs/contract-08-auth-network.md`` §1、§3.1、§3.4。

模块划分：

| 模块 | 职责 |
|---|---|
| :mod:`core.auth.context` | ``AuthContext`` —— 服务端生成的唯一可信请求主体 |
| :mod:`core.auth.passwords` | bcrypt 哈希、密码策略、72 字节边界 |
| :mod:`core.auth.tokens` | 会话令牌生成与哈希（明文永不落库） |
| :mod:`core.auth.ratelimit` | 进程内滑动窗口限流 |
| :mod:`core.auth.audit` | 审计事件写入 |
| :mod:`core.auth.service` | 注册 / 登录 / 会话校验与吊销的业务逻辑 |
"""

from core.auth.context import AuthContext
from core.auth.passwords import (
    PasswordValidationError,
    hash_password,
    validate_password,
    verify_password,
)
from core.auth.tokens import generate_session_token, hash_session_token

__all__ = [
    "AuthContext",
    "PasswordValidationError",
    "generate_session_token",
    "hash_password",
    "hash_session_token",
    "validate_password",
    "verify_password",
]

"""会话令牌 —— 生成与哈希。

契约见 ``docs/contract-08-auth-network.md`` §3.4：

- 令牌 32 字节随机（``secrets.token_urlsafe``）；
- **数据库只存 SHA-256 hash**，明文只出现在 ``Set-Cookie``；
- 认证时用 hash 查库。

## 为什么令牌用 SHA-256 而不是 bcrypt

令牌本身是 256 bit 高熵随机串，不存在字典攻击空间，不需要慢哈希；而**每次
请求都要查会话**，必须快。这与密码的处理逻辑完全不同，不要混为一谈。

## 为什么不用 JWT

JWT 无法即时吊销：改密码、禁用用户、成员退出后旧 token 仍然有效直到过期。
而 PRD FR-N8 的核心要求就是**即时撤权**。
"""

from __future__ import annotations

import hashlib
import secrets

#: 令牌随机字节数
TOKEN_BYTES = 32

#: SHA-256 十六进制长度
TOKEN_HASH_LENGTH = 64


def generate_session_token() -> str:
    """生成一个新的会话令牌（明文）。

    返回值**只能**用于 ``Set-Cookie``，不得落库、不得写日志。
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """会话令牌 → 入库用的 SHA-256 十六进制。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

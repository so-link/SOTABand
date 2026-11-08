"""会话 Cookie 的下发与清除。

契约见 ``docs/contract-08-auth-network.md`` §4.1（Cookie 属性）与 §3.4。

## 属性约定

```text
Set-Cookie: sotaband_session=<token>;
            HttpOnly;            ← 前端脚本读不到，从根上消除 XSS 窃取路径
            SameSite=Lax;        ← 挡住绝大多数跨站写请求（CSRF）
            Path=/;
            Max-Age=<ttl>;
            [Secure]             ← 仅生产 https 开启
```

``Secure`` **必须按环境条件开启**：开发是 ``http://localhost``，硬编码 Secure 会让
浏览器直接丢弃 Cookie。用 ``SOTABAND_COOKIE_SECURE=1`` 在生产打开。

## 为什么令牌不进 localStorage

PRD §14 风险表：「localStorage Token —— 易受 XSS」。本项目前端要渲染 LLM 生成的
代码与 Markdown，XSS 面不小。HttpOnly Cookie 让脚本**读不到**令牌。
"""

from __future__ import annotations

from fastapi import Response

from config.settings import settings


def set_session_cookie(response: Response, token: str) -> None:
    """下发会话 Cookie。``token`` 是明文，只在这一次响应里出现。"""
    response.set_cookie(
        key=settings.auth.cookie_name,
        value=token,
        max_age=settings.auth.session_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.auth.cookie_secure,
        samesite=settings.auth.cookie_samesite,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """清除会话 Cookie（登出）。

    属性必须与下发时一致（path / samesite / secure），否则浏览器不会清除同一个
    Cookie —— 会出现"点了登出但刷新又登录了"的诡异现象。
    """
    response.delete_cookie(
        key=settings.auth.cookie_name,
        httponly=True,
        secure=settings.auth.cookie_secure,
        samesite=settings.auth.cookie_samesite,
        path="/",
    )


__all__ = ["clear_session_cookie", "set_session_cookie"]

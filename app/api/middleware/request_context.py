"""请求上下文中间件 —— 分配并回写 ``request_id``。

## 为什么用纯 ASGI 而不是 Starlette 的 BaseHTTPMiddleware

``BaseHTTPMiddleware`` 会为每个请求包一层 streaming 适配，与本项目大量使用的
**SSE**（chat 流式输出、工具执行流式输出）配合时有已知风险：缓冲行为、
``BackgroundTask`` 时序、断连处理都可能出问题。

纯 ASGI 中间件只是包了一下 ``send``，对响应体零干预，对 SSE 完全透明。
"""

from __future__ import annotations

import uuid

from starlette.datastructures import MutableHeaders

#: 请求 id 的响应头名
REQUEST_ID_HEADER = "X-Request-ID"

#: 请求 id 的长度上限（防止客户端塞超长值进审计表）
_MAX_REQUEST_ID_LEN = 64


def _extract_request_id(scope) -> str:
    """优先使用客户端传入的 request id（便于跨服务串联），否则新生成。"""
    for name, value in scope.get("headers") or []:
        if name.lower() == b"x-request-id":
            try:
                candidate = value.decode("latin-1").strip()
            except Exception:  # noqa: BLE001
                break
            if candidate:
                return candidate[:_MAX_REQUEST_ID_LEN]
            break
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """为每个 HTTP 请求分配 ``request_id``，放进 ``scope["state"]`` 并回写响应头。

    下游通过 ``request.state.request_id`` 读取；审计事件用它把一次请求涉及的
    多条记录串起来（PRD §11「关键事件可通过 request_id 追踪」）。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_request_id(scope)
        # scope["state"] 是 Starlette 的 state 字典，Request.state 直接读它
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_wrapper)

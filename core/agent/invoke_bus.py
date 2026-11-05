"""Agent 间调用的投递层（FR-N7「接线」）—— 把授权判定接到消息总线上。

## 这个模块解决什么

批次 5 的 ``/invoke`` 接口只做到**授权判定**：告诉调用方"你被允许调用 X"，
然后返回。设计文档 §8.3 如实记录为遗留项「消息总线未接线」。

本模块补上后半段：判定通过后，把调用**真正投递**给目标 Agent 并等待响应。

## 设计取舍：为什么不自己实现传输层

PRD §2.4 明确把「Agent 底层通信协议（RPC / 消息总线传输层）」列为**非目标** ——
目标是在网络上做**授权**，不是替用户选 RPC 框架。所以这里：

- 复用仓库里已有的 :class:`~core.agent.bus.MessageBus` 抽象（Pub/Sub），
  而不是引入 gRPC / NATS / Redis；
- 传输层保持**可替换**：单机用 ``InProcessMessageBus``，将来要跨机只需换
  ``MessageBus`` 的实现，本模块的代码不用动。

## 频道约定

```text
agent.invoke.<resource_type>.<release_id>        请求频道（目标 Agent 订阅）
agent.invoke.reply.<correlation_id>              响应频道（调用方临时订阅）
```

## 在线 / 离线是**正常状态**，不是错误

目标 Agent 没启动时没有任何订阅者。这时接口返回
``delivered=false, delivery_status='offline'`` —— **授权仍然有效**，
调用方可以稍后重试。把"对方没上线"当成 4xx/5xx 会让调用方误以为被拒绝，
从而去排查权限问题。

## 超时

目标订阅了但迟迟不回（Agent 卡住）→ ``delivery_status='timeout'``。
默认 5 秒，可通过 ``SOTABAND_INVOKE_DELIVERY_TIMEOUT`` 调整。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from core.agent.bus import InProcessMessageBus, MessageBus

logger = logging.getLogger(__name__)

#: 投递结果状态
DELIVERY_OK = "ok"
DELIVERY_OFFLINE = "offline"
DELIVERY_TIMEOUT = "timeout"

REQUEST_CHANNEL_PREFIX = "agent.invoke."
REPLY_CHANNEL_PREFIX = "agent.invoke.reply."

#: 单次投递的默认等待上限（秒）
DEFAULT_TIMEOUT_SECONDS = 5.0

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


def _timeout_seconds() -> float:
    raw = os.getenv("SOTABAND_INVOKE_DELIVERY_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _request_channel(resource_type: str, release_id: str) -> str:
    return f"{REQUEST_CHANNEL_PREFIX}{resource_type}.{release_id}"


class InvocationBus:
    """把「授权通过的调用」投递给目标 Agent 并等待响应。

    ## 为什么在内存里维护 ``_online`` 集合，而不是问总线

    ``MessageBus`` 抽象只有 publish/subscribe，没有"这个频道有没有订阅者"的
    查询（刻意保持最小接口）。本类自己记账：``register_target`` 时登记、
    ``unregister_target`` 时移除。这样"目标在不在线"是 O(1) 判断，
    不需要发一条探测消息出去再等超时。
    """

    def __init__(self, bus: MessageBus | None = None) -> None:
        self._bus: MessageBus = bus or InProcessMessageBus()
        #: `("agent", "my-agent")` 形式的在线登记表
        self._online: set[tuple[str, str]] = set()

    # ── 目标侧：注册 / 注销 ──────────────────────────────────────────

    async def register_target(
        self, resource_type: str, release_id: str, handler: Handler
    ) -> None:
        """让一个运行中的 Agent 出现在总线上，可以接收调用。

        ``handler`` 收到请求字典，返回响应字典（或 None 表示无响应体）。
        """
        channel = _request_channel(resource_type, release_id)

        async def _on_request(message: dict[str, Any]) -> None:
            reply_channel = message.get("reply_channel")
            if not reply_channel:
                return
            try:
                result = await handler(message.get("payload") or {})
                await self._bus.publish(
                    reply_channel,
                    {"ok": True, "result": result},
                )
            except Exception as exc:
                logger.warning("Agent 处理调用失败: %s", exc, exc_info=True)
                await self._bus.publish(
                    reply_channel,
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                )

        await self._bus.subscribe(channel, _on_request)
        self._online.add((resource_type, release_id))

    async def unregister_target(self, resource_type: str, release_id: str) -> None:
        await self._bus.unsubscribe(_request_channel(resource_type, release_id))
        self._online.discard((resource_type, release_id))

    def is_online(self, resource_type: str, release_id: str) -> bool:
        return (resource_type, release_id) in self._online

    def online_targets(self) -> list[tuple[str, str]]:
        return sorted(self._online)

    # ── 调用方侧：投递 + 等响应 ──────────────────────────────────────

    async def deliver(
        self,
        *,
        resource_type: str,
        release_id: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """投递一次调用，返回 ``(delivery_status, response)``。

        离线时直接返回，不等待 —— 调用方拿到 ``offline`` 就知道该过会儿重试。
        """
        if not self.is_online(resource_type, release_id):
            return DELIVERY_OFFLINE, None

        correlation_id = uuid.uuid4().hex
        reply_channel = f"{REPLY_CHANNEL_PREFIX}{correlation_id}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def _on_reply(message: dict[str, Any]) -> None:
            if not future.done():
                future.set_result(message)

        await self._bus.subscribe(reply_channel, _on_reply)

        # ⚠️ 必须**并发**发布，不能 `await publish(...)` 之后再等 future。
        #
        # `InProcessMessageBus.publish` 是顺序 await 每个 handler 的 —— 也就是
        # 它要等到目标 Agent 处理完才返回。若先 await publish 再 wait_for，
        # 超时计时器在"目标已经处理完"之后才开始跑，超时分支永远无法触发
        # （这个 bug 是测试 `test_timeout_is_reported` 抓出来的）。
        #
        # 拆成 task 之后：publish 在后台跑，这边同时在等 reply，超时才有意义。
        publish_task = asyncio.create_task(
            self._bus.publish(
                _request_channel(resource_type, release_id),
                {
                    "correlation_id": correlation_id,
                    "reply_channel": reply_channel,
                    "payload": payload,
                },
            )
        )
        try:
            try:
                reply = await asyncio.wait_for(
                    future, timeout=timeout if timeout is not None else _timeout_seconds()
                )
            except TimeoutError:
                return DELIVERY_TIMEOUT, None
        finally:
            # 无论成功/超时/异常都要退订，否则 reply_channel 会永久驻留在总线里
            await self._bus.unsubscribe(reply_channel)

            # 超时后调用方已经放弃（对应 HTTP 请求也返回了）。此时目标若还在处理，
            # 就取消掉这个投递任务 —— 它的产物已无人接收。取消后仍要 await 一次
            # 以回收任务，避免 asyncio 报 "Task was destroyed but it is pending"。
            if not publish_task.done():
                publish_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await publish_task

        if not reply.get("ok", False):
            return DELIVERY_OK, {"error": reply.get("error", "目标 Agent 处理失败")}
        result = reply.get("result")
        return DELIVERY_OK, result if isinstance(result, dict) else {"result": result}


# ── 进程级单例 ────────────────────────────────────────────────────────
#
# 与 `login_throttle()` 等一致的惰性单例：测试可以 `set_invocation_bus(...)`
# 注入自己的总线，避免用例之间互相污染。

_BUS: InvocationBus | None = None


def invocation_bus() -> InvocationBus:
    global _BUS
    if _BUS is None:
        _BUS = InvocationBus()
    return _BUS


def set_invocation_bus(bus: InvocationBus | None) -> None:
    """替换（或重置）进程级总线。**仅供测试使用。**"""
    global _BUS
    _BUS = bus


__all__ = [
    "DELIVERY_OFFLINE",
    "DELIVERY_OK",
    "DELIVERY_TIMEOUT",
    "InvocationBus",
    "invocation_bus",
    "set_invocation_bus",
]

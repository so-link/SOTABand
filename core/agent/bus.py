"""消息总线 — 提供 Agent 间 Pub/Sub 通信（ADR-08-07 不引入新传输层）。

## 为什么保持这么小

PRD §2.4 把「Agent 底层通信协议（RPC / 消息总线传输层）」列为**非目标**。
所以这里只定义一个最小的可替换抽象：`publish` / `subscribe` / `unsubscribe`。
单机用 :class:`InProcessMessageBus`；将来要跨机，加一个实现同一个
:class:`MessageBus` 接口的类即可，调用方（`core/agent/invoke_bus.py`）不用改。

## 刻意**不**提供"频道有没有订阅者"的查询

三个原语就够用了。要不要知道"对方在不在线"属于 RPC 层的语义
（见 `InvocationBus._online` 自己记账），塞进总线接口会让抽象变胖，
也会诱导调用方用轮询探测代替明确的在线登记。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class MessageBus(ABC):
    """消息总线抽象。"""

    @abstractmethod
    async def publish(self, channel: str, message: dict) -> None:
        """发布消息到频道。"""
        ...

    @abstractmethod
    async def subscribe(
        self, channel: str, handler: Callable[[dict], Awaitable[None]]
    ) -> None:
        """订阅频道，收到消息时调用 handler。"""
        ...

    @abstractmethod
    async def unsubscribe(self, channel: str) -> None:
        """取消订阅。"""
        ...


class InProcessMessageBus(MessageBus):
    """进程内消息总线（开发/单机模式，基于直接回调）。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[dict], Awaitable[None]]]] = defaultdict(list)

    async def publish(self, channel: str, message: dict) -> None:
        """按订阅顺序依次 await 每个 handler。

        ⚠️ **单个 handler 失败不会影响其他订阅者**，但异常必须记日志 ——
        原先这里是 `except Exception: pass`，等于把 Agent 处理失败的原因彻底吞掉，
        排查"调用没反应"时会毫无线索。
        """
        for handler in list(self._subscribers.get(channel, [])):
            try:
                await handler(message)
            except Exception:
                logger.warning(
                    "消息处理失败 channel=%s handler=%r",
                    channel,
                    getattr(handler, "__qualname__", handler),
                    exc_info=True,
                )

    async def subscribe(
        self, channel: str, handler: Callable[[dict], Awaitable[None]]
    ) -> None:
        self._subscribers[channel].append(handler)

    async def unsubscribe(self, channel: str) -> None:
        self._subscribers.pop(channel, None)


__all__ = ["InProcessMessageBus", "MessageBus"]

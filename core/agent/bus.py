"""消息总线 — 提供 Agent 间 Pub/Sub 和 RPC 通信"""

import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Callable, Awaitable


class MessageBus(ABC):
    """消息总线抽象"""

    @abstractmethod
    async def publish(self, channel: str, message: dict) -> None:
        """发布消息到频道"""
        ...

    @abstractmethod
    async def subscribe(
        self, channel: str, handler: Callable[[dict], Awaitable[None]]
    ) -> None:
        """订阅频道，收到消息时调用 handler"""
        ...

    @abstractmethod
    async def unsubscribe(self, channel: str) -> None:
        """取消订阅"""
        ...


class InProcessMessageBus(MessageBus):
    """进程内消息总线（开发/单机模式，基于 asyncio.Queue）"""

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    async def publish(self, channel: str, message: dict) -> None:
        handlers = self._subscribers.get(channel, [])
        for handler in handlers:
            try:
                await handler(message)
            except Exception:
                pass  # 单 handler 失败不影响其他订阅者

    async def subscribe(
        self, channel: str, handler: Callable[[dict], Awaitable[None]]
    ) -> None:
        self._subscribers[channel].append(handler)

    async def unsubscribe(self, channel: str) -> None:
        self._subscribers.pop(channel, None)

"""消息总线 — 提供 Agent 间 Pub/Sub 和 RPC 通信

拓扑（todo 4.5 本地通信）：所有 Agent 运行在独立子进程中，**主进程是唯一的
路由中枢**。子进程之间没有直连通道，跨 Agent 消息都经主进程转发：

    子进程A ──stdout 帧──▶ 主进程（订阅表/路由） ──stdin 帧──▶ 子进程B

两种模式：
- Pub/Sub：publish 尽力而为，目标未启动/未订阅时丢弃不重投；订阅表在
  主进程，进程退出即自然失效。
- RPC：call_agent 发起请求，被调方以 execute 语义执行（事件流聚合为一次性
  响应）；目标未运行/超时/崩溃均回结构化错误，不抛异常。

帧格式与分发见 core/agent/protocol.py；子进程侧挂接见
core/agent/base.py（AgentBusClient）与 core/agent/entrypoint.py。
"""

import asyncio
from collections import defaultdict
from typing import Callable, Awaitable

from core.agent.protocol import (
    FRAME_BUS_MESSAGE,
    FRAME_BUS_PUBLISH,
    make_frame,
)


class AgentBusClient:
    """子进程侧总线客户端 — 经 entrypoint 的 stdin/stdout 通道与主进程通信。

    由 entrypoint 在协议通道建立后调用 bind() 挂接。Agent 代码在
    execute() 运行期间即可 await publish/call_agent：
    - publish 是写侧动作，立即返回（尽力而为）。
    - call_agent 挂起等待响应帧；entrypoint 的 stdin 读循环收到
      agent_call_response 帧后调用 resolve() 唤醒。
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._writer = None                       # entrypoint 的 stdout StreamWriter
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self._seq = 0

    def bind(self, writer, handlers: dict[str, Callable[[dict], Awaitable[None]]] | None = None):
        """绑定 stdout writer 与消息处理回调（{channel: async fn(message)}）。"""
        self._writer = writer
        if handlers:
            self._handlers.update(handlers)

    def add_handler(self, channel: str, handler: Callable[[dict], Awaitable[None]]):
        self._handlers[channel] = handler

    def _next_req(self) -> str:
        self._seq += 1
        return f"{self.agent_id}#{self._seq}"

    async def _send(self, frame: dict) -> bool:
        if self._writer is None:
            return False
        import json
        line = json.dumps(frame, ensure_ascii=False) + "\n"
        async with self._write_lock:
            try:
                self._writer.write(line.encode("utf-8"))
                await self._writer.drain()
                return True
            except Exception:
                return False

    async def subscribe(self, channels: list[str]) -> bool:
        """向主进程登记订阅（订阅表在主进程，进程退出自动失效）。"""
        return await self._send(make_frame(
            "o", "bus_subscribe", channels=list(channels)))

    async def publish(self, channel: str, message: dict | None = None) -> bool:
        """发布消息到频道（尽力而为，不保证有人接收）。"""
        return await self._send(make_frame(
            "o", "bus_publish", channel=channel, message=message or {},
            src=self.agent_id))

    async def call_agent(self, target: str, method: str,
                         params: dict | None = None,
                         timeout: float = 60) -> dict:
        """RPC 调用另一个 Agent，返回 {"ok": bool, "result"|"error": ...}。"""
        req = self._next_req()
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[req] = fut
        sent = await self._send(make_frame(
            "o", "agent_call", req=req, to=target, method=method,
            params=params or {}, timeout=timeout, src=self.agent_id))
        if not sent:
            self._pending.pop(req, None)
            return {"ok": False, "error": "通信通道不可用（stdout 已关闭）"}
        try:
            return await asyncio.wait_for(fut, timeout=timeout + 5)
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"调用 {target} 超时（{timeout}s）"}
        finally:
            self._pending.pop(req, None)

    async def platform_call(self, tool: str, args: dict | None = None, *,
                            session_id: str = "default", user_id: str = "default",
                            timeout: float = 300) -> dict:
        """请求主进程执行一次平台工具调用（对话内控制系统）。

        子进程**不直接访问数据库/文件系统/registry**：一切平台能力都经主进程
        的 core.chat_control 网关执行，由它做身份注入、参数校验、风险分级与审计。
        返回网关的结构化结果：{"status": "ok"|"pending"|"error", ...}；
        通道故障也回结构化 error，不抛异常。
        """
        req = self._next_req()
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[req] = fut
        sent = await self._send(make_frame(
            "o", "platform_call", req=req, tool=tool, args=args or {},
            session_id=session_id, user_id=user_id, src=self.agent_id))
        if not sent:
            self._pending.pop(req, None)
            return {"status": "error", "tool": tool,
                    "error": "与引擎的通信通道不可用，平台操作未执行"}
        try:
            # 网关侧全链路硬超时 300s，这里留 30s 余量
            return await asyncio.wait_for(fut, timeout=timeout + 30)
        except asyncio.TimeoutError:
            return {"status": "error", "tool": tool,
                    "error": f"平台调用超时（{timeout}s），操作结果以引擎审计记录为准"}
        finally:
            self._pending.pop(req, None)

    # ── entrypoint 内部：接收主进程帧 ──

    def resolve(self, frame: dict) -> None:
        """唤醒 call_agent 的等待（收到 agent_call_response 帧时调用）。"""
        fut = self._pending.get(frame.get("req"))
        if fut is not None and not fut.done():
            fut.set_result({
                "ok": bool(frame.get("ok")),
                "result": frame.get("result"),
                "error": frame.get("error"),
            })

    def resolve_platform(self, frame: dict) -> None:
        """唤醒 platform_call 的等待（收到 platform_call_response 帧时调用）。"""
        fut = self._pending.get(frame.get("req"))
        if fut is not None and not fut.done():
            fut.set_result(frame.get("result") or {
                "status": "error", "error": "网关未返回结果"})

    async def deliver(self, frame: dict) -> None:
        """投递订阅消息 / 被调 RPC。RPC 被调侧经 handle_call 回调执行。"""
        t = frame.get("type")
        if t == FRAME_BUS_MESSAGE:
            handler = self._handlers.get(frame.get("channel", ""))
            if handler is None:
                return
            try:
                await handler(frame.get("message") or {})
            except Exception:
                pass  # 订阅回调异常不影响其他消息
        elif t == "agent_call":
            result: dict
            try:
                handler = self._handlers.get("__call__")
                if handler is None:
                    result = {"ok": False, "error": f"{self.agent_id} 未实现 RPC 处理"}
                else:
                    result = await handler(
                        frame.get("from", ""), frame.get("method", ""),
                        frame.get("params") or {})
            except Exception as e:
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            await self._send(make_frame(
                "o", "agent_reply", req=frame.get("req"), **result))


class InProcessMessageBus:
    """进程内消息总线（开发/单机模式，基于内存订阅表，无 RPC 路由能力）。

    保留旧 API（publish/subscribe/unsubscribe），供主进程内部与测试使用；
    Agent 子进程间通信请使用 AgentFactory 的总线路由。
    """

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

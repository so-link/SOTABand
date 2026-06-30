"""Agent 进程封装 — 每个 Agent 运行在一个独立进程中"""

import asyncio
import json
import time

from core.agent.base import BaseAgent, AgentContext
from core.agent.bus import InProcessMessageBus


class AgentProcess:
    """Agent 进程核心循环

    启动后订阅消息总线，等待任务并执行，结果发布回总线。
    """

    def __init__(self, agent: BaseAgent, bus: InProcessMessageBus = None):
        self.agent = agent
        self.bus = bus or InProcessMessageBus()
        self._stop_event = asyncio.Event()
        self._started = False

    async def run(self):
        """Agent 主循环"""
        await self.agent.on_start()
        self._started = True

        # 订阅任务频道
        channel = f"agent.{self.agent.agent_id}.task"
        await self.bus.subscribe(channel, self._handle_task)

        # 健康心跳
        heartbeat_task = asyncio.create_task(self._heartbeat())

        # 发布启动通知
        await self.bus.publish("agent.heartbeat", {
            "agent_id": self.agent.agent_id,
            "status": "started",
            "timestamp": time.time(),
        })

        # 等待停止信号
        await self._stop_event.wait()

        # 清理
        heartbeat_task.cancel()
        await self.bus.unsubscribe(channel)
        await self.agent.on_stop()

    async def _handle_task(self, message: dict):
        """处理收到的任务消息"""
        ctx_data = message.get("context", {})
        ctx = AgentContext(
            agent_id=ctx_data.get("agent_id", self.agent.agent_id),
            session_id=ctx_data.get("session_id", "default"),
            user_id=ctx_data.get("user_id", "default"),
            metadata=ctx_data.get("metadata", {}),
        )
        params = message.get("params", {})
        reply_channel = message.get("reply_to", f"agent.{self.agent.agent_id}.response")

        try:
            async for event in self.agent.execute(ctx, **params):
                await self.bus.publish(reply_channel, event)
        except Exception as e:
            await self.bus.publish(reply_channel, {
                "event": "error",
                "data": {"message": str(e)},
            })

    async def _heartbeat(self):
        """每 10s 发送心跳"""
        while not self._stop_event.is_set():
            await asyncio.sleep(10)
            await self.bus.publish("agent.heartbeat", {
                "agent_id": self.agent.agent_id,
                "status": "running",
                "timestamp": time.time(),
            })

    def stop(self):
        """发送停止信号"""
        self._stop_event.set()

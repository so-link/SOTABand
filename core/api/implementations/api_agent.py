"""Agent 相关 API 实现"""
from core.agent.factory import AgentFactory


class ApiAgentStart:
    """api-agent-start: 启动 Agent"""
    factory = AgentFactory()

    @staticmethod
    async def call(**kwargs) -> dict:
        agent_id = kwargs.get("agent_id", "")
        impl_path = kwargs.get("impl_path", "")
        await ApiAgentStart.factory.start(agent_id, impl_path)
        return {"agent_id": agent_id, "status": "started"}


class ApiAgentStop:
    """api-agent-stop: 停止 Agent"""
    factory = AgentFactory()

    @staticmethod
    async def call(**kwargs) -> dict:
        agent_id = kwargs.get("agent_id", "")
        await ApiAgentStop.factory.stop(agent_id)
        return {"agent_id": agent_id, "status": "stopped"}

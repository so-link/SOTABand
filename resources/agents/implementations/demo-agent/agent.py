# Demo Agent
from core.agent.base import BaseAgent, AgentContext

class DemoAgent(BaseAgent):
    async def execute(self, ctx, **kwargs):
        yield {"event": "content", "data": {"text": "hello"}}
        yield {"event": "done", "data": {"messageId": ctx.session_id}}

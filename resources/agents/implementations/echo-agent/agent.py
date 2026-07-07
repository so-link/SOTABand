"""Agent: 回显 Agent — echo-agent"""

import os
import subprocess
import tempfile
import json
import sys
from pathlib import Path
from typing import AsyncGenerator

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import BaseAgent, AgentContext

class EchoAgent(BaseAgent):
    """回显 Agent — echo-agent"""

    def __init__(self, spec=None):
        super().__init__(spec)


    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        """Agent 主执行逻辑"""
        content = kwargs.get("content", "").strip()
        if not content:
                yield {"event": "content", "data": {"text": "输入内容为空，请输入一些文本。"}}
        else:
                yield {"event": "content", "data": {"text": content}}
        yield {"event": "done", "data": {"messageId": ctx.session_id}}

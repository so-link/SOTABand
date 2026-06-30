"""Agent: 广州天气定时报告器 — """

import sys
from pathlib import Path
from typing import AsyncGenerator

# 确保项目根目录可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import BaseAgent, AgentContext


class GuangzhouWeatherReporter(BaseAgent):
    """Task agent"""

    def __init__(self, spec=None):
        super().__init__(spec)
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from config.settings import settings
            from core.llm.client import DeepSeekClient
            self._llm = DeepSeekClient(settings.llm)
        return self._llm

    def _build_prompt(self) -> str:
        """从 MD 规范文档构建 system prompt"""
        if self.spec and self.spec.raw_md:
            return self.spec.raw_md
        return "You are a helpful AI assistant."

    async def execute(self, ctx: AgentContext, **kwargs) -> AsyncGenerator[dict, None]:
        content = kwargs.get("content", "")
        attachments = kwargs.get("attachments", [])

        system_prompt = self._build_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        if attachments:
            att_info = ", ".join(
                a.get("fileName", a.get("file_name", "unknown"))
                for a in attachments
            )
            messages.append(
                {"role": "user", "content": f"[附加文件: {att_info}]\n\n{content}"}
            )
        else:
            messages.append({"role": "user", "content": content})

        llm = self._get_llm()
        full = ""
        async for token in llm.chat_stream(messages=messages):
            full += token
            yield {"event": "content", "data": {"text": token}}

        yield {"event": "done", "data": {"messageId": ctx.session_id}}

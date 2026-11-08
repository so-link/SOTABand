"""Agent: 城市信息查询 Agent — city-info-query-agent

通过继承 InteractiveAgentBase 复用交互式 Agent 的核心能力：
- 工具匹配（LLM 语义 + 关键词兜底，限定在本 Agent 声明的工具集内）
- 参数引导（状态机逐个引导，含类型纠正与校验）
- 工具调用（统一走 ToolExecutor）
- 多轮会话状态管理

本 Agent 声明的工具集来自 MD 规范的 【【城市天气预报】】、【【航班查询工具】】。
"""

import sys
from pathlib import Path
import json
import os
import subprocess
import tempfile
from typing import AsyncGenerator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.base import AgentRole, AgentSpec  # noqa: E402
from core.agent.interactive_base import InteractiveAgentBase  # noqa: E402


class CityInfoQueryAgent(InteractiveAgentBase):
    """城市信息查询 Agent — 对话式查询天气 / 航班。"""

    def __init__(self, spec=None):
        if spec is None:
            spec = AgentSpec(
                id="city-info-query-agent",
                name="城市信息查询 Agent",
                version="0.1.0",
                role=AgentRole.INTERACTIVE,
                description="接收用户自然语言查询，通过多轮对话调用城市天气预报或航班查询工具。",
            )
        super().__init__(spec)

    def build_system_prompt(self) -> str:
        """定制 System Prompt：明确本 Agent 的能力边界（天气 / 航班）。"""
        base = (
            "你是「城市信息查询 Agent」，一个专注于城市信息查询的助手。\n\n"
            "## 能力\n"
            "- 查询指定城市的天气\n"
            "- 查询指定城市的航班信息\n\n"
            "## 交互要求\n"
            "- 用户需要查询天气或航班时，引导其提供城市名称（航班还需数量）\n"
            "- 超出天气 / 航班范围的问题，礼貌说明能力边界\n"
            "- 回复简洁、友好、专业\n"
        )
        tools = self._get_active_tools()
        if tools:
            lines = "\n".join(f"- {t['id']}: {t.get('name', '')}" for t in tools)
            base += f"\n## 当前可用工具（{len(tools)} 个）\n{lines}"
        return base

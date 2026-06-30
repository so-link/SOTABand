"""Agent 基类 — 所有 Agent 的抽象"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator


class AgentRole(Enum):
    INTERACTIVE = "interactive"      # 交互型 — 与用户对话
    TASK = "task"                    # 任务型 — 执行具体数据处理
    ORCHESTRATOR = "orchestrator"    # 编排型 — 协调多 Agent
    OBSERVER = "observer"            # 观测型 — 监控与报告


@dataclass
class AgentContext:
    """每次调用的执行上下文"""

    agent_id: str
    session_id: str
    user_id: str = "default"
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentSpec:
    """从 MD 规范文档解析出的结构化规格"""

    id: str
    name: str
    version: str = "0.1.0"
    role: AgentRole = AgentRole.TASK
    description: str = ""
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    required_tools: list = field(default_factory=list)
    optional_tools: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    communication: dict = field(default_factory=dict)
    raw_md: str = ""


class BaseAgent(ABC):
    """Agent 基类 — 所有 Agent 必须继承"""

    def __init__(self, spec: AgentSpec, config: dict = None):
        self.spec = spec
        self.config = config or spec.config
        self._running = False

    @property
    def agent_id(self) -> str:
        return self.spec.id

    @abstractmethod
    async def execute(
        self, ctx: AgentContext, **kwargs
    ) -> AsyncGenerator[dict, None]:
        """
        执行 Agent 核心任务。

        Args:
            ctx: 执行上下文
            **kwargs: 输入参数（与 spec.inputs 对应）

        Yields:
            dict: 输出事件，格式 {"event": "content"|"card"|"done"|"error", "data": {...}}
        """
        ...

    async def on_start(self):
        """Agent 启动时的初始化（子类可覆盖）"""
        self._running = True

    async def on_stop(self):
        """Agent 停止时的清理（子类可覆盖）"""
        self._running = False

    async def health_check(self) -> bool:
        """健康检查（子类可覆盖）"""
        return self._running

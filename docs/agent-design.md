# Agent 完整生命周期设计

> 从自然语言描述到独立进程运行 — Agent 的规格化、构造、注册、启动全流程。

---

## 一、Agent 生命周期总览

```
┌─ Phase 1: 规格化 ────────────────────────────────────────────┐
│  用户自然语言描述 → LLM 转换 → MD 规范描述文档                   │
│       ↓ 用户审阅修改                                          │
│  保存到 resources/agents/definitions/{agent-name}.md           │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 2: 构造 ──────────────────────────────────────────────┐
│  核心层读取 MD 规范文档                                        │
│       ↓                                                      │
│  模板引擎 + 代码生成 → Agent Python 代码                        │
│       ↓ 沙箱预览 + 用户核验                                    │
│  保存到 core/agent/implementations/{agent-name}/               │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 3: 注册 ──────────────────────────────────────────────┐
│  资源注册中心登记 Agent                                        │
│       ↓                                                      │
│  资源发现器可检索 → 编排系统可引用                               │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 4: 运行 ──────────────────────────────────────────────┐
│  Agent 工厂启动独立进程                                        │
│       ↓                                                      │
│  通过通信总线与系统其他部分交互                                   │
│       ↓                                                      │
│  健康监控 + 生命周期管理                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、Phase 1 — Agent 规格化（MD 规范描述文档）

### 2.1 MD 规范描述文档格式

每个 Agent 必须有一份标准化 Markdown 描述文档。这是 Agent 的"源代码"——人类可读、LLM 可解析、系统可执行。

```markdown
---
id: interactive-agent
name: 交互Agent
version: 1.0.0
role: interactive
status: active
created: 2026-06-25
---

# 交互Agent

## 1. 功能概述

负责与用户进行自然语言对话，理解用户意图，引导任务编排流程。
是 MAIA Engine 的主交互入口，所有用户请求首先经过此 Agent。

## 2. 角色定位

- **角色类型**: interactive（交互型）
- **在系统中的位置**: 应用层与核心层之间的桥梁
- **协作对象**: 编排Agent、任务Agent、资源发现器

## 3. 输入规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 用户自然语言输入 |
| attachments | FileAttachment[] | 否 | 附加文件列表 |
| session_id | string | 是 | 会话标识 |
| user_id | string | 是 | 用户标识 |

## 4. 输出规范

| 事件类型 | 说明 | 数据结构 |
|----------|------|---------|
| content | 文本增量（流式） | `{"text": "..."}` |
| card | 内联卡片 | `{"type": "...", "title": "...", "data": {...}}` |
| done | 响应结束 | `{"message_id": "..."}` |
| error | 错误信息 | `{"code": "...", "message": "..."}` |

## 5. 运行机制

### 5.1 处理流程

1. 接收用户输入（content + attachments）
2. 构建 System Prompt（角色描述 + 可用工具列表）
3. 调用远程 LLM（流式）
4. 解析 LLM 响应中的结构化卡片数据
5. 逐事件返回给调用方

### 5.2 状态管理

- **有状态**: 维护会话历史（最近 N 轮对话）
- **会话存储**: Redis / 内存（可配置）

### 5.3 超时与重试

- LLM 调用超时: 60s
- 最大重试次数: 2
- 降级策略: 返回友好错误提示

## 6. 工具使用

### 6.1 必选工具

| 工具ID | 工具名称 | 用途 |
|--------|---------|------|
| tool-llm-client | LLM 调用客户端 | 调用远程大模型接口 |

### 6.2 可选工具（条件触发）

| 工具ID | 工具名称 | 触发条件 |
|--------|---------|---------|
| tool-resource-discoverer | 资源发现器 | 用户提及工具/数据/模型时 |
| tool-code-builder | 代码构建器 | 无匹配工具需自动生成时 |
| tool-orchestrator | 编排器 | 用户描述复杂多步任务时 |

## 7. 通信协议

- **入站**: HTTP/SSE（由 API 路由转发）
- **出站**: 消息总线 Pub/Sub（向其他 Agent 发消息）
- **消息格式**: JSON

## 8. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| llm_model | gpt-4o | 使用的 LLM 模型 |
| max_history | 20 | 最大会话历史轮数 |
| temperature | 0.7 | LLM 温度参数 |
| max_tokens | 4096 | 最大输出 token 数 |
| stream_enabled | true | 是否启用流式输出 |

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-06-25 | 初始版本 |
```

### 2.2 自然语言 → MD 规范文档 的转换流程

```
用户: "我需要一个能分析EEG数据、检测异常信号、生成报告的Agent"

        ↓ 调用 LLM（转换专用 Prompt）

系统生成 → resources/agents/definitions/eeg-analysis-agent.md

        ↓ 用户在编排编辑器中审阅

用户修改: 增减工具、调整参数、补充说明

        ↓ 确认保存

MD 规范文档落盘 → 进入 Phase 2
```

**转换 Prompt 的核心指令：**
- 根据用户描述，推断 Agent 的角色类型
- 填充标准模板的所有字段
- 如果用户未提及，合理推断输入/输出格式
- 建议合适的工具列表
- 标注不确定的字段供用户确认

### 2.3 MD 文档存储位置

```
resources/agents/definitions/
├── interactive-agent.md       # 交互Agent
├── data-loader-agent.md       # 数据加载Agent
├── eeg-analysis-agent.md      # EEG分析Agent（用户生成）
└── ...
```

---

## 三、Phase 2 — Agent 构造（代码生成）

### 3.1 Agent 基类设计

所有 Agent 共享的抽象基类，位于 `core/agent/base.py`：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator, Any
from enum import Enum

class AgentRole(Enum):
    INTERACTIVE = "interactive"    # 交互型
    TASK = "task"                  # 任务型
    ORCHESTRATOR = "orchestrator"  # 编排型
    OBSERVER = "observer"          # 观测型

@dataclass
class AgentContext:
    """每次调用的执行上下文"""
    agent_id: str
    session_id: str
    user_id: str
    metadata: dict = field(default_factory=dict)

@dataclass
class AgentSpec:
    """从 MD 规范文档解析出的结构化规格"""
    id: str
    name: str
    version: str
    role: AgentRole
    description: str
    inputs: dict       # 输入字段定义
    outputs: dict      # 输出事件定义
    required_tools: list[str]
    optional_tools: list[str]
    config: dict       # 配置参数默认值
    communication: dict  # 通信配置
    raw_md: str        # 原始 MD 文档

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
        执行 Agent 任务的核心方法。

        Args:
            ctx: 执行上下文
            **kwargs: 输入参数（与 spec.inputs 对应）

        Yields:
            dict: 输出事件（与 spec.outputs 对应）
        """
        ...

    @abstractmethod
    async def on_start(self):
        """Agent 进程启动时的初始化"""
        ...

    @abstractmethod
    async def on_stop(self):
        """Agent 进程停止时的清理"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        ...

    @classmethod
    def from_spec_file(cls, md_path: str, config: dict = None) -> "BaseAgent":
        """从 MD 规范文档文件构造 Agent 实例"""
        spec = parse_agent_md(md_path)
        agent_class = get_agent_class(spec.role)
        return agent_class(spec, config)
```

### 3.2 Agent 代码生成器

位于 `core/resource/builder.py`，负责从 MD 规范文档生成 Agent 骨架代码：

```python
class AgentCodeBuilder:
    """从 Agent MD 规范文档生成 Agent Python 代码"""

    def __init__(self, llm_client: "LLMClient"):
        self.llm = llm_client

    async def generate(self, spec: AgentSpec) -> str:
        """
        生成 Agent 实现代码。

        策略：
        1. 如果 role 是标准类型 → 使用预定义模板 + spec 参数填充
        2. 如果 role 是自定义类型 → 调用 LLM 生成代码
        3. 生成的代码继承 BaseAgent，实现所有抽象方法
        """
        if spec.role in STANDARD_ROLES:
            return self._template_generate(spec)
        else:
            return await self._llm_generate(spec)

    def _template_generate(self, spec: AgentSpec) -> str:
        """基于模板生成：填充 execute()、工具调用、配置读取"""
        template = AGENT_TEMPLATES[spec.role]
        return template.format(
            agent_id=spec.id,
            agent_name=spec.name,
            required_tools=spec.required_tools,
            config_defaults=spec.config,
            # ...
        )

    async def _llm_generate(self, spec: AgentSpec) -> str:
        """调用 LLM 生成自定义 Agent 代码"""
        prompt = self._build_generation_prompt(spec)
        response = await self.llm.chat([{"role": "user", "content": prompt}])
        return self._extract_code(response)
```

**生成物产出目录：**

```
core/agent/implementations/
└── {agent-id}/
    ├── __init__.py
    ├── agent.py         # Agent 实现（继承 BaseAgent）
    ├── spec.md          # MD 规范文档副本
    └── config.yaml      # 运行时配置
```

### 3.3 沙箱预览与用户核验

```
代码生成完成
    ↓
沙箱引擎启动隔离环境（Docker/Process）
    ↓
预跑: python agent.py --dry-run --spec spec.md
    ↓
输出检查: 语法 ✓  依赖 ✓  接口匹配 ✓
    ↓
用户在前端"代码核验视图"中审阅:
  - 左侧: 代码编辑器
  - 右侧: 沙箱运行结果
    ↓
用户决策:
  ├── [批准] → 进入 Phase 3（注册）
  ├── [修改] → 用户编辑代码 → 重新核验
  └── [拒绝] → 丢弃，保留 MD 文档
```

---

## 四、Phase 3 — Agent 注册

### 4.1 注册流程

```
┌─ 资源注册中心 (core/resource/registry.py) ──────────────────┐
│                                                             │
│  Agent 注册请求                                               │
│    ↓                                                        │
│  1. 校验 MD 规范文档完整性                                     │
│  2. 校验 Agent 代码（继承 BaseAgent、实现抽象方法）              │
│  3. 分配唯一 Agent ID                                        │
│  4. 写入 Agent 空间 (resources/agents/)                       │
│  5. 建立索引: 按角色/能力/工具/标签                            │
│  6. 发布注册事件 → 通信总线通知其他组件                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Agent 空间存储结构

```
resources/agents/
├── definitions/                    # MD 规范文档（人类 + LLM 可读）
│   ├── interactive-agent.md
│   ├── data-loader-agent.md
│   └── eeg-analysis-agent.md
│
├── registry.json                   # Agent 注册表（JSON 索引）
│   [
│     {
│       "id": "interactive-agent",
│       "name": "交互Agent",
│       "version": "1.0.0",
│       "role": "interactive",
│       "status": "active",
│       "spec_path": "definitions/interactive-agent.md",
│       "impl_path": "implementations/interactive-agent/",
│       "tools": ["tool-llm-client", "tool-resource-discoverer"],
│       "tags": ["交互", "对话", "入口"],
│       "created_at": "2026-06-25T10:00:00Z",
│       "health": "healthy"
│     }
│   ]
│
└── implementations/                # Agent 实现代码
    └── interactive-agent/
        ├── __init__.py
        ├── agent.py
        ├── spec.md
        └── config.yaml
```

### 4.3 资源发现

```python
class ResourceDiscoverer:
    """资源发现器 — Agent 可被按需检索"""

    async def find_agents(
        self,
        role: AgentRole = None,
        capabilities: list[str] = None,
        tools: list[str] = None,
        tags: list[str] = None,
    ) -> list[AgentSpec]:
        """按角色、能力、工具、标签检索 Agent"""
        ...
```

---

## 五、Phase 4 — Agent 运行（独立进程）

### 5.1 进程架构

每个 Agent 作为**独立操作系统进程**运行，与主 API 服务器解耦。

```
┌─ 主进程 (MAIA API Server) ──────────────────────────────────┐
│  FastAPI (HTTP + SSE)                                       │
│  Agent 工厂 (AgentFactory)                                   │
│  进程管理器 (ProcessManager)                                  │
│  通信总线客户端                                               │
└─────────────────────────────────────────────────────────────┘
          │                  │                  │
    Redis Pub/Sub (消息总线)
          │                  │                  │
┌─────────┴──┐    ┌─────────┴──┐    ┌─────────┴──┐
│ Agent 进程  │    │ Agent 进程  │    │ Agent 进程  │
│ interactive │    │ data-loader│    │ anomaly-det │
│ PID: 1001   │    │ PID: 1002  │    │ PID: 1003  │
│ Port: -     │    │ Port: -    │    │ Port: -    │
│ Status: ✓   │    │ Status: ✓   │    │ Status: ✓  │
└─────────────┘    └─────────────┘    └─────────────┘
```

### 5.2 Agent 进程

每个 Agent 进程的标准结构：

```python
# core/agent/process.py
import asyncio
import signal
import sys
from core.agent.base import BaseAgent
from core.agent.bus import MessageBus

class AgentProcess:
    """Agent 进程封装"""

    def __init__(self, agent: BaseAgent, bus: MessageBus):
        self.agent = agent
        self.bus = bus
        self._stop_event = asyncio.Event()

    async def run(self):
        """Agent 主循环"""
        await self.agent.on_start()

        # 订阅消息总线，等待任务
        await self.bus.subscribe(
            f"agent.{self.agent.agent_id}.task",
            self._handle_task,
        )

        # 定期健康检查上报
        asyncio.create_task(self._heartbeat())

        # 等待停止信号
        await self._stop_event.wait()
        await self.agent.on_stop()

    async def _handle_task(self, message: dict):
        """处理收到的任务消息"""
        ctx = AgentContext(**message["context"])
        try:
            async for event in self.agent.execute(ctx, **message["params"]):
                # 将输出事件发布回消息总线
                await self.bus.publish(
                    f"agent.{self.agent.agent_id}.response",
                    event,
                )
        except Exception as e:
            await self.bus.publish(
                f"agent.{self.agent.agent_id}.error",
                {"error": str(e)},
            )

    async def _heartbeat(self):
        """定期向总线发送心跳"""
        while not self._stop_event.is_set():
            await self.bus.publish(
                "agent.heartbeat",
                {"agent_id": self.agent.agent_id, "timestamp": time.time()},
            )
            await asyncio.sleep(10)

    def stop(self):
        self._stop_event.set()
```

### 5.3 通信总线

位于 `core/agent/bus.py`，提供 Pub/Sub 和 RPC 两种模式：

```python
from abc import ABC, abstractmethod
from typing import Callable, Awaitable

class MessageBus(ABC):
    """消息总线抽象"""

    @abstractmethod
    async def publish(self, channel: str, message: dict):
        """发布消息到频道"""
        ...

    @abstractmethod
    async def subscribe(
        self, channel: str, handler: Callable[[dict], Awaitable[None]]
    ):
        """订阅频道，收到消息时调用 handler"""
        ...

    @abstractmethod
    async def rpc_call(self, target_agent: str, method: str, params: dict) -> dict:
        """RPC 调用：向目标 Agent 发请求并等待响应"""
        ...

class RedisMessageBus(MessageBus):
    """基于 Redis 的消息总线实现"""
    ...

class InProcessMessageBus(MessageBus):
    """进程内消息总线（开发/单机模式）"""
    ...
```

### 5.4 Agent 工厂 + 进程管理器

位于 `core/agent/factory.py`：

```python
class AgentFactory:
    """Agent 工厂 — 实例化 + 池化管理 + 生命周期"""

    def __init__(self, bus: MessageBus, registry: AgentRegistry):
        self.bus = bus
        self.registry = registry
        self.process_manager = ProcessManager()

    async def start_agent(self, agent_id: str) -> str:
        """
        启动一个 Agent 进程：
        1. 从注册表获取 Agent 信息
        2. 加载代码模块
        3. 创建 AgentProcess
        4. 启动独立进程
        5. 等待健康检查通过
        6. 返回进程 ID
        """
        ...

    async def stop_agent(self, agent_id: str):
        """停止 Agent 进程"""
        ...

    async def restart_agent(self, agent_id: str):
        """重启 Agent 进程"""
        ...

    async def list_running(self) -> list[dict]:
        """列出所有运行中的 Agent 及其状态"""
        ...

class ProcessManager:
    """操作系统进程管理器"""

    def spawn(self, agent_id: str, entrypoint: str) -> int:
        """启动子进程，返回 PID"""
        ...

    def kill(self, pid: int, graceful: bool = True):
        """终止进程（SIGTERM / SIGKILL）"""
        ...

    def is_alive(self, pid: int) -> bool:
        """检查进程是否存活"""
        ...

    def list_all(self) -> list[dict]:
        """列出所有被管理的进程"""
        ...
```

### 5.5 Agent 启动入口

```python
# core/agent/entrypoint.py
"""每个 Agent 进程的入口脚本"""

import asyncio
import argparse
from core.agent.base import BaseAgent
from core.agent.bus import RedisMessageBus
from core.agent.process import AgentProcess

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--spec", required=True, help="MD 规范文档路径")
    parser.add_argument("--bus-url", default="redis://localhost:6379")
    args = parser.parse_args()

    # 从 MD 文档构造 Agent
    agent = BaseAgent.from_spec_file(args.spec)

    # 连接消息总线
    bus = RedisMessageBus(args.bus_url)

    # 启动 Agent 进程
    process = AgentProcess(agent, bus)
    await process.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### 5.6 进程启动方式

```
# 通过 Agent 工厂启动（主进程内部）
factory.start_agent("interactive-agent")
  → ProcessManager.spawn()
    → python -m core.agent.entrypoint \
        --agent-id interactive-agent \
        --spec resources/agents/definitions/interactive-agent.md \
        --bus-url redis://localhost:6379
      → PID 1001
    → 等待 health_check 通过
    → 注册到运行列表

# 停止
factory.stop_agent("interactive-agent")
  → ProcessManager.kill(1001, graceful=True)
    → SIGTERM → Agent.on_stop() 清理
    → 5s 后仍未退出 → SIGKILL
```

---

## 六、交互 Agent 与 API 服务器的关系

交互 Agent 是特殊的——它需要直接与前端通信（SSE）。有两种架构选择：

### 方案 A：API 服务器内嵌 Agent（推荐第一阶段）

```
前端 ← SSE → FastAPI ← 直接调用 → InteractiveAgent 实例（同进程）
                                   ↓
                              调用 LLM API
```

- 优点：简单，无跨进程延迟
- 缺点：交互 Agent 与 API 服务器耦合

### 方案 B：交互 Agent 也是独立进程

```
前端 ← SSE → FastAPI ← 消息总线 → InteractiveAgent 进程
                      (RPC/ Pub-Sub)
```

- 优点：架构统一，所有 Agent 都是独立进程
- 缺点：增加 SSE 到消息总线的桥接层

**建议：第一阶段用方案 A，后续统一迁移到方案 B。**

---

## 七、文件清单

### 新增文件（15 个）

```
# Agent 基类与运行时
core/agent/base.py              # Agent 基类 + AgentSpec
core/agent/bus.py               # 消息总线（Redis + InProcess）
core/agent/factory.py           # Agent 工厂 + ProcessManager
core/agent/process.py           # Agent 进程封装
core/agent/entrypoint.py        # Agent 进程入口脚本
core/agent/implementations/
└── interactive-agent/
    ├── __init__.py
    ├── agent.py                # 交互Agent 实现
    ├── spec.md                 # MD 规范文档副本
    └── config.yaml             # 运行时配置

# 资源构建
core/resource/builder.py        # Agent 代码生成器（从 MD 生成代码）
core/resource/registry.py       # 资源注册中心
core/resource/discoverer.py     # 资源发现器

# API 层
app/main.py                     # FastAPI 入口
app/api/routes/chat.py          # /api/chat/send SSE 端点
app/api/schemas/chat.py         # Pydantic 模型

# MD 规范文档
resources/agents/definitions/
└── interactive-agent.md        # 交互Agent 规范文档

# 前端
frontend/src/services/api/chat.ts  # ApiChatService
```

### 修改文件（3 个）

```
config/settings.py               # +LLMConfig, +AgentProcessConfig, +MessageBusConfig
frontend/src/stores/chat-store.ts # Mock → Api
frontend/.env.development         # +VITE_API_BASE_URL
```

---

## 八、端到端调用链路（以交互Agent为例）

```
┌─ 浏览器 ────────────────────────────────────────────────────┐
│ POST /api/chat/send {"content": "分析EEG数据", ...}          │
└──────────────────────────────────────────────────────────────┘
                         │ SSE
┌─ FastAPI (app/main.py) ────────────────────────────────────┐
│ chat_router                                                     │
│   → 从 AgentFactory 获取 interactive-agent 实例                  │
│   → agent.execute(ctx, content="分析EEG数据", attachments=[])   │
│      → _build_system_prompt()                                   │
│      → llm_client.chat_stream(messages)                         │
│         → POST https://api.openai.com/v1/chat/completions       │
│      ← 逐 token yield                                            │
│   → 每个 yield 转换为 SSE event:                                  │
│      {"event": "content", "data": {"text": "好的"}}              │
│      {"event": "content", "data": {"text": "，我来分析"}}         │
│      {"event": "card", "data": {...}}                            │
│      {"event": "done", "data": {"messageId": "msg-42"}}         │
└──────────────────────────────────────────────────────────────┘
                         │ SSE stream
┌─ 浏览器 ────────────────────────────────────────────────────┐
│ ApiChatService 读取 ReadableStream                               │
│   → store 更新 agentMessage.content（流式显示）                    │
│   → InlineCard 渲染工具匹配卡片                                    │
│   → done 事件结束流                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 九、实现顺序

```
Phase A: Agent 基架（1 天）
  ├── 1. core/agent/base.py — BaseAgent + AgentSpec
  ├── 2. core/agent/bus.py — InProcessMessageBus
  ├── 3. resources/agents/definitions/interactive-agent.md — 写定第一份 MD 规范
  └── 4. core/agent/implementations/interactive-agent/agent.py — 交互Agent（内嵌版）

Phase B: LLM 客户端 + API 对接（1 天）
  ├── 5. config/settings.py — LLMConfig
  ├── 6. core/llm/client.py — OpenAICompatibleClient
  ├── 7. app/main.py + app/api/routes/chat.py — FastAPI + SSE
  └── 8. 端到端测试: curl → API → Agent → LLM → SSE

Phase C: 前端对接（半天）
  ├── 9. frontend/src/services/api/chat.py — ApiChatService
  ├── 10. chat-store.ts 切换服务
  └── 11. 端到端测试: 浏览器 → API → Agent → LLM → 流式显示

Phase D: Agent 生命周期（后续）
  ├── 12. core/agent/process.py — 独立进程封装
  ├── 13. core/agent/factory.py — ProcessManager
  ├── 14. core/resource/registry.py — 注册中心
  ├── 15. core/resource/discoverer.py — 资源发现
  └── 16. core/resource/builder.py — MD → 代码生成器
```

---

## 十、讨论点

| # | 问题 | 推荐 | 备选 |
|---|------|------|------|
| 1 | 消息总线实现 | **第一阶段**: InProcess (asyncio.Queue)；**生产**: Redis Pub/Sub | NATS, RabbitMQ |
| 2 | Agent 进程管理 | Python `subprocess` + `signal` | Docker 容器 / systemd |
| 3 | MD 规范 → 代码的生成策略 | 标准角色用模板，自定义用 LLM | 全部用 LLM 生成 |
| 4 | 交互Agent 内嵌还是独立进程 | 第一阶段内嵌（方案 A） | 后续迁到方案 B |
| 5 | 进程间 Agent 发现 | 注册表 JSON 文件（开发）→ Redis（生产） | etcd, Consul |

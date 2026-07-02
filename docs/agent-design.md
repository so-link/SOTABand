# Agent 完整生命周期设计

> 从自然语言描述到独立进程运行 — Agent 的规格化、构造、注册、启动全流程。
>
> **默认 LLM：DeepSeek v4**（OpenAI 兼容协议，`api.deepseek.com/v1`）

---

## 一、系统启动与默认 Agent

### 1.1 开机即启动：交互 Agent

SOTABand Engine 启动时，**交互 Agent（Interactive Agent）作为第一个 Agent 自动启动**。它是系统的默认入口，无需用户手动创建。

```
系统启动 (python -m app.main)
    │
    ├── FastAPI 服务启动 (port 8000)
    │
    ├── AgentFactory 初始化
    │   └── 自动启动 交互Agent (PID 1001)
    │       ├── 读取 resources/agents/definitions/interactive-agent.md
    │       ├── 加载 core/agent/implementations/interactive-agent/agent.py
    │       └── AgentProcess.run() → 等待用户输入
    │
    ├── 通信总线就绪
    │
    └── 前端连接 → 对话视图 → 用户输入 → 交互Agent 处理
```

### 1.2 交互 Agent 的工作循环

```
用户在前端输入 "帮我分析这批EEG数据"
    │
    ▼
POST /api/chat/send  →  FastAPI 路由
    │
    ▼
InteractiveAgent.execute(ctx, content, attachments)
    │
    ├── 1. 构建 System Prompt（角色 + 可用工具列表 + 数据上下文）
    ├── 2. 调用 DeepSeek v4 API（流式）
    │      POST https://api.deepseek.com/v1/chat/completions
    │      Authorization: Bearer ${DEEPSEEK_API_KEY}
    │      Body: { model: "deepseek-v4", messages: [...], stream: true }
    ├── 3. 逐 token 接收 → yield content 事件
    ├── 4. 解析结构化卡片 → yield card 事件
    └── 5. yield done 事件
    │
    ▼
SSE 流 → 前端 ApiChatService → store → 界面流式显示
```

---

## 二、Agent 生命周期总览

```
┌─ Phase 1: 规格化 ────────────────────────────────────────────┐
│  用户在 Agent 编辑器中用自然语言描述需求                          │
│       ↓ 调用 DeepSeek v4 转换为标准 MD                         │
│  MD 规范文档展示在编辑器中，用户可交互修改                        │
│       ↓ 确认保存                                              │
│  保存到 resources/agents/definitions/{agent-name}.md           │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 2: 构造 ──────────────────────────────────────────────┐
│  用户点击 [生成] → 核心层读取 MD 规范文档                        │
│       ↓                                                      │
│  模板引擎 + 代码生成 → Agent Python 代码                        │
│       ↓ 沙箱自动测试                                          │
│  沙箱结果展示 → 用户核验                                       │
│       ↓ 用户批准                                              │
│  保存到 core/agent/implementations/{agent-name}/               │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 3: 注册 ──────────────────────────────────────────────┐
│  资源注册中心登记 Agent（自动）                                  │
│       ↓                                                      │
│  Agent 空间更新 → 资源发现器可检索                              │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 4: 运行 ──────────────────────────────────────────────┐
│  Agent 工厂启动独立进程（自动）                                  │
│       ↓                                                      │
│  通过通信总线与其他组件交互                                      │
│       ↓                                                      │
│  健康监控 + 生命周期管理                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、用户添加 Agent 的完整 UI 流程

### 3.1 入口：资源空间的 [+] 按钮

```
用户在左侧面板 → 资源空间标签 → Agent 空间 分类旁

    🤖 Agent 空间                    [+]
    ├── 交互Agent          ● healthy
    ├── 数据加载Agent       ● healthy
    └── ...

用户点击 [+] 按钮
    ↓
主面板切换到 → Agent 编辑器视图
```

### 3.2 Agent 编辑器视图（新增前端视图）

这是前端新增的第六个视图（在现有的对话/数据预览/代码核验/编排/任务监控之外）。

```
┌──────────────────────────────────────────────────────────┐
│  🤖 Agent 编辑器                                   [× 关闭] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Step 1: 描述需求                                        │
│  ┌──────────────────────────────────────────────────────┐│
│  │ 用自然语言描述你需要什么样的 Agent...                  ││
│  │                                                      ││
│  │ 例如: "我需要一个能分析EEG数据、检测异常信号、         ││
│  │ 自动生成可视化报告的Agent"                             ││
│  │                                                      ││
│  └──────────────────────────────────────────────────────┘│
│                                        [生成 MD 文档 →]  │
│                                                          │
│  ───────────────────────────────────────────────────────  │
│                                                          │
│  Step 2: 审阅 & 编辑 MD 规范文档                           │
│  ┌──────────────────────────────────────────────────────┐│
│  │ ---                                                  ││
│  │ id: eeg-analysis-agent                               ││
│  │ name: EEG分析Agent                                    ││
│  │ version: 0.1.0                                       ││
│  │ role: task                                           ││
│  │ ---                                                  ││
│  │                                                      ││
│  │ # EEG分析Agent                                        ││
│  │                                                      ││
│  │ ## 1. 功能概述                                        ││
│  │ 分析EEG数据，检测异常信号，生成可视化报告...             ││
│  │                                                      ││
│  │ ## 2. 角色定位                                        ││
│  │ - **角色类型**: task                                  ││
│  │ - **协作对象**: data-loader-agent                     ││
│  │                                                      ││
│  │ ...                                                  ││
│  └──────────────────────────────────────────────────────┘│
│                          [← 返回修改需求]  [→ 生成 Agent] │
│                                                          │
│  ───────────────────────────────────────────────────────  │
│                                                          │
│  Step 3: 沙箱测试 & 核验                                   │
│  ┌──────────────────┬───────────────────────────────────┐│
│  │  生成代码预览      │  沙箱测试结果                      ││
│  │                  │                                   ││
│  │  class EEG...    │  ✅ 语法检查通过                    ││
│  │  (BaseAgent):    │  ✅ 依赖检查通过                    ││
│  │    async def     │  ✅ 沙箱运行成功 (2.3s)             ││
│  │    execute(      │  ✅ 接口匹配通过                    ││
│  │      self,       │                                   ││
│  │      ctx, ...    │  输出: 3 个异常区间检测到            ││
│  │    ):            │                                   ││
│  │      ...         │                                   ││
│  └──────────────────┴───────────────────────────────────┘│
│                                                          │
│              [修改代码]  [拒绝]  [✅ 批准并注册上线]        │
│                                                          │
│  ───────────────────────────────────────────────────────  │
│                                                          │
│  Step 4: 注册 & 启动（自动）                                │
│  ┌──────────────────────────────────────────────────────┐│
│  │ ✅ Agent "eeg-analysis-agent" 已注册到 Agent 空间      ││
│  │ ✅ 进程已启动 (PID: 1003)                              ││
│  │ ✅ 健康检查通过                                        ││
│  │                                                      ││
│  │ 🎉 Agent 已就绪，可在任务编排中使用！                    ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│                                    [返回对话] [查看Agent] │
└──────────────────────────────────────────────────────────┘
```

### 3.3 Agent 编辑器状态机

```
[初始状态] 空白的 Step 1 输入框
    │
    │ 用户输入描述，点击 [生成 MD 文档]
    ▼
[MD 预览] Step 2 展示生成的 MD，可编辑
    │
    ├── 用户点击 [← 返回修改需求] → 回到初始状态
    │
    │ 用户点击 [→ 生成 Agent]
    ▼
[构造中] 显示进度：代码生成 → 沙箱测试
    │
    ▼
[核验] Step 3 展示代码 + 测试结果
    │
    ├── [修改代码] → 编辑代码 → 重新沙箱测试
    ├── [拒绝] → 丢弃代码，MD 文档保留在 Step 2
    │
    │ [✅ 批准并注册上线]
    ▼
[完成] Step 4 显示注册 & 启动结果
```

### 3.4 涉及的前端改动

```
新增文件:
  frontend/src/components/center-panel/AgentEditorView.tsx   # Agent 编辑器主视图
  frontend/src/stores/agent-editor-store.ts                   # 编辑器状态管理
  frontend/src/services/api/agent.ts                         # Agent CRUD API 调用

修改文件:
  frontend/src/stores/ui-store.ts           # ActiveView 增加 'agent-editor'
  frontend/src/components/center-panel/CenterPanel.tsx  # ViewTabBar 增加 Agent 编辑器标签
  frontend/src/components/left-panel/ResourceBrowser.tsx # Agent 空间项增加 [+] 按钮
```

---

## 四、Phase 1 — Agent 规格化（MD 规范描述文档）

### 4.1 交互Agent 的 MD 规范文档（系统预置，开机即用）

```markdown
---
id: interactive-agent
name: 交互Agent
version: 1.0.0
role: interactive
status: active
created: 2026-06-25
builtin: true
---

# 交互Agent

## 1. 功能概述

负责与用户进行自然语言对话，理解用户意图，引导任务编排流程。
是 SOTABand Engine 的主交互入口，所有用户请求首先经过此 Agent。

系统启动时自动加载运行，等待用户输入，调用 DeepSeek v4 大模型
解析用户意图，并将结果以流式 SSE 返回前端界面。

## 2. 角色定位

- **角色类型**: interactive（交互型）
- **在系统中的位置**: 应用层与核心层之间的桥梁，前端唯一直接对话的 Agent
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
3. 调用 DeepSeek v4 API（流式）
4. 解析 LLM 响应中的结构化卡片数据
5. 逐事件 SSE 返回给前端

### 5.2 System Prompt 结构

```
你是 SOTABand Engine 的交互Agent。
你的职责是理解用户意图，帮助用户完成数据处理任务。

当前可用的工具:
- eeg_bandpass_filter: EEG带通滤波器
- eeg_anomaly_detector: 基于深度学习的EEG异常检测
- spectral_analyzer: 多通道频谱分析
- data_loader: 通用数据加载工具
...

当前可用的 Agent:
- data-loader-agent: 数据加载专用Agent
- anomaly-detection-agent: 异常检测专用Agent
...

当前工作区间数据:
- subj01.edf (64通道, 256Hz)
- subj02.edf (64通道, 256Hz)

当用户需求简单时，直接推荐工具调用。
当用户需求复杂时，引导进入编排模式。
当没有匹配工具时，建议自动生成新工具。
```

### 5.3 状态管理

- **有状态**: 维护会话历史（最近 20 轮对话）
- **会话存储**: 内存（开发阶段）/ Redis（生产）

### 5.4 超时与重试

- DeepSeek v4 调用超时: 60s
- 最大重试次数: 2
- 降级策略: 返回友好错误提示

## 6. 工具使用

### 6.1 必选工具

| 工具ID | 工具名称 | 用途 |
|--------|---------|------|
| tool-llm-client | DeepSeek v4 客户端 | 调用 DeepSeek v4 大模型接口 |

### 6.2 可选工具（条件触发）

| 工具ID | 工具名称 | 触发条件 |
|--------|---------|---------|
| tool-resource-discoverer | 资源发现器 | 用户提及工具/数据/模型时 |
| tool-code-builder | 代码构建器 | 无匹配工具需自动生成时 |
| tool-orchestrator | 编排器 | 用户描述复杂多步任务时 |

## 7. 通信协议

- **入站**: HTTP/SSE（由 API 路由转发，与前端直连）
- **出站**: 消息总线 Pub/Sub（向其他 Agent 发消息）
- **消息格式**: JSON

## 8. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| llm_provider | deepseek | LLM 提供商 |
| llm_model | deepseek-v4 | 使用的 LLM 模型 |
| llm_base_url | https://api.deepseek.com/v1 | API 地址 |
| max_history | 20 | 最大会话历史轮数 |
| temperature | 0.7 | LLM 温度参数 |
| max_tokens | 4096 | 最大输出 token 数 |
| stream_enabled | true | 是否启用流式输出 |

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-06-25 | 初始版本，默认 LLM: DeepSeek v4 |
```

### 4.2 自然语言 → MD 规范文档（Agent 编辑器中触发）

```
用户在 Agent 编辑器的 Step 1 输入框中输入：

  "我需要一个能分析EEG数据、检测异常信号、生成报告的Agent"

点击 [生成 MD 文档]
    ↓
前端 POST /api/agent/generate-spec { "description": "我需要一个能..." }
    ↓
FastAPI → 调用 DeepSeek v4（规格化专用 Prompt）
    ↓
DeepSeek v4 根据描述填充标准模板 → 返回完整 MD 文档
    ↓
前端 Agent 编辑器 Step 2 展示生成的 MD 文档
    ↓
用户可编辑修改任何字段
    ↓
点击 [→ 生成 Agent] → 进入 Phase 2
```

**规格化 Prompt 的核心指令（发给 DeepSeek v4）：**
- 根据用户描述，推断 Agent 的角色类型（interactive / task / orchestrator / observer）
- 填充标准模板的全部 9 个段落
- 如果用户未提及，合理推断输入/输出格式
- 建议合适的工具列表（从已有工具空间中匹配）
- 标注不确定的字段供用户确认（如 `⚠️ 待确认: ...`）

### 4.3 MD 规范文档格式（标准模板）

每个 Agent 必须包含以下 9 个段落：

| 段落 | 必填 | 说明 |
|------|------|------|
| `frontmatter (--- ... ---)` | 是 | id, name, version, role, status |
| `## 1. 功能概述` | 是 | 用自然语言描述 Agent 做什么 |
| `## 2. 角色定位` | 是 | 角色类型、在系统中的位置、协作对象 |
| `## 3. 输入规范` | 是 | 输入字段表格（名称、类型、必填、说明） |
| `## 4. 输出规范` | 是 | 输出事件表格（事件类型、说明、数据结构） |
| `## 5. 运行机制` | 是 | 处理流程、状态管理、超时与重试 |
| `## 6. 工具使用` | 是 | 必选工具 + 可选工具（条件触发） |
| `## 7. 通信协议` | 是 | 入站、出站、消息格式 |
| `## 8. 配置参数` | 是 | 参数名、默认值、说明 |
| `## 9. 版本历史` | 是 | 版本、日期、变更 |

### 4.4 MD 文档存储

```
resources/agents/definitions/
├── interactive-agent.md       # 系统预置，开机自启（builtin: true）
├── data-loader-agent.md       # 系统预置
├── eeg-analysis-agent.md      # 用户通过 Agent 编辑器生成
└── ...
```

---

## 五、Phase 2 — Agent 构造（代码生成）

### 5.1 Agent 基类设计

所有 Agent 共享的抽象基类，位于 `core/agent/base.py`：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator
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
    inputs: dict
    outputs: dict
    required_tools: list[str]
    optional_tools: list[str]
    config: dict
    communication: dict
    raw_md: str

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
        """执行 Agent 核心任务，yield 输出事件"""
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
        """从 MD 规范文档构造 Agent 实例"""
        spec = parse_agent_md(md_path)
        agent_class = get_agent_class(spec.role)
        return agent_class(spec, config)
```

### 5.2 Agent 代码生成器

位于 `core/resource/builder/agent_builder.py`：

```python
class AgentCodeBuilder:
    """从 MD 规范文档生成 Agent Python 代码"""

    def __init__(self, llm_client: "LLMClient"):
        self.llm = llm_client  # DeepSeek v4

    async def generate(self, spec: AgentSpec) -> str:
        """
        生成策略：
        1. 标准角色（interactive/task/orchestrator/observer）
           → 预定义 Python 模板 + spec 参数填充
        2. 自定义角色
           → 调用 DeepSeek v4 生成代码
        3. 生成物继承 BaseAgent，实现所有抽象方法
        """
        if spec.role in STANDARD_ROLES:
            return self._template_generate(spec)
        else:
            return await self._llm_generate(spec)

    def _template_generate(self, spec: AgentSpec) -> str:
        """模板填充：execute()、工具调用、配置读取"""
        template = AGENT_TEMPLATES[spec.role]
        return template.format(
            agent_id=spec.id,
            agent_name=spec.name,
            required_tools=spec.required_tools,
            optional_tools=spec.optional_tools,
            config=spec.config,
        )

    async def _llm_generate(self, spec: AgentSpec) -> str:
        """DeepSeek v4 生成自定义 Agent 代码"""
        prompt = self._build_generation_prompt(spec)
        response = await self.llm.chat([
            {"role": "system", "content": "你是一个 Python 代码生成器..."},
            {"role": "user", "content": prompt},
        ])
        return self._extract_code(response)
```

**生成物目录：**

```
core/agent/implementations/
└── {agent-id}/
    ├── __init__.py
    ├── agent.py         # 继承 BaseAgent
    ├── spec.md          # MD 规范文档副本
    └── config.yaml      # 运行时配置
```

### 5.3 沙箱测试与用户核验

```
用户点击 [→ 生成 Agent]
    ↓
AgentCodeBuilder.generate(spec) → 生成 agent.py
    ↓
沙箱引擎（Process 隔离）:
  python agent.py --dry-run --spec spec.md
    ↓
自动检查:
  ✅ 语法 (ast.parse)
  ✅ 依赖 (import 检查)
  ✅ 接口 (BaseAgent 抽象方法实现)
  ✅ 预跑 (execute() 空输入测试)
    ↓
结果展示在 Agent 编辑器 Step 3:
  ├── 左侧: 代码高亮预览
  ├── 右侧: 沙箱测试结果
  └── 按钮: [修改代码] [拒绝] [✅ 批准并注册上线]
```

---

## 六、Phase 3 — Agent 注册

```
用户点击 [✅ 批准并注册上线]
    ↓
ResourceRegistry (core/resource/registry/__init__.py)
  → AgentRegistry (core/resource/registry/agent_registry.py)
    ├── 1. 校验 MD 规范文档完整性（9 段落）
    ├── 2. 校验代码有效性（继承 BaseAgent、实现抽象方法）
    ├── 3. 分配唯一 Agent ID
    ├── 4. 写入 resources/agents/registry.json
    ├── 5. 建立索引: 角色 / 能力 / 工具 / 标签
    ├── 6. 发布注册事件 → 通信总线
    └── 7. 返回注册结果
    ↓
Agent 空间自动刷新 → 前端左侧面板可看到新 Agent
```

---

## 七、Phase 4 — Agent 运行（独立进程）

### 7.1 进程架构

```
┌─ SOTABand API Server (主进程) ─────────────────────────────────┐
│  FastAPI (HTTP + SSE)                                      │
│  AgentFactory + ProcessManager                             │
│  通信总线客户端                                              │
│                                                            │
│  ★ 开机自动启动: InteractiveAgent (PID 1001, 内嵌 SSE)       │
└────────────────────────────────────────────────────────────┘
          │                  │                  │
    消息总线 (开发: InProcess / 生产: Redis Pub/Sub)
          │                  │                  │
┌─────────┴──┐    ┌─────────┴──┐    ┌─────────┴──┐
│ Agent 进程  │    │ Agent 进程  │    │ Agent 进程  │
│ interactive │    │ data-loader│    │ eeg-analysis│
│ PID: 1001   │    │ PID: 1002  │    │ PID: 1003  │
│ 开机自启     │    │ 手动启动    │    │ 用户创建    │
└─────────────┘    └─────────────┘    └─────────────┘
```

### 7.2 Agent 进程（核心循环）

```python
class AgentProcess:
    """Agent 进程封装 — 每个 Agent 一个独立进程"""

    async def run(self):
        await self.agent.on_start()

        # 订阅消息总线，等待任务
        await self.bus.subscribe(
            f"agent.{self.agent.agent_id}.task",
            self._handle_task,
        )

        # 健康心跳（每 10s）
        asyncio.create_task(self._heartbeat())

        # 等待停止信号
        await self._stop_event.wait()
        await self.agent.on_stop()

    async def _handle_task(self, message: dict):
        ctx = AgentContext(**message["context"])
        try:
            async for event in self.agent.execute(ctx, **message["params"]):
                await self.bus.publish(
                    f"agent.{self.agent.agent_id}.response", event
                )
        except Exception as e:
            await self.bus.publish(
                f"agent.{self.agent.agent_id}.error", {"error": str(e)}
            )
```

### 7.3 交互Agent 的特殊处理（内嵌 SSE）

交互 Agent 需要直接与前端通信（SSE 流式），第一阶段内嵌在 API 服务器中：

```
前端 ← SSE → FastAPI ← 同进程调用 → InteractiveAgent
                       (无消息总线中转)
```

其他 Agent 通过消息总线通信。后续所有 Agent 统一迁到独立进程 + 消息总线架构。

---

## 八、LLM 配置（DeepSeek v4）

### 8.1 `config/settings.py` 中的 LLM 配置

```python
@dataclass
class LLMConfig:
    """LLM 配置 — 默认 DeepSeek v4"""
    provider: str = "deepseek"
    api_key: str = ""                 # 从环境变量 DEEPSEEK_API_KEY 加载
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-v4"
    max_tokens: int = 4096
    temperature: float = 0.7
    streaming: bool = True
    timeout: int = 60
```

### 8.2 DeepSeek v4 客户端

DeepSeek v4 使用 **OpenAI 兼容协议**（`/v1/chat/completions`），可直接用 OpenAI Python SDK：

```python
from openai import AsyncOpenAI

class DeepSeekClient(LLMClient):
    """DeepSeek v4 客户端（OpenAI 兼容）"""

    def __init__(self, config: LLMConfig):
        self.client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )
        self.model = config.model

    async def chat_stream(self, messages, **kwargs):
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            max_tokens=kwargs.get("max_tokens", 4096),
            temperature=kwargs.get("temperature", 0.7),
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
```

**环境变量：**
```bash
# .env
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
```

---

## 九、API 端点总览

| 端点 | 方法 | 说明 | 调用方 |
|------|------|------|--------|
| `/api/chat/send` | POST (SSE) | 交互Agent 对话 | 前端对话视图 |
| `/api/agent/generate-spec` | POST | 自然语言 → MD 规范文档 | 前端 Agent 编辑器 (Step 1→2) |
| `/api/agent/generate-code` | POST | MD 规范文档 → Python 代码 + 沙箱测试 | 前端 Agent 编辑器 (Step 2→3) |
| `/api/agent/register` | POST | 注册 Agent 并启动进程 | 前端 Agent 编辑器 (Step 3→4) |
| `/api/agent/list` | GET | 列出所有 Agent | 前端资源空间 |
| `/api/agent/{id}/status` | GET | Agent 状态查询 | 前端/监控 |
| `/api/resource/{type}/list` | GET | 列出指定类型资源（通用） | 前端资源空间 |
| `/api/resource/{type}/{id}` | GET | 获取单个资源详情 | 前端属性面板 |
| `/api/resource/search?q=&type=` | GET | 跨资源搜索 | 前端搜索框 |

> `/api/resource/*` 是通用资源接口，`type` ∈ {data, tool, model, agent, task}。
> `/api/agent/*` 是 Agent 专用接口（含代码生成、沙箱测试等特有操作）。

---

## 十、资源管理子系统架构（可扩展设计）

### 10.1 设计原则

所有资源类型（Agent、工具、模型、数据、任务）都需要**注册**、**发现**能力，部分需要**构建**能力。为避免未来扩展时文件膨胀，采用**按功能分层、每层按资源类型分文件**的目录结构：

```
core/resource/
├── __init__.py                  # ResourceManager 统一入口
├── lifecycle.py                 # 跨资源生命周期管理（创建→版本升级→废弃→归档）
│
├── registry/                    # 注册中心层
│   ├── __init__.py              #   ResourceRegistry — 统一对外接口
│   ├── registry_base.py         #   BaseRegistry — 所有注册器的抽象基类
│   ├── agent_registry.py        #   AgentRegistry
│   ├── data_registry.py         #   DataRegistry
│   ├── tool_registry.py         #   ToolRegistry
│   ├── model_registry.py        #   ModelRegistry
│   └── task_registry.py         #   TaskRegistry
│
├── discoverer/                  # 发现器层
│   ├── __init__.py              #   ResourceDiscoverer — 统一对外接口
│   ├── discoverer_base.py       #   BaseDiscoverer — 所有发现器的抽象基类
│   ├── agent_discoverer.py      #   AgentDiscoverer (按角色/能力/工具检索)
│   ├── data_discoverer.py       #   DataDiscoverer (按格式/来源/质量检索)
│   ├── tool_discoverer.py       #   ToolDiscoverer (按类别/输入输出格式检索)
│   ├── model_discoverer.py      #   ModelDiscoverer (按框架/类型/精度检索)
│   └── task_discoverer.py       #   TaskDiscoverer (按状态/关联资源检索)
│
└── builder/                     # 构建器层
    ├── __init__.py              #   ResourceBuilder — 统一对外接口
    ├── builder_base.py          #   BaseBuilder — 所有构建器的抽象基类
    ├── agent_builder.py         #   AgentCodeBuilder (MD → Python 代码)
    ├── tool_builder.py          #   ToolCodeBuilder (需求 → 工具代码)
    └── model_builder.py         #   ModelBuilder (模型注册 + 配置生成)
```

### 10.2 基类设计

```python
# core/resource/registry/registry_base.py
class BaseRegistry(ABC):
    """资源注册基类"""

    ...

# core/resource/discoverer/discoverer_base.py
class BaseDiscoverer(ABC):
    """资源发现基类"""

    ...

# core/resource/builder/builder_base.py
class BaseBuilder(ABC):
    """资源构建基类"""

    ...
```

### 10.3 扩展新资源类型

当需要支持新的资源类型（如 `user`）时：

1. 在 `registry/` 下新建 `user_registry.py`，实现 `BaseRegistry`
2. 在 `discoverer/` 下新建 `user_discoverer.py`，实现 `BaseDiscoverer`
3. 在 `builder/` 下新建 `user_builder.py`（如需构建能力），实现 `BaseBuilder`
4. 在各层的 `__init__.py` 中注册新类型

**无需修改任何已有文件。**

---

## 十一、文件清单

### 新增文件

```
# Agent 基类与运行时
core/agent/__init__.py
core/agent/base.py                          # BaseAgent + AgentSpec + MD 解析器
core/agent/bus.py                           # 消息总线（InProcess + Redis）
core/agent/factory.py                       # AgentFactory + ProcessManager
core/agent/process.py                       # AgentProcess 主循环
core/agent/entrypoint.py                    # Agent 进程入口
core/agent/implementations/
└── interactive-agent/
    ├── __init__.py
    ├── agent.py                            # InteractiveAgent（开机自启）
    ├── spec.md                             # MD 规范文档副本
    └── config.yaml

# LLM 客户端
core/llm/__init__.py
core/llm/client.py                          # DeepSeekClient (OpenAI 兼容)

# 资源管理（按功能分层，按资源类型分文件，全局唯一文件名）
core/resource/__init__.py                   # 资源子系统统一入口
core/resource/lifecycle.py                  # 跨资源生命周期管理器
core/resource/registry/                     # 注册中心
├── __init__.py                             #   统一注册入口 (ResourceRegistry)
├── registry_base.py                        #   注册基类 (BaseRegistry)
├── agent_registry.py                       #   Agent 注册
├── data_registry.py                        #   数据注册
├── tool_registry.py                        #   工具注册
├── model_registry.py                       #   模型注册
└── task_registry.py                        #   任务注册
core/resource/discoverer/                   # 发现器
├── __init__.py                             #   统一发现入口 (ResourceDiscoverer)
├── discoverer_base.py                      #   发现基类 (BaseDiscoverer)
├── agent_discoverer.py                     #   Agent 发现
├── data_discoverer.py                      #   数据发现
├── tool_discoverer.py                      #   工具发现
├── model_discoverer.py                     #   模型发现
└── task_discoverer.py                      #   任务发现
core/resource/builder/                      # 构建器
├── __init__.py                             #   统一构建入口 (ResourceBuilder)
├── builder_base.py                         #   构建基类 (BaseBuilder)
├── agent_builder.py                        #   Agent 代码生成器
├── tool_builder.py                         #   工具代码生成器
└── model_builder.py                        #   模型构建器

# API 层
app/main.py                                 # FastAPI 入口 + 开机启动交互Agent
app/api/routes/__init__.py
app/api/routes/chat_routes.py               # /api/chat/send (SSE)
app/api/routes/agent_routes.py              # /api/agent/* (Agent 管理)
app/api/schemas/chat_schemas.py             # ChatRequest/SSEEvent 模型
app/api/schemas/agent_schemas.py            # AgentSpec/GenerateRequest 模型

# MD 规范文档
resources/agents/definitions/
└── interactive-agent.md                    # 交互Agent 规范（系统预置）

# Agent 模板
core/agent/templates/
├── interactive.py.tmpl                     # 交互型 Agent 模板
├── task.py.tmpl                            # 任务型 Agent 模板
├── orchestrator.py.tmpl                    # 编排型 Agent 模板
└── observer.py.tmpl                        # 观测型 Agent 模板

# 前端新增
frontend/src/components/center-panel/AgentEditorView.tsx
frontend/src/stores/agent-editor-store.ts
frontend/src/services/api/chat.ts           # ApiChatService (SSE)
frontend/src/services/api/agent.ts          # Agent CRUD API
```

### 修改文件

```
config/settings.py                           # +LLMConfig, +AgentProcessConfig
frontend/src/stores/chat-store.ts            # Mock → ApiChatService
frontend/src/stores/ui-store.ts              # +'agent-editor' 视图
frontend/src/components/center-panel/CenterPanel.tsx  # +AgentEditor 路由
frontend/src/components/left-panel/ResourceBrowser.tsx # +[+] 按钮
frontend/.env.development                    # +VITE_API_BASE_URL, +VITE_DEEPSEEK_KEY
```

---

## 十二、实现顺序

```
Phase A: 后端基架
  ├── 1. config/settings.py — LLMConfig (DeepSeek v4)
  ├── 2. core/llm/client.py — DeepSeekClient
  ├── 3. core/agent/base.py — BaseAgent + AgentSpec + MD 解析
  ├── 4. core/agent/bus.py — InProcessMessageBus
  ├── 5. resources/agents/definitions/interactive-agent.md
  └── 6. core/agent/implementations/interactive-agent/agent.py

Phase B: API + 开机自启
  ├── 7. app/main.py — FastAPI 入口，启动时自动加载交互Agent
  ├── 8. app/api/schemas/chat_schemas.py — Pydantic 模型
  ├── 9. app/api/routes/chat_routes.py — /api/chat/send SSE 端点
  └── 10. 端到端测试: curl → API → Agent → DeepSeek v4 → SSE

Phase C: 前端对接
  ├── 11. frontend/src/services/api/chat.ts — ApiChatService
  ├── 12. chat-store.ts 切换服务
  └── 13. 端到端: 浏览器 → API → Agent → DeepSeek v4 → 流式显示

Phase D: Agent 编辑器 (用户添加 Agent 的 UI 流程)
  ├── 14. core/resource/registry/registry_base.py — BaseRegistry 基类
  ├── 15. core/resource/registry/agent_registry.py — AgentRegistry
  ├── 16. core/resource/discoverer/discoverer_base.py — BaseDiscoverer 基类
  ├── 17. core/resource/discoverer/agent_discoverer.py — AgentDiscoverer
  ├── 18. core/resource/builder/builder_base.py — BaseBuilder 基类
  ├── 19. core/resource/builder/agent_builder.py — Agent 代码生成
  ├── 20. frontend/src/stores/agent-editor-store.ts
  ├── 21. frontend/src/components/center-panel/AgentEditorView.tsx
  ├── 22. ui-store.ts + CenterPanel.tsx — 注册新视图
  ├── 23. ResourceBrowser.tsx — Agent 空间 [+] 按钮
  ├── 24. app/api/routes/agent_routes.py — /api/agent/* 端点
  └── 25. 端到端: 点击 [+] → 描述需求 → 生成 MD → 审阅 → 生成代码 → 沙箱 → 注册 → 上线

Phase E: 独立进程（后续）
  ├── 22. core/agent/process.py — AgentProcess
  ├── 23. core/agent/factory.py — ProcessManager
  └── 24. 交互Agent 从内嵌迁到独立进程
```

---

## 十三、关键决策

| # | 决策 | 结论 |
|---|------|------|
| 1 | 默认 LLM | **DeepSeek v4**（`api.deepseek.com/v1`，OpenAI 兼容协议） |
| 2 | 第一个 Agent | **交互Agent**，系统开机自动启动，等待用户输入 |
| 3 | 用户添加 Agent 的入口 | 左侧资源空间 → Agent 空间 → **[+] 按钮** |
| 4 | Agent 编辑流程 | 主面板 Agent 编辑器视图：描述→MD预览→代码生成→沙箱→注册 |
| 5 | 消息总线 | 第一阶段 InProcess；生产 Redis Pub/Sub |
| 6 | 交互Agent 位置 | 第一阶段内嵌 FastAPI；后续独立进程 |
| 7 | MD → 代码 | 标准角色用模板，自定义用 DeepSeek v4 生成 |

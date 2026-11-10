# Agent 完整生命周期设计

> 从自然语言描述到独立进程运行 — Agent 的规格化、构造、注册、启动全流程。
>
> **LLM 多服务商可配置**：由 `.env` 的 `LLM_PROVIDER` / `LLM_API_KEY` 统一指定（OpenAI 兼容协议），DeepSeek 仅为默认预设之一，可切换 MiMo / OpenAI / Kimi / 智谱 / 通义 / 豆包 / 硅基流动 / MiniMax 或任意自建网关。

---

## 一、系统启动与默认 Agent

### 1.1 开机即启动：交互 Agent

SOTABand Engine 启动时，**交互 Agent（Interactive Agent）作为第一个 Agent 自动拉起**。它是系统的默认入口，无需用户手动创建；同时它也支持被用户手动关闭 / 重启（见 `agent_routes` 的 start/stop/restart）。

```
系统启动 (uvicorn app.main:app，端口由 .env 的 SOTABAND_API_HOST/PORT 配置，默认 127.0.0.1:8201)
    │
    ├── FastAPI lifespan 启动
    │   └── ensure_default_interactive_agent()（幂等）
    │       ├── AgentFactory.start("interactive-agent",
    │       │       impl="resources/agents/implementations/interactive_agent")
    │       ├── 拉起 Agent 子进程 (python -m core.agent.entrypoint)
    │       └── 子进程通过 stdin/stdout JSON-line 协议与主进程通信
    │
    └── 前端连接 → 对话视图 → 用户输入 → 交互Agent 处理
```

### 1.2 交互 Agent 的工作循环

```
用户在前端输入 "帮我分析这批EEG数据"
    │
    ▼
POST /api/chat/send  →  FastAPI 路由（SSE）
    │
    ▼
agent_factory.execute_stream("interactive-agent", params, context)
    │  向 Agent 子进程 stdin 写入一行 JSON 任务
    ▼
交互Agent 子进程内 InteractiveAgent.execute(ctx, **params)
    ├── 1. 构建 System Prompt（角色 + 可用工具列表 + 数据上下文）
    ├── 2. 调用配置的 LLM 服务商 API（流式，OpenAI 兼容协议）
    ├── 3. 逐 token 从 stdout 写回 content 事件
    ├── 4. 解析结构化卡片 → card 事件
    └── 5. done 事件
    │
    ▼
主进程逐行读 stdout → SSE 流 → 前端 fetch+getReader → store → 界面流式显示
```

对话可中断：`POST /api/chat/stop` 置停止标志，主进程在下一次 yield 时发出 `stopped` 事件并结束 SSE。对话链路不设短总超时（长耗时工具如模型训练可能数分钟无中间输出），由 `AGENT_ROUND_TIMEOUT`（默认 7200s）兜底硬超时、每 `AGENT_IDLE_TIMEOUT`（默认 300s）无输出做一次心跳探活，探活失败才判定卡死并强制重启。

---

## 二、Agent 生命周期总览

```
┌─ Phase 1: 规格化 ────────────────────────────────────────────┐
│  用户在 Agent 编辑器中用自然语言描述需求                          │
│       ↓ 调用 LLM 转换为标准 MD                                  │
│  MD 规范文档展示在编辑器中，用户可交互修改                        │
│       ↓ 确认保存                                              │
│  保存到 resources/agents/definitions/{agent-name}.md           │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 2: 构造 ──────────────────────────────────────────────┐
│  用户点击 [生成] → 核心层读取 MD 规范文档                        │
│       ↓                                                      │
│  固定头部模板 + 机械生成工具/API辅助方法 + LLM 生成 execute 体    │
│       ↓ 代码静态校验 (_check_agent_code) + 自动修正              │
│  沙箱结果展示 → 用户核验                                       │
│       ↓ 用户批准                                              │
│  保存到 resources/agents/implementations/{agent-name}/         │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 3: 注册 ──────────────────────────────────────────────┐
│  AgentRegistry 登记（自动）                                    │
│       ↓                                                      │
│  Agent 空间更新 → 前端资源浏览器可检索                           │
└──────────────────────────────────────────────────────────────┘
                              │
┌─ Phase 4: 运行 ──────────────────────────────────────────────┐
│  AgentFactory 启动独立子进程（注册后手动/自动启动）                │
│       ↓                                                      │
│  stdin/stdout JSON-line 协议接收任务、流式返回事件               │
│       ↓                                                      │
│  健康检查（status）+ 生命周期管理（start/stop/restart/自动重建）  │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、用户添加 Agent 的完整 UI 流程

### 3.1 入口：资源空间的 [+] 按钮

```
用户在左侧面板 → 资源空间标签 → Agent 空间 分类旁

    🤖 Agent 空间                    [+]
    ├── 交互Agent          ● healthy
    ├── 城市信息查询Agent   ● healthy
    └── ...

用户点击 [+] 按钮
    ↓
主面板切换到 → Agent 编辑器视图
```

### 3.2 Agent 编辑器视图

四步向导：**描述 → 审阅 → 核验 → 注册**（与工具编辑器同构，状态在 `agent-editor-store.ts`）。

- **Step 1 描述需求**：自然语言输入，选择角色类型（interactive / task / web-service，带模板确认对话框）
- **Step 2 审阅 MD 规范文档**：LLM 生成的 MD 全文可编辑，确认后进入代码生成
- **Step 3 沙箱测试 & 自动调试**：展示生成代码与静态校验结果列表（通过/失败），并提供按规范文档（task 输入规范表 / web-service ```apis 声明）解析出的测试参数表单，一键「自动调试」——后端沙箱试运行 Agent 子进程（单轮超时 `DEBUG_EXEC_TIMEOUT`，与工具统一默认 60 秒）→ LLM 分析错误重写代码 → 再跑（SSE 流式展示每轮结果，默认最多 10 轮，可经系统设置/`DEBUG_MAX_ROUNDS` 调整，上限 100）；可随时停止（前端 AbortController 断流 + `POST /api/agent/auto-debug/stop` 兜底）；`agent-detail` 视图另行提供「AI 辅助修改」通道
- **Step 4 注册 & 启动**：注册到 Agent 空间，可立即启动进程并做健康检查

### 3.3 Agent 编辑器状态机

```
[初始状态] 空白的 Step 1 输入框
    │  用户输入描述 + 选角色，点击 [生成 MD 文档]
    ▼
[MD 预览] Step 2 展示生成的 MD，可编辑
    │  点击 [→ 生成 Agent]
    ▼
[构造中] 代码生成 → 静态校验 → 沙箱测试
    ▼
[核验] Step 3 展示代码 + 测试结果
    ├── [修改代码] → 编辑 → 重新校验
    ├── [拒绝] → 丢弃代码，MD 文档保留在 Step 2
    │  [✅ 批准并注册上线]
    ▼
[完成] Step 4 显示注册 & 启动结果
```

### 3.4 涉及的前端文件

```
frontend/src/components/center-panel/AgentEditorView.tsx   # Agent 编辑器主视图
frontend/src/components/center-panel/AgentDetailView.tsx   # Agent 详情/运行/AI辅助修改
frontend/src/stores/agent-editor-store.ts                   # 编辑器状态管理
frontend/src/services/api/agent.ts                          # Agent CRUD API 调用 + SSE 流式消费（AbortSignal 可中止）
```

---

## 四、Phase 1 — Agent 规格化（MD 规范描述文档）

### 4.1 交互Agent 的 MD 规范文档（系统预置，开机即用）

预置规范文档位于 `resources/agents/definitions/interactive-agent.md`，实现代码位于 `resources/agents/implementations/interactive_agent/`（`agent.py` + `config.yaml`）。其要点：

- **角色**: interactive，是应用层与核心层之间的桥梁，前端唯一直接对话的 Agent
- **输入**: `content`（用户自然语言）、`attachments`（附件列表）、`workspace_tool_ids`（工作区工具范围）；执行上下文携带 `session_id` / `user_id`
- **输出事件**: `content`（文本增量，流式）、`card`（内联卡片）、`done`（响应结束）、`stopped`（被中断）、`error`；首轮结束后引擎侧另发 `title` 事件（LLM 总结的会话标题）
- **运行机制**: 构建 System Prompt（角色 + 可用工具列表 + 数据上下文）→ 调用 LLM 流式对话（携带平台工具 tools 声明，支持 function calling：tool_call 经总线交平台控制网关，≤5 轮回填后继续生成）→ 解析结构化卡片 → 逐事件返回。共享逻辑在 `core/agent/interactive_base.py` 中复用
- **LLM 配置**: 不写死服务商与端点，跟随全局 `.env` 配置（`LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL`），切换服务商无需改 Agent 代码

### 4.2 自然语言 → MD 规范文档（Agent 编辑器中触发）

```
用户在 Agent 编辑器的 Step 1 输入框中输入：

  "我需要一个能分析EEG数据、检测异常信号、生成报告的Agent"

点击 [生成 MD 文档]
    ↓
前端 POST /api/agent/generate-spec { "description": "我需要一个能...", "role": "task" }（SSE）
    ↓
FastAPI → 调用 LLM（规格化专用 Prompt，按所选角色附加针对性指引；
描述中的【【工具名】】/【API名】引用会先查出参数信息注入上下文）
    ↓
LLM 流式生成 MD 文档 → 逐 token 下发 reasoning（思考）/ delta（正文）事件，done 事件带回完整 MD
    ↓
前端 Agent 编辑器 Step 2 展示生成的 MD 文档，用户可编辑修改任何字段
    ↓
点击 [→ 生成 Agent] → 进入 Phase 2
```

**规格化 Prompt 的核心指令**：
- 根据用户描述与所选角色，填充标准模板的全部段落
- 如果用户未提及，合理推断输入/输出格式
- 建议合适的工具列表（从已有工具空间中匹配）

### 4.3 MD 规范文档格式（标准模板）

frontmatter（id / name / version / role / status）+ 正文段落：功能概述、角色定位、输入规范、输出规范、运行机制、工具使用、通信协议、配置参数、版本历史。

### 4.4 MD 文档存储

```
SB-USER/100/agents/definitions/            # 公共系统根（系统用户目录，内置资源经种子脚本导入）
├── interactive-agent.md                   # 系统预置，开机自启
└── ...

SB-USER/<uid>/agents/definitions/          # 私有根（用户新写/修改的规格写入此处，读取时私有优先合并）

resources/agents/definitions/              # 出厂种子包（随仓库分发，运行期不解析，仅供导入/哈希参照）
├── {agent-name}.md
└── ...
```

---

## 五、Phase 2 — Agent 构造（代码生成）

### 5.1 Agent 基类设计

所有 Agent 共享的抽象基类，位于 `core/agent/base.py`：

- `AgentRole` 枚举 — **三类角色体系**：
  - `INTERACTIVE` 交互式：与用户对话，理解意图并调用工具完成任务
  - `TASK` 任务式：按既定流程依次调用工具完成任务
  - `WEB_SERVICE` Web 服务式：以 HTTP API 形式对外提供服务，组合调用工具
  - 所有类型运行于独立进程，均支持手动启动 / 关闭
- `AgentContext` — 每次调用的执行上下文（agent_id / session_id / user_id / metadata）
- `AgentSpec` — 从 MD 规范文档解析出的结构化规格
- `BaseAgent` — 抽象基类，子类实现 `execute(ctx, **kwargs)` 异步生成器（yield 输出事件）及 `on_start` / `on_stop` / `health_check`

`core/agent/interactive_base.py` 在 `BaseAgent` 之上为交互式 Agent 提供共享实现（System Prompt 组装、LLM 流式对话、卡片解析）。

### 5.2 Agent 代码生成器

位于 `core/resource/builder/agent_builder.py`，`build()` 的组装策略是「模板保证正确性 + LLM 只写核心逻辑」：

1. **头部**：`HEADER_TEMPLATE` 固定模板，保证 import 与类骨架始终合法
2. **辅助方法**：从 MD 中提取 `【【工具名】】` / `【API名】` 引用，机械生成 `_call_tool_{id}` / `_call_api_{id}` 方法（子进程调用，不经 LLM，不会幻觉）
3. **execute() 体**：唯一由 LLM 生成的部分，按角色（interactive/task/web-service）注入差异化 Prompt
4. 后处理 `_fix_common_errors` 正则修正常见错误；`AgentFactory._check_agent_code` 静态校验，`_auto_fix_code` 启动前再兜底

`core/agent/templates/` 下的 `interactive.py.tmpl` / `task.py.tmpl` / `web_service.py.tmpl` 是基于 `spec.flow` / `spec.apis` 的**纯模板生成方案**，当前 `agent_builder` 未接入（保留为备选/参考实现）；实际生成一律走上述「LLM 生成 execute 体」路径。

**生成物目录：**

```
SB-USER/<uid>/agents/implementations/{agent-id}/   （用户生成的写入私有根；公共 Agent 在系统根 SB-USER/100/ 下同构）
├── __init__.py
├── agent.py         # 继承 BaseAgent
└── config.yaml      # 运行时配置
```

### 5.3 沙箱测试与用户核验

```
用户点击 [→ 生成 Agent]
    ↓
前端 POST /api/agent/generate-code（SSE）→ AgentCodeBuilder.build() 生成 agent.py，
execute 体逐 token 流式展示（reasoning / delta 事件）
    ↓
静态校验（语法 / BaseAgent 子类存在 / 抽象方法实现）→ 常见错误自动修正 → 沙箱试运行（dry_run）
    ↓
结果展示在 Agent 编辑器 Step 3:
  ├── 左侧: 代码预览（可手工微调）
  ├── 右侧: 校验/测试结果清单
  └── 按钮: [修改代码] [拒绝] [✅ 批准并注册上线]
```

---

## 六、Phase 3 — Agent 注册

```
用户点击 [✅ 批准并注册上线]
    ↓
POST /api/agent/register（agent_routes）
    ├── 1. Agent ID 取自 MD frontmatter 的 id 字段（缺省 custom-agent），
    │      经 RESOURCE_ID_RE 校验拒绝路径穿越；代码先过静态安全审查；
    │      id 已命中公共层时非系统身份被拦截（防影子遮蔽，改公共 Agent 先 fork）
    ├── 2. AgentRegistry.register 写入对应根 registry.json
    │      （含从【【工具名】】引用解析出的 required_tools）
    ├── 3. 路由写入实现目录（agent.py / spec.md / config.yaml，与条目同根）
    └── 4. Web 服务式 Agent 注册后自动生成对外 API 文档
         （api_doc_builder，见 /api/agent/{id}/api-doc）
    ↓
Agent 空间自动刷新 → 前端左侧面板可看到新 Agent
```

注册采用「读合并 / 按身份写」模型：读取时按私有根→系统根顺序合并，写入根由创建者身份决定——系统身份（act-as 令牌）注册的 Agent `visibility=public` 落系统用户目录（全员可用），其他用户注册的 `visibility=private` 落私有根（仅自己可用）；visibility 不接受客户端传值。例外是 `save-spec` / `save-code`：它们优先写回文件的原位置（公共层资产就地更新，须系统身份），原位置不存在时才写私有根；命中公共层时先过 `ensure_can_touch_builtin` 闸门，非系统身份 403。注册与 save-code 的代码先过 `core/security/code_scan.py` 静态安全审查（与工具同一闸门，todo 3.2），命中阻断项返回 400、不落盘。

---

## 七、Phase 4 — Agent 运行（独立进程）

### 7.1 进程架构

```
┌─ SOTABand API Server (主进程) ─────────────────────────────────┐
│  FastAPI (HTTP + SSE)                                          │
│  AgentFactory（子进程生命周期：start/stop/restart/status/重建）   │
│  WebServiceHost（Web 服务式 Agent 的 HTTP 宿主）                 │
└────────────────────────────────────────────────────────────────┘
          │ stdin (JSON-line 任务)          ▲ stdout (JSON-line 事件)
┌─────────┴──────────┐          ┌──────────┴───────┐
│ Agent 子进程        │          │ Agent 子进程      │
│ interactive-agent  │          │ {user-agent}     │
│ 开机自启（可手动关闭）│          │ 手动启动          │
└────────────────────┘          └──────────────────┘
```

**所有 Agent（包括交互 Agent）都运行在独立子进程中**，统一通过 `core/agent/entrypoint.py` 加载：`python -m core.agent.entrypoint --agent-id xxx --impl-path <实现目录>`。主进程与子进程之间以 stdin/stdout 的 JSON-line 协议通信——主进程向 stdin 写一行任务 JSON，子进程按行向 stdout 写事件 JSON。主进程为每个子进程维护一个后台 reader 逐行读取：普通事件进入该 Agent 的输出队列（`execute_stream` 从队列取事件转发 SSE，保证低延迟流式），通信帧（见下）交给工厂的总线路由器。

`core/agent/bus.py` + `core/agent/protocol.py` 提供 Agent 间通信（本地）：主进程是唯一的路由中枢，跨 Agent 消息一律经「子进程 stdout → 主进程路由 → 目标子进程 stdin」转发。Agent 代码经 `self._bus` 使用两种模式——Pub/Sub（`subscribe`/`publish`，尽力而为投递，订阅随进程退出失效）与 RPC（`await self._bus.call_agent(target, method, params)`，被调方以 execute 语义执行、事件流聚合为一次性响应；目标未运行/超时/崩溃一律回结构化错误，不抛异常）。v1 通信帧 `{"v":1,"dir":"o|i","type":...}` 与旧协议行（无 `v` 字段）共存，旧调用方不受影响。外部系统可经 `POST /api/agent/bus/publish|call`、`GET /api/agent/bus/status` 接入总线。Web 服务式 Agent 额外由 `web_server.py` 宿主暴露 HTTP API（`/api/agent/{id}/service` 查询服务地址）。

### 7.2 超时与中断语义

- `/api/chat/send` 的对话流 `timeout=None`：不设总超时，由用户通过停止按钮（`/api/chat/stop`）主动中断
- `AgentFactory.execute_stream` 默认总超时 120s，此时空闲容忍取 `max(60s, timeout)`；对话链路显式传 `timeout=None`，改由 `AGENT_ROUND_TIMEOUT`（默认 7200s）兜底硬超时、每 `AGENT_IDLE_TIMEOUT`（默认 300s）无输出做一次心跳探活，探活失败才判定卡死并强制重启（长耗时工具不会被误杀）
- 自动调试的单轮沙箱试运行超时与最大轮数为运行参数：`DEBUG_EXEC_TIMEOUT`（工具与 Agent 统一默认 60 秒）、`DEBUG_MAX_ROUNDS`（默认 10，上限 100），可在系统设置页调整并写入 `.env` 热生效
- 主进程退出（lifespan shutdown）时遍历停止全部 Agent 子进程

### 7.3 自动重建

Agent 启动时若发现实现代码缺失或校验失败，`AgentFactory._try_rebuild` 会尝试从 MD 规格重新生成代码后再启动。

---

## 八、LLM 配置（多服务商）

### 8.1 `config/settings.py` 中的 LLM 配置

`LLMConfig` 从 `.env` 加载，解析优先级（高→低）：

```
api_key : 代码赋值 > LLM_API_KEY（仅默认 provider）> <PROVIDER>_API_KEY > 空
base_url: 代码赋值 > LLM_BASE_URL（仅默认 provider）> <PROVIDER>_BASE_URL > 按 Key 前缀自动选择的端点变体 > 服务商预设
model   : 代码赋值 > LLM_MODEL（仅默认 provider）> <PROVIDER>_MODEL > 服务商预设默认值
```

- 主 key：`LLM_PROVIDER` 指向的厂商（填 `LLM_API_KEY`），Agent 对话与普通工具的默认选择
- 副 key：`<PROVIDER>_API_KEY` 形式存放的其他厂商 key，工具经系统 API `api-llm-chat-with-config` 只传 provider 即可自动取用
- 端点按 Key 前缀自动选择（如 MiMo 的 `sk-` → 按量付费端点、`tp-` → Token Plan 端点）
- **生成模型分离**：代码/规格生成链路（工具与 Agent 的 generate-spec/code、自动调试）可指定独立模型——`resolve_gen_llm_config()` 按 `LLM_GEN_PROVIDER / LLM_GEN_MODEL / LLM_GEN_API_KEY / LLM_GEN_BASE_URL` 解析，`LLM_GEN_PROVIDER` 留空则跟随全局对话模型；各变量留空时逐级回退（`<PROVIDER>_MODEL`、同厂商已有 Key、Key 前缀匹配端点）。系统设置页提供该组配置的可视化编辑

### 8.2 LLM 客户端

`core/llm/client.py` 提供 OpenAI 兼容协议的统一客户端（`/v1/chat/completions`，支持流式）；`core/llm/providers.py` 维护各服务商预设（端点、默认模型、端点变体）。所有服务商——包括 DeepSeek——走同一套协议与代码路径，不存在单一厂商专属客户端。

---

## 九、API 端点总览

| 端点 | 方法 | 说明 | 调用方 |
|------|------|------|--------|
| `/api/chat/send` | POST (SSE) | 与交互Agent 对话 | 前端对话视图 |
| `/api/chat/stop` | POST | 停止指定 session 的对话 | 前端对话视图 |
| `/api/chat/sessions` `/sessions/{id}` | GET / DELETE | 会话列表 / 会话消息 / 删除会话 | 前端对话视图 |
| `/api/chat/platform-call` `/confirm` `/cancel` `/pending` | POST / GET | 对话内平台控制网关（执行/确认/取消/恢复待确认） | 前端对话视图 |
| `/api/chat/platform-ops` | GET | 当前用户的平台操作审计流水 | 前端 |
| `/api/agent/generate-spec` | POST (SSE) | 自然语言 → MD 规范文档（流式） | 前端 Agent 编辑器 (Step 1→2) |
| `/api/agent/generate-code` | POST (SSE) | MD 规范文档 → Python 代码 + 校验（流式） | 前端 Agent 编辑器 (Step 2→3) |
| `/api/agent/auto-debug` | POST (SSE) | 自动调试循环（沙箱试运行 → LLM 重写 → 再跑） | 前端 Agent 编辑器 (Step 3) |
| `/api/agent/auto-debug/stop` | POST | 停止指定 Agent 的自动调试 | 前端 Agent 编辑器 (Step 3) |
| `/api/agent/bus/publish` `/call` | POST | 外部系统接入 Agent 间通信总线 | 外部集成 |
| `/api/agent/bus/status` | GET | 总线状态（频道 / 订阅 / RPC） | 外部集成 |
| `/api/agent/register` | POST | 注册 Agent（落盘 + 写 registry） | 前端 Agent 编辑器 (Step 3→4) |
| `/api/agent/list` | GET | 列出所有 Agent | 前端资源空间 |
| `/api/agent/{id}` | GET | 单个 Agent 详情（spec/code/config） | 前端详情页 |
| `/api/agent/{id}/save-spec` | POST | 保存规格修改 | 前端编辑器 |
| `/api/agent/{id}/save-code` | POST | 保存代码修改 | 前端编辑器 |
| `/api/agent/{id}/sync-spec-from-code` | POST | 从代码反向同步规格 | 前端编辑器 |
| `/api/agent/{id}/fork-status` | GET | fork 状态（是否有副本、上游是否有更新） | 前端详情页 |
| `/api/agent/{id}/fork` `/fork` (DELETE) | POST / DELETE | 复制为我的副本 / 丢弃副本 | 前端详情页 |
| `/api/agent/{id}/export` | GET | 导出 Agent 迁移包（zip 直链下载） | 前端详情页 |
| `/api/agent/{id}/status` | GET | Agent 进程状态查询 | 前端/监控 |
| `/api/agent/{id}/start` `/stop` `/restart` | POST | 进程启停 | 前端资源空间 |
| `/api/agent/{id}/execute` | POST (SSE) | 手动运行 Agent（任务式） | 前端详情页 |
| `/api/agent/{id}/service` | GET | Web 服务式 Agent 的服务地址 | 前端详情页 |
| `/api/agent/{id}/test-api` | POST | 测试 Web 服务式 Agent 的 API | 前端详情页 |
| `/api/agent/{id}/api-doc` | GET | 获取自动生成的对外 API 文档 | 前端/外部集成 |
| `/api/agent/{id}/api-doc/download` | GET | 下载 API 文档 | 前端/外部集成 |
| `/api/agent/{id}` | DELETE | 删除 Agent | 前端资源空间 |

资源查询没有统一的 `/api/resource/*` 通用接口；数据、工具、模型、Agent 分别由 `/api/data/*`、`/api/tool/*`、`/api/model/*`、`/api/agent/*` 路由提供。

---

## 十、资源管理子系统架构（可扩展设计）

### 10.1 设计原则

所有资源类型（Agent、工具、模型、数据）都需要**注册**、**发现**能力，部分需要**构建**能力。为避免未来扩展时文件膨胀，采用**按功能分层、每层按资源类型分文件**的目录结构：

```
core/resource/
├── paths.py                       # 双层资源根模型（私有根 + 公共系统根；resources/ 为出厂种子包）
├── spec_outline.py                # MD 规格文档大纲解析
│
├── registry/                      # 注册中心层
│   ├── registry_base.py           #   BaseRegistry — 所有注册器的抽象基类（读合并/按身份写）
│   ├── agent_registry.py          #   AgentRegistry
│   ├── data_registry.py           #   DataRegistry（按 owner 隔离）
│   ├── tool_registry.py           #   ToolRegistry
│   └── model_registry.py          #   ModelRegistry
│
├── discoverer/                    # 发现器层
│   ├── discoverer_base.py         #   BaseDiscoverer — 所有发现器的抽象基类
│   ├── data_discoverer.py         #   DataDiscoverer
│   └── tool_discoverer.py         #   ToolDiscoverer
│
└── builder/                       # 构建器层
    ├── builder_base.py            #   BaseBuilder — LLM 调用、代码提取等公共能力
    ├── agent_builder.py           #   Agent 代码生成 (MD → Python)
    ├── tool_builder.py            #   ToolCodeBuilder (需求 → 工具代码 + 自动调试)
    └── api_doc_builder.py         #   Web 服务式 Agent 的对外 API 文档生成
```

任务注册器（`task_registry`）、Agent/模型发现器等属于该框架下尚未补齐的插槽。

### 10.2 基类设计

`BaseRegistry` / `BaseDiscoverer` / `BaseBuilder` 分别抽象注册、发现、构建三类能力，公共存储与 LLM 交互逻辑收口在基类。

### 10.3 扩展新资源类型

当需要支持新的资源类型（如 `task`）时：

1. 在 `registry/` 下新建 `task_registry.py`，实现 `BaseRegistry`
2. （可选）在 `discoverer/`、`builder/` 下按同规则新增
3. 注册与实现文件遵循「读合并 / 按身份写」模型，无需改动已有文件

---

## 十一、关键文件清单

```
# Agent 运行时框架
core/agent/base.py                  # BaseAgent + AgentSpec + AgentRole(三类) + MD 解析
core/agent/bus.py                   # 消息总线（子进程侧 AgentBusClient + InProcess 实现）
core/agent/protocol.py              # Agent 间通信帧协议（v1 帧定义）
core/agent/factory.py               # AgentFactory — 子进程生命周期 + 主进程总线路由
core/agent/entrypoint.py            # 子进程入口（stdin/stdout JSON-line 协议）
core/agent/interactive_base.py      # 交互式 Agent 共享基类
core/agent/web_server.py            # Web 服务式 Agent 的 HTTP 宿主
core/agent/tool_runtime.py          # Agent 级工具常驻 Worker
core/agent/templates/               # interactive / task / web_service 三个角色模板

# LLM 客户端
core/llm/client.py                  # OpenAI 兼容统一客户端
core/llm/providers.py               # 多服务商预设与端点变体

# 资源管理
core/resource/registry/agent_registry.py     # Agent 注册
core/resource/builder/agent_builder.py       # Agent 代码生成
core/resource/builder/api_doc_builder.py     # Web 服务 Agent API 文档生成

# API 层
app/main.py                         # FastAPI 入口 + lifespan 拉起默认交互 Agent
app/api/routes/chat_routes.py       # /api/chat/send (SSE)、/api/chat/stop
app/api/routes/agent_routes.py      # /api/agent/* (Agent 管理)

# Agent 资产
resources/agents/definitions/       # MD 规范文档出厂种子（运行期不解析；公共层与私有根同构）
resources/agents/implementations/   # agent.py + config.yaml 出厂种子（公共层与私有根同构）
```

---

## 十二、关键决策

| # | 决策 | 结论 |
|---|------|------|
| 1 | 默认 LLM | **多服务商可配置**（`.env` 的 `LLM_PROVIDER`/`LLM_API_KEY`，OpenAI 兼容协议；DeepSeek 为默认预设） |
| 2 | 第一个 Agent | **交互Agent**，引擎启动自动拉起，同样支持手动关闭/重启 |
| 3 | 用户添加 Agent 的入口 | 左侧资源空间 → Agent 空间 → **[+] 按钮** |
| 4 | Agent 编辑流程 | 四步向导：描述→MD审阅→沙箱核验→注册 |
| 5 | 角色体系 | 三类：interactive / task / web_service（早期四类设计中的 orchestrator/observer 已收敛为角色类型，不再单设；内置 `orchestrator-agent` 只是承担编排职责的 web_service 角色普通实例，不是第四种角色） |
| 6 | 进程模型 | **所有 Agent 统一独立子进程**，stdin/stdout JSON-line 协议，交互 Agent 不再内嵌主进程 |
| 7 | MD → 代码 | 固定模板 + 机械辅助方法 + LLM 生成 execute 体（按角色差异化 Prompt），静态校验 + 自动修正 |
| 8 | 资源存储 | 双层根模型：私有根 `<USER_HOME>/<uid>/` + 公共系统根 `<USER_HOME>/100/`；读合并（私有优先）、写按身份；`resources/` 为出厂种子包（随仓库，运行期不解析） |

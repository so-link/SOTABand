# Agent 类型体系设计

> 将 Agent 从四类（interactive / task / orchestrator / observer）收敛为**三类**：
> **交互式（interactive）**、**任务式（task）**、**Web 服务式（web-service）**。
> 三类 Agent 的执行界面各不相同。
>
> 本文档为**设计说明**，不含实现代码。

---

## 一、设计目标

### 1.1 从四类收敛为三类

| 原类型 | 处理 | 理由 |
|--------|------|------|
| `interactive` 交互型 | ✅ 保留 | 系统默认入口，理解自然语言、匹配并调用工具 |
| `task` 任务型 | ✅ 保留（重新定义） | 按既定流程编排工具，一键执行 |
| `orchestrator` 编排型 | ❌ 移除 | 职责并入 task（流程编排即任务式的一种） |
| `observer` 观测型 | ❌ 移除 | 职责并入 Web 服务式（对外提供监控数据） |
| **`web-service` Web 服务式** | ➕ 新增 | 以 HTTP API 形式对外提供服务，组合调用工具 |

### 1.2 三类 Agent 的核心差异

| 维度 | 交互式 | 任务式 | Web 服务式 |
|------|--------|--------|-----------|
| **触发来源** | 用户对话输入 | 用户点击「启动」 | 外部 HTTP 请求 |
| **交互形态** | 多轮对话 | 单次执行（可重复） | 请求—响应（无状态/短连接） |
| **流程确定性** | LLM 动态决策 | 既定流程（确定） | 按 API 路由既定组合 |
| **进程模型** | **独立进程** | **独立进程** | **独立进程** |
| **生命周期** | 手动启停（系统默认交互 Agent 随引擎自启） | 手动启停 | 手动启停 |
| **对外暴露** | 无 | 无 | **HTTP API（可多个）** |
| **执行界面** | 启停按钮 + **对话视图** | 启停按钮 + 消息区 | 启停按钮 + 消息区 + **API 测试区** |

### 1.3 统一的生命周期模型（重要）

**所有类型的 Agent 统一遵循以下规则：**

1. **运行时一律是独立进程**：每个 Agent（无论类型）在运行时都拥有自己的独立进程，进程间互不影响、互不阻塞。
2. **一律支持手动启动 / 关闭**：所有 Agent 都提供启动与关闭按钮，用户可随时启停。
3. **系统默认交互 Agent 的例外**：仅「系统默认的交互 Agent」随引擎自动启动（作为系统默认入口），但它**同样可以被手动关闭 / 重启**；用户自定义新增的交互式 Agent 则完全手动启停。
4. **进程隔离收益**：单个 Agent 崩溃 / 阻塞不影响引擎与其他 Agent；便于按 Agent 维度限流、监控与资源隔离。

> 设计演进说明：当前实现中，交互 Agent 是**与 FastAPI 同进程的模块级单例**（`chat_routes.py`）。
> 本设计将统一改为**独立进程模型**，与任务式 / Web 服务式一致。

---

## 二、AgentRole 枚举调整

### 2.1 新版定义

```python
class AgentRole(Enum):
    INTERACTIVE  = "interactive"    # 交互式 — 对话式完成任务
    TASK         = "task"           # 任务式 — 按既定流程执行工具
    WEB_SERVICE  = "web-service"    # Web 服务式 — 以 HTTP API 对外提供服务
```

**改动点**：
- 删除 `ORCHESTRATOR`、`OBSERVER`
- 新增 `WEB_SERVICE = "web-service"`
- 默认值仍为 `TASK`

### 2.2 向后兼容

现有注册表中 `role: "orchestrator"` / `"observer"` 的记录（当前无），迁移规则：

| 旧值 | 新值 |
|------|------|
| `orchestrator` | `task` |
| `observer` | `web-service` |

`core/agent/base.py` 的 `AgentSpec.role` 解析需容错：遇到未知 role 时回退为 `TASK` 并记录告警。

---

## 三、三类 Agent 的规格（MD 规范文档）

### 3.1 交互式 Agent（interactive）

与现状一致，无需改动。系统核心的交互 Agent 即此类型。

**MD 规范要点**：
```yaml
role: interactive
inputs: { content: {type: string, required: true}, attachments: ... }
tools: 可选工具列表（LLM 动态匹配）
```

**行为**：接收用户输入 → 构建 System Prompt（角色 + 可用工具 + 上下文）→ LLM 流式生成 → 工具匹配/参数收集 → 调用工具 → 返回结果。

### 3.2 任务式 Agent（task）

**定义**：按照**既定流程**依次调用工具完成任务。流程在 MD 规范中明确声明，执行时不依赖 LLM 动态决策。

**MD 规范要点**：
```yaml
role: task
inputs: 启动参数（可含默认值）
flow:
  - step: 1
    tool: <tool-id>
    params: { ... }          # 可用 {input.xxx} 引用启动参数
    output_bind: step1_out   # 供后续步骤引用
  - step: 2
    tool: <tool-id>
    params: { data: "{step1_out.model_path}" }
outputs: 最终输出字段
```

**行为**：
1. 用户点击「启动」→ 提交启动参数
2. 按 `flow` 顺序执行：每步调用 `ToolExecutor.execute(tool_id, params)`
3. 上一步的输出可被后续步骤引用（`output_bind` 机制）
4. 结果实时推送到「消息显示界面」
5. 支持「关闭」中止执行

**与交互式的关键区别**：**流程由 MD 静态定义**，不由 LLM 临时决定；执行是**确定性**的。

### 3.3 Web 服务式 Agent（web-service）

**定义**：以 HTTP 服务形式对外提供**多个 API 接口**，每个接口对应一组工具调用组合。

**MD 规范要点**：
```yaml
role: web-service
server:
  host: 0.0.0.0
  port: 9200              # 每个 Agent 分配独立端口（或统一网关）
apis:
  - name: detect
    method: POST
    path: /detect
    description: 目标检测
    params:
      - { name: image, type: string, required: true, desc: 图片路径 }
    flow:                 # 该 API 内部的工具组合
      - tool: <tool-id>
        params: { file: "{param.image}" }
    response:             # 响应字段映射
      - { name: objects, from: "{step1_out.detections}" }
  - name: train
    method: POST
    path: /train
    params: [ ... ]
    flow: [ ... ]
```

**行为**：
1. 用户点击「启动」→ Agent 启动一个 HTTP 服务进程，监听端口
2. 外部请求 `POST /detect` → 匹配对应 API → 按 `flow` 组合调用工具 → 返回 JSON
3. 「关闭」→ 停止 HTTP 服务
4. **执行界面**额外提供「API 测试区」：列出所有 API，用户选 API + 填参数 → 点击测试 → 显示返回结果

**常驻性**：与任务式的本质区别在于 —— Web 服务式 Agent 启动后**持续监听**，等待外部请求。

---

## 四、三类 Agent 的执行界面设计

### 4.1 界面总览

| Agent 类型 | 界面组件 | 布局 |
|-----------|---------|------|
| 交互式 | `InteractiveAgentView`（新增，含对话区） | **启停按钮** + 对话视图 |
| 任务式 | `TaskAgentView`（新增） | 启停按钮 + 消息显示区 |
| Web 服务式 | `WebServiceAgentView`（新增） | 启停按钮 + 消息显示区 + API 测试区 |

> **共性**：所有类型的界面**顶部都有「启动 / 关闭」按钮与运行状态指示**，因为所有 Agent 都支持手动启停且运行时为独立进程。

### 4.2 交互式 Agent 界面

```
┌──────────────────────────────────────────────────────────┐
│  💬 交互式 Agent：<名称>          ● 运行中 (PID 1042)     │
├──────────────────────────────────────────────────────────┤
│  [ ▶ 启动 ]   [ ■ 关闭 ]                                  │
├──────────────────────────────────────────────────────────┤
│                    对话区（复用 ChatView 主体）            │
│  ┌────────────────────────────────────────────────────┐  │
│  │ 用户: 帮我分析这批数据                              │  │
│  │ Agent: 好的，我将调用 xxx 工具 ...                  │  │
│  │ 用户: ...                                          │  │
│  └────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────┐  │
│  │ [输入框________________________________] [发送]     │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**组件**：`InteractiveAgentView.tsx`（新增）
- **启动/关闭按钮**：控制该 Agent 的独立进程
- **对话区**：复用现有 `ChatView` 的消息流 / 输入框 / 附件能力

**两类交互式 Agent 的差异**：

| 场景 | 启动行为 |
|------|---------|
| **系统默认交互 Agent** | 随引擎**自动启动**；顶部仍显示启停按钮，可手动关闭 / 重启 |
| **用户自定义交互 Agent** | 完全**手动**启动；未启动时对话区禁用并提示「请先启动 Agent」 |

> 主界面默认对话（`activeView === 'chat'`）即系统默认交互 Agent 的界面，行为与现状保持一致，仅额外增加顶部的启停控制。

### 4.3 任务式 Agent 界面（新增）

```
┌──────────────────────────────────────────────────────────┐
│  ⚙ 任务式 Agent：<名称>                    ● 已停止       │
├──────────────────────────────────────────────────────────┤
│  [ ▶ 启动 ]   [ ■ 关闭 ]                                  │
├──────────────────────────────────────────────────────────┤
│  启动参数（可选）                                          │
│    参数1: [__________]                                    │
│    参数2: [__________]                                    │
├──────────────────────────────────────────────────────────┤
│  消息显示                                                  │
│  ┌────────────────────────────────────────────────────┐  │
│  │ [12:00:01] 任务已启动                               │  │
│  │ [12:00:02] 步骤 1/3：调用工具 xxx ...               │  │
│  │ [12:00:05] 步骤 1 完成，输出：{...}                 │  │
│  │ [12:00:06] 步骤 2/3：调用工具 yyy ...               │  │
│  │ ...                                                │  │
│  │ [12:01:10] ✅ 任务完成                              │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**组件**：`TaskAgentView.tsx`
- **启动按钮**：提交启动参数 → `POST /api/agent/{id}/start-task`
- **关闭按钮**：`POST /api/agent/{id}/stop-task`
- **消息显示区**：SSE 流式接收执行日志（步骤进度、工具输出、错误）
- **无对话输入框**（与交互式的核心区别）

### 4.4 Web 服务式 Agent 界面（新增）

```
┌──────────────────────────────────────────────────────────┐
│  🌐 Web 服务 Agent：<名称>              ● 运行中 :9200    │
├──────────────────────────────────────────────────────────┤
│  [ ▶ 启动 ]   [ ■ 关闭 ]                                  │
├──────────────────────────────────────────────────────────┤
│  消息显示                                                  │
│  ┌────────────────────────────────────────────────────┐  │
│  │ [12:00:01] 服务已启动，监听 0.0.0.0:9200            │  │
│  │ [12:00:30] POST /detect 来自 10.0.0.1               │  │
│  │ [12:00:31] 调用工具 xxx → 成功                      │  │
│  │ [12:00:31] 响应 200 (12ms)                          │  │
│  └────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────┤
│  API 测试                                                  │
│    选择 API: [ /detect        ▼ ]                         │
│    参数:                                                   │
│      image: [________________]                            │
│      score_thr: [______________]                          │
│    [ 发送测试请求 ]                                        │
│  ┌─ 测试结果 ─────────────────────────────────────────┐  │
│  │ { "status": "success", "objects": [...] }          │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**组件**：`WebServiceAgentView.tsx`
- **启动/关闭按钮**：控制 HTTP 服务进程
- **消息显示区**：请求日志（谁来了、调了什么工具、响应耗时）
- **API 测试区**：
  - 下拉选择该 Agent 暴露的 API（从 MD 规范解析）
  - 根据 API 的 `params` 动态渲染输入表单
  - 「发送测试请求」→ 由后端**内部代理**调用该 HTTP 服务（避免 CORS）→ 展示 JSON 结果

---

## 五、Agent 创建流程（类型优先 + 需求模板）

### 5.1 现状

现有创建流程（`AgentEditorView.tsx`）为四步：

```
Step 1 描述需求  →  Step 2 审阅 MD  →  Step 3 代码核验  →  Step 4 完成
```

**Step 1 是一个空白的需求文本框**，用户直接自由填写。没有「类型」概念，所有 Agent 走同一套生成逻辑。

### 5.2 改进后的流程

**在描述需求之前，先让用户选择 Agent 类型**；选定类型后，需求框**自动填入该类型的默认模板**作为起点。

```
┌─ Step 1a: 选择 Agent 类型 ────────────────────────────────┐
│   请选择要创建的 Agent 类型：                              │
│   ○ 交互式（interactive）— 通过对话完成任务                │
│   ○ 任务式（task）        — 按既定流程执行工具             │
│   ○ Web 服务式（web-service）— 以 HTTP API 对外提供服务    │
└───────────────────────────────────────────────────────────┘
                        │ 选择后
                        ▼
┌─ Step 1b: 描述需求（带类型默认模板）──────────────────────┐
│   已选择类型：任务式（task）              [ 更改类型 ]      │
│   ┌─────────────────────────────────────────────────────┐ │
│   │ <该类型的默认需求模板，用户在此基础上编辑>            │ │
│   └─────────────────────────────────────────────────────┘ │
│   提示：@ 引用 API，$ 引用工具                              │
│                                          [ 生成 MD 文档 ]  │
└───────────────────────────────────────────────────────────┘
```

### 5.3 三类默认需求模板

选择类型后，需求框预填对应模板（用户可自由修改）：

**① 交互式（interactive）**
```
我需要一个交互式 Agent，用于【描述你的业务场景】。

- 功能：接收用户输入，理解用户意图，并调用合适的工具完成任务。
- 可用工具：$工具名（按需引用）
- 交互方式：与用户多轮对话，逐步澄清需求后执行。
- 输出：以自然语言回复，并展示工具执行结果。
```

**② 任务式（task）**
```
我需要一个任务式 Agent，用于【描述你的业务流程】。

- 功能：按既定流程依次调用工具完成任务。
- 触发方式：用户点击「启动」后执行。
- 执行流程：
    步骤 1：调用 $工具名1，参数 ...
    步骤 2：调用 $工具名2，使用步骤 1 的输出 ...
    步骤 3：...
- 启动参数：【列出启动时需要的参数】
- 输出：最终结果输出到消息界面。
```

**③ Web 服务式（web-service）**
```
我需要一个 Web 服务式 Agent，用于【描述你的服务场景】。

- 功能：以 HTTP API 形式对外提供服务。
- 提供服务（可多个）：
    API 1：POST /xxx
        参数：{ 参数名: 类型 }
        流程：调用 $工具名 ...
        返回：{ 返回字段 }
    API 2：...
- 监听端口：自动分配
- 输出：JSON 响应。
```

### 5.4 模板的作用

| 方面 | 说明 |
|------|------|
| **降低门槛** | 用户不必从零构思，按模板填空即可 |
| **结构对齐** | 模板天然对应三类规格（`flow` / `apis`），减少 LLM 生成的偏差 |
| **类型约束** | `generate-spec` 接口接收「类型」，把类型约束注入 LLM 提示词，确保生成的 MD 与所选类型匹配 |
| **可编辑** | 模板只是**起点**，用户可任意增删改 |

### 5.5 接口调整

`POST /api/agent/generate-spec` 请求体新增 `role` 字段：

```json
{
  "description": "（已含模板内容的用户描述）",
  "role": "task"        // 新增：interactive | task | web-service
}
```

后端在 `SPEC_GENERATION_PROMPT` 中按 `role` 注入**类型专属的生成指引**（例如 task 必须生成 `flow` 结构、web-service 必须生成 `apis` 结构）。

### 5.6 前端改动点

| 文件 | 改动 |
|------|------|
| `components/center-panel/AgentEditorView.tsx` | Step1 拆为「类型选择（1a）+ 需求描述（1b）」；新增类型单选组与「更改类型」按钮 |
| `stores/agent-editor-store.ts` | 新增 `agentRole` 状态 + `setAgentRole`；提供三类默认模板常量 |
| `services/api/agent.ts` | `generateSpec` 调用增加 `role` 参数 |

> 交互细节：切换 / 更改类型时，若需求框为空则填入新类型模板；若已有用户内容，则**询问是否覆盖**（避免丢失已写内容）。

---

## 六、前后端改动点

### 5.1 后端

| 文件 | 改动 |
|------|------|
| `core/agent/base.py` | `AgentRole` 收敛为三类；`AgentSpec` 增加 `flow`（task）/ `server`+`apis`（web-service）字段 |
| `core/agent/factory.py` | `start/stop` 按 role 统一为独立进程模型；启动/停止/状态查询对三类一致；web-service 额外申请端口 |
| `core/agent/entrypoint.py` | 统一子进程入口：按 role 分派（interactive → 对话循环；task → flow 执行；web-service → HTTP 服务宿主） |
| `app/api/routes/chat_routes.py` | **改造**：交互 Agent 由「同进程单例」改为「经 AgentFactory 的子进程」；对话请求转发到子进程 |
| `app/main.py` | 启动时仅自动启动「系统默认交互 Agent」；其余不自动启动 |
| `core/resource/builder/agent_builder.py` | 代码生成模板按三类区分（对话循环 / flow 执行器 / HTTP 服务框架） |
| `app/api/routes/agent_routes.py` | 新增：`POST /{id}/start-task`、`POST /{id}/stop-task`、`GET /{id}/apis`、`POST /{id}/test-api` |
| `core/agent/templates/` | 三类 Agent 模板更新 |

### 5.2 前端

| 文件 | 改动 |
|------|------|
| `stores/ui-store.ts` | `ActiveView` 新增 `'interactive-agent'`、`'task-agent'`、`'web-service-agent'` |
| `components/center-panel/CenterPanel.tsx` | switch 增加三个 case |
| `components/center-panel/InteractiveAgentView.tsx` | **新增**（启停按钮 + 复用对话区） |
| `components/center-panel/TaskAgentView.tsx` | **新增**（启停 + 消息区） |
| `components/center-panel/WebServiceAgentView.tsx` | **新增**（启停 + 消息区 + API 测试区） |
| `components/center-panel/ChatView.tsx` | 可提取出可复用的「对话区」子组件，供默认对话与 `InteractiveAgentView` 共用 |
| `stores/agent-runtime-store.ts`（新建） | 统一管理所有 Agent 的运行状态（running/stopped/pid）与消息流 |
| `services/api/agent.ts` | 新增 start / stop / status / apis / test-api 调用 |

---

## 七、关键流程

### 6.1 任务式 Agent 执行流程

```
用户点击「启动」
    │
    ▼
POST /api/agent/{id}/start-task  { params: {...} }
    │
    ▼
AgentFactory.start_task(agent_id, params)
    │  启动独立子进程（或线程）
    ▼
TaskRunner 按 MD 的 flow 逐步执行：
    for step in flow:
        result = ToolExecutor.execute(step.tool, resolve(step.params))
        yield {event:"log", data:{step, result}}   → SSE
    yield {event:"done"}
    │
    ▼
前端 SSE → TaskAgentView 消息区流式追加
```

### 6.2 Web 服务式 Agent 流程

```
用户点击「启动」
    │
    ▼
POST /api/agent/{id}/start-service
    │
    ▼
启动 HTTP 服务子进程（监听分配端口，如 9200）
    │  记录到 agent_factory._services[agent_id] = {port, proc}
    ▼
外部：POST http://host:9200/detect  { image: "..." }
    │
    ▼
服务进程匹配 API 路由 /detect → 按 flow 组合调用工具 → 返回 JSON
    │  同时把请求日志推送到主进程（通过 bus 或日志文件）
    ▼
前端 消息区显示请求日志
```

### 6.3 API 测试区流程

```
用户在界面选择 API + 填参数
    │
    ▼
POST /api/agent/{id}/test-api  { api_name, params }
    │
    ▼
后端内部代理请求到 http://127.0.0.1:{port}{path}
    │  （走本机回环，避免 CORS 与外部网络问题）
    ▼
返回响应 JSON → 前端测试结果区展示
```

---

## 八、端口与生命周期管理

### 7.1 端口分配

| 方案 | 说明 | 推荐 |
|------|------|------|
| **固定端口** | MD 中声明 `port`，冲突时报错 | 简单场景 |
| **动态分配** | 从端口池（如 9200-9299）分配，记录到注册表 | **推荐**，避免冲突 |
| 统一网关 | 所有 Agent 服务共用网关，按路径前缀路由 | 复杂、需要网关组件 |

设计采用**动态分配 + 端口池**：`storage/agent_services.json` 记录 `{agent_id: {port, status}}`。

### 7.2 生命周期（三类 Agent 统一）

| 动作 | 说明 |
|------|------|
| 启动 | 启动独立子进程 → 健康检查（心跳 / 端口探测） |
| 停止 | 发送停止信号 → 等待退出 → 超时强杀 |
| 崩溃恢复 | 进程异常退出 → 记录日志（可选自动重启） |
| 引擎重启 | FastAPI 重启后，**所有 Agent 均不自动恢复**（除系统默认交互 Agent），需用户手动启动 |

> ⚠️ 与现有 `agent_factory` 一致：进程状态存内存字典，引擎重启即丢失。若需持久常驻，后续可引入进程守护。

### 7.3 安全

- 默认监听 `127.0.0.1`（仅本机），MD 中显式声明 `host: 0.0.0.0` 才对外暴露
- 可选的 API Key 鉴权（MD 中配置 `auth: {type: api_key, header: X-API-Key}`）
- 请求体大小限制、超时限制

---

## 九、注册表结构扩展

`resources/agents/registry.json` 每条记录扩展 role 相关字段：

```json
{
  "id": "yolo-service-agent",
  "name": "YOLO检测服务",
  "role": "web-service",
  "status": "active",
  "spec_path": "definitions/yolo-service-agent.md",
  "impl_path": "implementations/yolo-service-agent/",
  "tools": ["yolo-detection-mmyolo"],
  "apis": ["detect", "train"],          // web-service 专有
  "server": { "port": null },           // 运行时分配
  "flow": null,                          // task 专有
  "tags": ["目标检测", "HTTP服务"],
  "health": "healthy"
}
```

| 字段 | 适用类型 | 说明 |
|------|---------|------|
| `flow` | task | 步骤化工具调用流程 |
| `apis` / `server` | web-service | API 列表与服务配置 |

---

## 十、进程模型（统一）

### 9.1 统一进程模型

**所有 Agent 在运行时都是独立进程**，由 `AgentFactory` 统一管理：

```
┌──────────────────────────────────────────────────────┐
│ FastAPI 主进程（引擎）                                 │
│                                                        │
│  AgentFactory（进程管理器）                            │
│    _processes: { agent_id → Process }                 │
│         │                                              │
│         ├── 子进程: 系统默认交互 Agent   (interactive) │
│         ├── 子进程: 用户自定义交互 Agent (interactive) │
│         ├── 子进程: 任务式 Agent        (task)         │
│         └── 子进程: Web 服务 Agent      (web-service)  │
│                                                        │
│  通信：stdin/stdout JSON-line（现有 entrypoint 协议）  │
└──────────────────────────────────────────────────────┘
```

### 9.2 三类 Agent 进程的差异

| 类型 | 进程行为 |
|------|---------|
| interactive | 循环等待用户消息（stdin 接收），逐条处理并流式返回 |
| task | 接收「启动」任务 → 按 flow 执行 → 推送日志 → 结束后进程可保留待下次启动 |
| web-service | 启动后监听 HTTP 端口，持续等待外部请求 |

### 9.3 进程管理能力（三类通用）

| 能力 | 说明 |
|------|------|
| 启动 | `POST /api/agent/{id}/start` → 启动独立子进程 |
| 关闭 | `POST /api/agent/{id}/stop` → 优雅停止 |
| 重启 | `POST /api/agent/{id}/restart` |
| 状态 | `GET /api/agent/{id}/status` → running / stopped / pid / health |
| 进程隔离 | 单 Agent 崩溃不影响引擎与其他 Agent |

---

## 十一、兼容性与迁移

1. **枚举收敛**：删除的 `orchestrator`/`observer` 在当前注册表中无实际使用，迁移零成本。
2. **交互 Agent 进程化**（**本次核心改造**）：
   - 现状：`chat_routes.py` 直接导入模块级单例，与 FastAPI **同进程**。
   - 目标：经 `AgentFactory` 启动为**独立子进程**，对话请求通过 stdin/stdout 转发。
   - 影响面：`chat_routes.py`、`app/main.py` 的启动逻辑需同步改造；对前端接口（`/api/chat/send`）**保持兼容**。
3. **系统默认交互 Agent**：仍随引擎自动启动，但可通过界面手动关闭 / 重启。
4. **其他现有 Agent**：`echo-agent`、`weather-query-agent`（interactive）与 4 个 task Agent 继续可用，且自动获得独立进程与启停能力。
5. **前端视图**：新增三个视图，不影响现有 `chat` / `agent-detail` 等。

---

## 十二、实施建议（分阶段）

| 阶段 | 内容 | 产出 |
|------|------|------|
| **P0** | **交互 Agent 进程化**：改造 `chat_routes.py` + `main.py`，走 AgentFactory 子进程；补启停 API 与前端按钮 | 三类 Agent 统一进程模型 |
| **P1** | 枚举收敛 + 规格字段扩展（`flow` / `apis`）+ 向后兼容 | 数据模型就绪 |
| **P2** | 任务式 Agent：flow 执行器 + `TaskAgentView` | 可跑通一个流程式任务 |
| **P3** | Web 服务式 Agent：HTTP 服务宿主 + 端口分配 + `WebServiceAgentView` | 可对外提供 API |
| **P4** | API 测试区 + 消息流 + 生命周期完善 | 完整体验 |

---

## 十三、待确认问题

1. **Web 服务 Agent 的隔离**：每个 Agent 独立进程，还是多个 Agent 共享一个多路由服务？
2. **认证鉴权**：对外 API 是否需要内置鉴权（API Key / Token）？
3. **任务式 Agent 的触发**：是否支持定时触发（cron），还是仅手动点击启动？
4. **web-service 与 task 的边界**：task 是否也可以被 HTTP 触发（即 task 的一种入口）？
5. **端口暴露范围**：默认仅本机，是否需要公网暴露能力？
6. **交互 Agent 进程化的对话流**：改造后对话请求经子进程转发，是否可接受额外的进程间通信开销？（现有 stdin/stdout 协议已验证可用）

---

## 十四、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.3.0 | 2026-10-11 | 新增「Agent 创建流程」章节：类型优先选择 + 三类默认需求模板；`generate-spec` 接口增加 role 参数 |
| 0.2.0 | 2026-10-11 | 明确「所有 Agent 独立进程 + 均可手动启停」；交互式界面增加启停按钮；新增进程模型章节 |
| 0.1.0 | 2026-10-11 | 初稿：Agent 三类体系设计（交互式/任务式/Web服务式）与三类界面 |

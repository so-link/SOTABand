# Agent 类型体系设计

> 将 Agent 从四类（interactive / task / orchestrator / observer）收敛为**三类**：
> **交互式（interactive）**、**任务式（task）**、**Web 服务式（web-service）**。
> **三类 Agent 的执行界面各不相同。**
>
> 本文档为设计说明；第 1.3、10 章的统一进程模型与交互式 Agent 进程化**已实现**，任务式/Web 服务式的独立执行界面按 `agent-detail` 视图落地（见第十一章）。

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

> 实现现状：统一独立进程模型已落地。`chat_routes.py` 不再持有同进程单例，对话请求经 `AgentFactory.execute_stream` 转发到交互 Agent 子进程（stdin/stdout JSON-line 协议）；引擎启动时由 `main.py` lifespan 自动拉起系统默认交互 Agent，且支持手动关闭 / 重启。

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

**实现**：任务式界面落在 `AgentDetailView.tsx`（`agent-detail:{id}` 参数化视图）中。
- **从规格解析参数表单**，提交后 `POST /api/agent/{id}/execute`（SSE 流式接收 content / card / done / error 事件，带 AbortController 超时中断）
- **启停按钮**：`POST /api/agent/{id}/start` / `stop` / `restart`（进程级），与执行（任务级）分离
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

**实现**：Web 服务界面同样落在 `AgentDetailView.tsx` 中。
- **启动/关闭按钮**：控制 HTTP 服务进程（`start`/`stop`）；启动时宿主从子进程的 `service_ready` 事件捕获实际监听地址（`agent_factory.get_service`，经 `GET /api/agent/{id}/service` 查询）
- **API 测试区**：
  - 下拉选择该 Agent 暴露的 API（来自 `service_ready` 上报 / 系统 API 注册表）
  - 根据 API 的 `params` 动态渲染输入表单
  - 「发送测试请求」→ `POST /api/agent/{id}/test-api` 由后端**内部代理**请求本机回环地址（避免 CORS）→ 展示 JSON 结果
- **API 文档**：注册时自动生成对外 API 文档（`GET /api/agent/{id}/api-doc` 及下载端点）

---

## 五、Agent 创建流程（类型优先 + 需求模板）

### 5.1 现状

早期创建流程（`AgentEditorView.tsx`）为四步：

```
Step 1 描述需求  →  Step 2 审阅 MD  →  Step 3 代码核验  →  Step 4 完成
```

最初 Step 1 是一个空白的需求文本框，所有 Agent 走同一套生成逻辑。

### 5.2 现行流程（已实现）

**在描述需求之前先让用户选择 Agent 类型**（interactive / task / web-service 单选，切换时带模板确认对话框）；选定类型后，需求框**自动填入该类型的默认模板**作为起点。后端 `generate-spec` 按 role 注入类型专属生成指引。

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

`POST /api/agent/generate-spec` 请求体带 `role` 字段：

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

## 六、前后端落地情况

### 5.1 后端

| 文件 | 现状 |
|------|------|
| `core/agent/base.py` | ✅ `AgentRole` 已收敛为三类；task 的 flow / web-service 的 apis 约束体现在规格文档与代码模板中 |
| `core/agent/factory.py` | ✅ `start/stop/restart/status` 对三类统一为独立进程模型；web-service 启动后捕获 `service_ready` 事件记录 host/port/apis |
| `core/agent/entrypoint.py` | ✅ 统一子进程入口，按 role 行为分化由生成的 agent.py 自身实现 |
| `app/api/routes/chat_routes.py` | ✅ 交互 Agent 已经 AgentFactory 子进程转发（原同进程单例方案已替换） |
| `app/main.py` | ✅ lifespan 仅自动拉起系统默认交互 Agent；shutdown 时停止全部子进程 |
| `core/resource/builder/agent_builder.py` | ✅ 代码生成按三类角色模板区分 |
| `app/api/routes/agent_routes.py` | ✅ 启停/状态/execute(SSE)/service/test-api/api-doc 端点均已实现（未单独设 start-task/stop-task，启停与执行分离：`/start` 管进程，`/execute` 管一次任务） |
| `core/agent/templates/` | ✅ `interactive.py.tmpl` / `task.py.tmpl` / `web_service.py.tmpl` 三个模板 |

### 5.2 前端

| 文件 | 现状 |
|------|------|
| `stores/ui-store.ts` | 未拆三个视图；Agent 运行/详情统一走参数化视图 `agent-detail:{id}` |
| `components/center-panel/AgentDetailView.tsx` | ✅ 承载任务式运行（参数表单 + SSE 消息区）与 Web 服务式（服务地址 + API 测试区 + API 文档） |
| `components/center-panel/AgentEditorView.tsx` | ✅ 四步向导 + 类型选择/模板预填 |
| `stores/agent-editor-store.ts` | ✅ 含 agentRole 状态与三类默认模板 |
| `services/api/agent.ts` | ✅ start / stop / status / execute / service / test-api 调用 |

> 界面组织与原设计的差异：没有为三类 Agent 各建一个顶层视图（`InteractiveAgentView` / `TaskAgentView` / `WebServiceAgentView`），而是统一为一个 `agent-detail` 视图，按角色渲染对应区块——交互式仍复用主对话视图。

---

## 七、关键流程

### 6.1 任务式 Agent 执行流程

```
用户在 agent-detail 视图填参数并点击「运行」
    │
    ▼
POST /api/agent/{id}/execute  { params: {...} }   (SSE)
    │
    ▼
AgentFactory.execute_stream(agent_id, params)
    │  向已启动的子进程 stdin 写任务 JSON
    ▼
Agent 子进程按 MD 的 flow 逐步执行：
    for step in flow:
        result = ToolExecutor.execute(step.tool, resolve(step.params))
        yield {event:"log"/"card", data:{step, result}}   → stdout → SSE
    yield {event:"done"}
    │
    ▼
前端 SSE → agent-detail 消息区流式追加
```

### 6.2 Web 服务式 Agent 流程

```
用户点击「启动」
    │
    ▼
POST /api/agent/{id}/start
    │
    ▼
AgentFactory 启动子进程 → 子进程内 WebServiceHost 监听端口
    │  （host 默认 127.0.0.1；port 传 0 时由系统分配实际端口）
    │  子进程上报 service_ready 事件 → agent_factory._services[agent_id] = {host, port, apis}
    ▼
外部：POST http://host:{port}/detect  { image: "..." }
    │
    ▼
服务进程匹配 API 路由 /detect → 按 flow 组合调用工具 → 返回 JSON
    ▼
前端 agent-detail 显示服务地址（GET /api/agent/{id}/service）
```

### 6.3 API 测试区流程

```
用户在界面选择 API + 填参数
    │
    ▼
POST /api/agent/{id}/test-api  { api_name, params }
    │
    ▼
后端读取 get_service(agent_id) 的 host/port，内部代理请求到该 HTTP 服务
    │  （走本机回环，避免 CORS 与外部网络问题）
    ▼
返回响应 JSON → 前端测试结果区展示
```

---

## 八、端口与生命周期管理

### 7.1 端口分配

| 方案 | 说明 | 采用 |
|------|------|------|
| **固定端口** | MD 中声明 `port`，冲突时报错 | 可选（spec 声明时生效） |
| **动态分配** | 生成代码默认 `port=0`，由操作系统分配实际端口，经 `service_ready` 事件上报 | ✅ **默认方案** |
| 统一网关 | 所有 Agent 服务共用网关，按路径前缀路由 | 未实现 |

实现采用**动态分配**：`core/agent/web_server.py` 的 `WebServiceHost` 以 `port=0` 监听时记录 `getsockname()` 的实际端口，子进程通过 `service_ready` 事件上报，主进程保存在 `AgentFactory._services`（内存字典）。引擎重启后需重新启动 Agent 并重新捕获。

### 7.2 生命周期（三类 Agent 统一）

| 动作 | 说明 |
|------|------|
| 启动 | `POST /api/agent/{id}/start` → 启动独立子进程；代码缺失时先尝试从 spec 自动重建；web-service 额外捕获 `service_ready` |
| 停止 | `POST /api/agent/{id}/stop` → 终止子进程 |
| 重启 | `POST /api/agent/{id}/restart` |
| 崩溃 | 进程异常退出后状态变为 stopped，不自动重启（进程状态存内存字典，引擎重启即丢失） |
| 引擎重启 | FastAPI 重启后，**所有 Agent 均不自动恢复**（除系统默认交互 Agent），需用户手动启动 |

### 7.3 安全

- `WebServiceHost` 默认监听 `127.0.0.1`（仅本机）；对外暴露需显式配置
- API Key 鉴权、请求体大小限制为规划项，尚未实现

---

## 九、注册表结构

`resources/agents/registry.json` 每条记录的实际字段：

```json
{
  "id": "city-info-query-agent",
  "name": "城市信息查询 Agent",
  "version": "0.1.0",
  "role": "interactive",
  "status": "active",
  "health": "healthy",
  "owner": "local-xxxxxxxx",
  "visibility": "public",
  "spec_path": "definitions/city-info-query-agent.md",
  "impl_path": "implementations/city-info-query-agent/",
  "tools": ["..."],
  "tags": ["..."],
  "created_at": "..."
}
```

- `role` ∈ interactive / task / web-service；`flow` / `apis` / `server` 等内容写在 MD 规范文档中，不复制进注册表
- `visibility` 默认 `public`（Agent 是共享代码资产）；`owner` 记录创建者
- web-service 的运行期端口等信息只存在于 `AgentFactory._services` 内存态，不落注册表

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

1. **枚举收敛**：删除的 `orchestrator`/`observer` 在当前注册表中无实际使用，迁移零成本。未知 role 解析时回退为 `task`。
2. **交互 Agent 进程化**（**已落地**）：
   - 原实现：`chat_routes.py` 直接导入模块级单例，与 FastAPI 同进程。
   - 现实现：经 `AgentFactory` 启动为独立子进程，对话请求通过 stdin/stdout 转发。
   - 对前端接口（`/api/chat/send`）保持兼容。
3. **系统默认交互 Agent**：仍随引擎自动启动，但可通过界面手动关闭 / 重启。
4. **其他现有 Agent**：用户创建的 Agent（如 city-info-query-agent）经统一启停端点获得独立进程与启停能力。
5. **前端视图**：统一为 `agent-detail:{id}` 参数化视图，不影响现有 `chat` 等视图。

---

## 十二、实施状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 交互 Agent 进程化：`chat_routes.py` + `main.py` 走 AgentFactory 子进程；启停 API 与前端按钮 | ✅ 完成 |
| **P1** | 枚举收敛 + 角色模板 + 向后兼容 | ✅ 完成 |
| **P2** | 任务式 Agent：agent-detail 参数表单 + execute SSE 运行 | ✅ 完成（flow 由生成的 agent.py 执行） |
| **P3** | Web 服务式 Agent：WebServiceHost + 动态端口 + service_ready 上报 | ✅ 完成 |
| **P4** | API 测试区 + api-doc 自动生成 + 生命周期完善 | ✅ 完成（鉴权/进程守护仍为规划项） |

---

## 十三、遗留问题

1. **认证鉴权**：对外 API 是否需要内置鉴权（API Key / Token）？—— 当前无鉴权，默认仅监听 127.0.0.1
2. **任务式 Agent 的触发**：是否支持定时触发（cron），还是仅手动点击启动？—— 当前仅手动
3. **web-service 与 task 的边界**：task 是否也可以被 HTTP 触发（即 task 的一种入口）？
4. **端口暴露范围**：默认仅本机，是否需要公网暴露能力？
5. **进程守护**：崩溃自动重启 / 引擎重启后自动恢复，尚未实现

---

## 十四、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.3.0 | 2026-10-11 | 新增「Agent 创建流程」章节：类型优先选择 + 三类默认需求模板；`generate-spec` 接口增加 role 参数 |
| 0.2.0 | 2026-10-11 | 明确「所有 Agent 独立进程 + 均可手动启停」；交互式界面增加启停按钮；新增进程模型章节 |
| 0.1.0 | 2026-10-11 | 初稿：Agent 三类体系设计（交互式/任务式/Web服务式）与三类界面 |

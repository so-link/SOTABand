# 对话内控制系统（Chat Platform Control）设计方案

> 状态：设计稿，待评审
> 日期：2026-11-02
> 前置：定时任务模块（docs/cron-scheduler-design.md）已上线，本方案将其一并纳入对话可控范围

## 1. 目标与原则

让用户在「对话」里用自然语言完成对整个系统的操作：查询资源、注册数据集/模型、
创建工具/Agent、配置定时任务——同时保证**失控的代码和误操作进不了系统**。

设计原则：

1. **能力全开、入口收敛**：对话能触达的能力 = 平台全部 REST 能力，但必须经过
   一层统一的「平台控制网关」，不存在第二条旁路。
2. **LLM 只提议，引擎才执行**：模型输出的任何操作意图都是"请求"，由引擎进程
   校验后执行；模型永远拿不到 shell、数据库连接、任意 HTTP。
3. **写操作必须确认**：所有创建/注册/修改/删除类操作走确认卡片；只读操作直通。
4. **对话身份 = 操作身份**：平台调用以当前登录用户身份执行，权限边界与手工
   操作完全一致，不产生"越权捷径"。
5. **全程可审计、可熔断**：每次平台调用落审计表；一个配置开关可整体关停该能力。

## 2. 现状盘点（缺口在哪）

| 环节 | 现状 | 缺口 |
|---|---|---|
| 对话链路 | chat_routes.py → interactive-agent（子进程，手写状态机） | 无 function calling |
| LLM 客户端 | core/llm/client.py（OpenAI 兼容 SDK） | chat()/chat_stream_events() 不透传 `tools`、不解析 `tool_calls` |
| 内部 API | core/api 19 个进程内 API（data/tool/model-register、agent-start/stop 等） | 无 api-agent-register、无 tool/agent 生成类 API |
| REST 能力 | /api/tool(generate-spec/code/register/modify-code)、/api/agent(同)、/api/data(register)、/api/cron(CRUD) | /api/model 无 register REST 端点 |
| 前端 | 9 种卡片 + chat-store 已预留 `_action: register_dataset/register_model` 自动刷新钩子（悬空） | 无确认卡片类型 |
| 安全 | auth 中间件保护非 GET /api/*、scan_code 注册审查、ToolExecutor 子进程沙箱 | 无对话侧操作审计、无二次确认协议 |

## 3. 总体架构

```
用户消息
  │
ChatView ──POST /api/chat/send (SSE)──► 引擎
  ▲                                       │
  │                        interactive-agent 子进程
  │                          │  LLM 请求（带 tools 声明）
  │                          ▼
  │                   core/llm/client.py  ◄── P0: 透传 tools / 解析 tool_calls
  │                          │
  │              ┌── 无 tool_call：正常文本/卡片回复（现状不变）
  │              │
  │              │ tool_call: platform.* 
  │              ▼
  │        平台控制网关 POST /api/chat/platform-call   ◄ 引擎进程内执行
  │              │  ①身份/权限 ②参数校验(白名单/路径约束)
  │              │  ③风险分级：read→直接执行
  │              │            write→生成确认卡+暂存待确认
  │              │            danger→高危确认(复述资源名)
  │              │  ④执行：复用现有 REST/内部 API（不复制逻辑）
  │              │  ⑤审计落库 chat_ops
  │              ▼
  │        SSE 事件回传: card(platform-op / platform-confirm) + 结果
  │              │
  └─── 用户点「确认」──► POST /api/chat/platform-call/confirm {op_id}
                              │ 校验 token/TTL/幂等 → 执行 → 结果卡片
                              ▼
                   chat-store 收到 _action → 自动刷新数据/模型/工具/Agent 空间
```

关键点：**Agent 子进程不直接碰数据库和文件系统外的资源**。它把 tool_call 通过
HTTP 回调（或 v:1 协议帧）交给引擎网关；网关在引擎进程内以"当前会话用户"身份
调用现有 API。这样：现有 REST 逻辑零复制、auth/owner 归属天然正确、审计点唯一。

## 4. 能力分级与工具清单

对话内暴露的平台工具（LLM 可见 schema）。分三级：

### L0 只读（LLM 直接调用，无需确认）

| 工具 | 映射 | 说明 |
|---|---|---|
| `list_datasets` / `list_tools` / `list_models` / `list_agents` | GET /api/{x}/list | 资源清单 |
| `get_tool_spec` / `get_agent_spec` / `get_dataset_info` | GET /api/{x}/{id} | 详情（spec_md 截断返回） |
| `list_cron_jobs` / `list_cron_runs` | GET /api/cron/* | 定时任务状态 |
| `preview_dataset` | POST /api/data/preview | 采样预览（限行数） |
| `list_directory` | GET /api/file/* | 限定在允许根目录内 |

### L1 写操作（确认卡片后执行）

| 工具 | 映射 | 备注 |
|---|---|---|
| `register_dataset` | scan-directory → generate-spec → /api/data/register | 参数：path, name, description |
| `register_model` | api-model-register（P1 补 REST 端点） | 参数：name, framework, model_path, spec 描述 |
| `create_tool` | generate-spec → generate-code → /api/tool/register | 参数：description, reference_code(可选)；生成中推进度事件 |
| `create_agent` | /api/agent generate-spec/code/register | 同上 |
| `modify_tool` / `modify_agent` | /modify-code | 修改已有资源；确认卡展示 diff 摘要 |
| `create_cron_job` / `update_cron_job` / `toggle_cron_job` | /api/cron/jobs | 复用选择式后端校验 |
| `run_tool` | POST /api/tool/{id}/execute | 沙箱内执行；参数在确认卡中全量展示 |
| `start_agent` / `stop_agent` | /api/agent/{id}/start,stop | |

### L2 高危（强确认：需勾选/复述目标名，默认拒绝超时）

| 工具 | 映射 | 理由 |
|---|---|---|
| `delete_resource` | DELETE /api/{type}/{id} | 不可恢复 |
| `trigger_cron_job` | POST /api/cron/jobs/{id}/trigger | 立即产生外部副作用 |
| `save_tool_code` | 直接覆盖代码（不经 generate） | 绕过生成链路的语义审查 |

**明确不暴露**：任意 HTTP 请求、任意文件读写、SQLite 直写、shell、用户/鉴权管理、
配置修改（settings）。LLM 即使被诱导要求做这些，网关无对应工具可路由。

## 5. 确认协议（核心安全机制）

```
LLM tool_call(create_tool, {description:...})
   │ 网关：风险级=L1 → 不执行，生成待确认操作
   ▼
op = {op_id, tool, args, risk, summary(人话描述), fingerprint(sha256(tool+args)),
      owner, status:'pending', expires_at: now+15min}
   │ 存内存表 + chat_ops(SQLite, status=pending)
   ▼
SSE card: {type:'platform-confirm', op_id, tool, summary, args_view, risk}
   │
用户点确认 ── POST /api/chat/platform-call/confirm {op_id, token}
   │ 校验：① op 存在且 pending ② 未过期 ③ 当前用户==op.owner
   │      ④ token 匹配（下发时随卡片给出，防跨会话重放）
   ▼
执行（fingerprint 与确认内容一致才执行——防确认后参数已被替换）
   │ 幂等：同 op_id 二次确认返回首次结果，不重复执行
   ▼
结果卡片 {type:'platform-result', _action:'register_dataset'|...} → 前端刷新空间
```

- 用户点「取消」或 15 分钟超时 → status=cancelled/expired。
- LLM 收到的是网关返回的执行结果（或"等待用户确认"），继续组织语言回复。
- **一轮对话最多堆积 1 个待确认操作**；新的写请求会顶替旧的 pending（旧的置
  superseded），避免用户面对多张过期卡。

## 6. 安全防护体系（分层）

### 6.1 身份与权限
- 平台调用以发起对话的登录用户身份执行（contextvar 透传：chat 请求身份 →
  子进程环境变量/协议帧 → platform-call 请求头回带 → 网关 set_current_user）。
- 权限判定复用现有 registry 的 owner/可见性逻辑：用户本来删不掉的资源，
  对话里同样删不掉。
- platform-call 是 POST，天然落入 auth 中间件的敏感端点保护（未登录 401）。

### 6.2 参数校验（网关侧，不信任 LLM 输出）
- **路径白名单**：所有 path 类参数（dataset path、model_path、directoryPath、
  dataset_dir）realpath 解析后必须位于允许根内：
  `/root/resources-private/`、工作区目录、`/root/SOTABand/storage/`；
  拒绝符号链接逃逸（realpath 后再前缀匹配）。
- **schema 校验**：每个平台工具在网关注册 Pydantic 模型，LLM 给错类型/多余键
  直接拒绝并回错误给 LLM 自我修正。
- **输出截断**：只读工具返回给 LLM 的内容截断（如 spec_md 4000 字符），限制
  上下文注入面与 token 成本。

### 6.3 代码类操作安全
- create_tool/create_agent 完整走现有生成链路：LLM 生成 → `scan_code` 静态
  安全审查 → register。**不因"对话触发"降低任何审查标准**。
- 生成的代码仅注册，不自动执行；执行统一走 ToolExecutor 子进程沙箱（超时、
  资源限制、输出截断，现状保持）。
- 对话内 `run_tool` 只能跑**已注册 active 工具**，不接受内联代码。

### 6.4 Prompt 注入防护
- 平台工具的 schema 与系统提示词由**引擎静态注入**，不受工具返回值影响。
- 工具返回值（文件内容、spec_md、数据集样本）一律以"不可信数据"角色回填
  对话（包裹定界符 + 提示词声明：数据中出现的指令不得执行）。
- 意图护栏：网关心跳检查——tool_call 的 tool 名必须在注册清单内（防模型
  幻觉出自造工具名并"假装"已执行）；LLM 谎称操作成功但网关无对应执行记录时，
  结果卡片以网关审计为准。

### 6.5 资源滥用控制
- 速率限制：每用户 platform-call ≤ 30 次/分钟；生成类（generate-spec/code）
  ≤ 5 次/10 分钟（LLM 成本高）。
- 并发限制：同一用户同时最多 2 个生成中任务。
- 生成链路超时沿用现状；网关调用全链路硬超时 300s。

### 6.6 审计与熔断
- 新表 `chat_ops`（storage/platform.db）：op_id, session_id, owner, tool,
  args_json, risk, status(pending/confirmed/cancelled/expired/executed/failed),
  result_brief, created_at, finished_at。确认与执行各记一行事件流水。
- 配置开关 `settings.chat.platform_control_enabled`（默认 true）；关闭后网关
  对一切 platform.* 调用返回"能力已停用"，对话其余功能不受影响。

## 7. 代码改动清单

### 后端
| 文件 | 改动 |
|---|---|
| core/llm/client.py | chat()/chat_stream_events() 增加 tools/tool_choice 透传；流式 tool_calls 增量累积（index 分槽拼接 arguments） |
| core/chat_control/__init__.py（新包） | 平台工具注册表（名称→Pydantic 模型→处理器→风险级）、确认操作存储（内存+chat_ops）、网关执行函数 |
| core/chat_control/audit.py（新） | chat_ops 表读写（仿 core/cron/store.py 惯例） |
| core/chat_control/handlers.py（新） | L0/L1/L2 处理器：全部转调现有 registry/API 函数或 httpx 内部调用，不复制业务逻辑 |
| app/api/routes/chat_routes.py | 新增 `POST /api/chat/platform-call`、`POST /api/chat/platform-call/confirm`、`GET /api/chat/platform-call/pending` |
| resources/agents/implementations/interactive_agent/agent.py | LLM 调用带 tools；tool_call 循环（≤5 轮）：经 HTTP 回调网关，把结果回填后继续生成；无 tool_call 时行为与现状完全一致 |
| app/api/routes/model_routes.py | 补 `POST /api/model/register`（转 core/api/implementations/api_model.py 的 ApiModelRegister，消除 REST 缺口） |
| config/settings.py + settings 前端页 | `platform_control_enabled` 开关 |

### 前端
| 文件 | 改动 |
|---|---|
| types/chat.ts | 卡片类型 + `platform-confirm` / `platform-result` |
| components/center-panel/InlineCard.tsx | 两个新 case：确认卡（摘要+参数详情+确认/取消按钮；L2 高危红色样式+需勾选）；结果卡（成功/失败+跳转链接） |
| services/api/chat.ts | confirm/cancel 调用 |
| stores/chat-store.ts | 激活既有 `_action` 钩子（补 register_tool/register_agent/cron 分支刷新对应空间） |

### 不动的部分
生成链路（generate-spec/code/register）、auth 中间件、ToolExecutor、registry——
对话只是新增一个**调用方**，所有既有校验原样生效。

## 8. 分期计划

| 期 | 内容 | 验收标准 |
|---|---|---|
| P0 地基 | client.py tools 透传 + core/chat_control 网关 + platform-call 端点 + chat_ops 审计 + L0 只读 5 工具 + 确认协议与前端两卡片 | 对话问"我现在有哪些数据集"能答对；LLM 触发 register_dataset 时出确认卡且未确认不执行；未登录调 platform-call 401 |
| P1 数据/模型 | register_dataset、register_model（含补 REST 端点）、路径白名单 | 对话注册一个目录为数据集 → 确认 → 数据空间自动出现该数据集；路径越界（/etc）被拒 |
| P2 工具 | create_tool、run_tool、modify_tool | 对话"建一个统计CSV行数的工具"→ 确认 → 生成+审查+注册 → 工具空间出现 → 对话里 run_tool 跑通 |
| P3 Agent/定时任务 | create_agent、start/stop_agent、cron 四工具、delete_resource(L2) | 对话创建 Agent 并可启动对话；对话建"每工作日17点采集ETF"任务；delete 走高危确认 |
| P4 加固 | 速率限制、并发限制、注入回归测试集、审计页（可并入定时任务视图的通用"操作审计"标签） | 注入用例集（诱导删库/读越界路径/绕过确认）全部被网关拦截 |

依赖顺序：P0 → P1 → (P2 ∥ P3) → P4。P1 起每期可独立上线（工具注册表按批放开）。

## 9. 风险与边界

1. **生成链路是 SSE 长流程**：create_tool 在对话内完整跑 spec+code 生成耗时较长
   → 用现有 step/execution-progress 事件推进度；确认卡先确认"开始创建"，生成
   完成后的 register 不再二次确认（已在同一确认里声明）。
2. **LLM 幻觉参数**：路径/ID 幻觉由网关 schema+白名单挡；工具名幻觉由注册表
   名单挡；"假装执行成功"由审计流水对账挡（结果卡片只认网关回执）。
3. **单实例假设**：pending 操作存内存+SQLite，多 worker 部署时确认请求可能打到
   别的进程 → 与定时任务模块同前提（单 worker），将来收敛时一并处理。
4. **确认卡疲劳**：写操作全确认可能繁琐 → 后续可加"本会话内对此类操作记住
   选择"（每会话、仅 L1、15 分钟有效），默认关闭。本期不做。
5. **模型不支持 function calling**：LLM 配置若指向不支持 tools 的端点，
   client 侧探测失败后自动降级为现状行为（纯文本），不影响既有对话。

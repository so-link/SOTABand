# SOTABand Engine 架构设计

> 多智能体驱动的多模态智能处理引擎

---

## 一、核心理念

构建一个多智能体驱动的多模态智能处理引擎，实现对开放世界多源异构数据的智能处理流程的自动化构建与动态扩展，支持在分布式异构算力集群上完成多智能体的协同工作。

## 二、核心设计理念

### 2.1 探索式用户交互

用户在工作区间中选择数据后发出需求，系统自动触发任务编排。如发现没有工具可完成，则自动编写代码，经用户核验后落地为本地工具。后续类似需求直接调用本地工具执行，工具空间随用户探索不断增长。

### 2.2 自然语言驱动的多智能体编排

用户通过自然语言描述复杂任务流程，系统根据已有Agent自动生成编排描述文件（Markdown格式）。用户可交互修改该流程定义，最终形成任务编排描述文件，驱动多智能体协同工作。

### 2.3 一切以Agent为主体

所有处理的最终执行主体都是Agent。包括与用户交互、接收用户探索指令在内，也由一个专门的交互Agent负责。系统本质上是多智能体协作生态系统。

### 2.4 资源全生命周期管理

资源层内所有资源（数据、工具、模型、任务等）均需进行标准化描述、注册登记，支持查询获取和动态整合。所有资源的构建、生成、注册、版本管理及全生命周期管理均由核心层统一把关和支撑。

### 2.5 AI模型作为特殊工具

所有AI模型对应其输出和使用方式，作为特殊工具纳入工具空间管理，同时在模型空间中进行独立的版本管理。模型与工具建立关联关系，便于追踪和调度。

---

## 三、系统分层

```
┌─────────────────────────────────────────────────────┐
│  应用层 (app/)                                       │
│  探索式交互界面、工作区间、AI助手、任务编排、可视化     │
├─────────────────────────────────────────────────────┤
│  核心层 (core/)                                      │
│  唯一具有执行能力的层次                               │
│  ┌──────────┬──────────┬──────────┬──────────┐      │
│  │ 工作流引擎 │ 资源管理  │ 调度算力  │ 多Agent协作│      │
│  ├──────────┼──────────┼──────────┼──────────┤      │
│  │ 安全治理  │ 可观测性  │          │          │      │
│  └──────────┴──────────┴──────────┴──────────┘      │
├─────────────────────────────────────────────────────┤
│  资源层 (系统用户根 SB-USER/100/ + 各用户家目录 SB-USER/<uid>/) │
│  数据空间 │ 工具空间 │ 模型空间 │ Agent空间 │ 用户空间 │ 任务空间 │
│  （resources/ 为出厂种子包，运行期不解析）              │
├─────────────────────────────────────────────────────┤
│  存储层 (SB-USER/_platform/ + 用户目录 SB-USER/<uid>/) │
│  文件存储 + SQLite：平台级实体在 SB-USER/_platform/auth/ │
│  （用户表 users.json、令牌密钥 secret.key）；每用户一库  │
│  SB-USER/<uid>/platform.db（对话、任务历史、定时任务、   │
│  平台操作审计、个人偏好）；缓存为规划项                  │
└─────────────────────────────────────────────────────┘
```

---

## 四、核心层子系统

核心层按目标设计划分为六类能力，其中**资源管理、多智能体协作、工具执行、系统API**已落地实现，并新增了**定时任务调度**（`core/cron/`）与**对话内平台控制网关**（`core/chat_control/`）两个子系统；**可观测性**已部分落地（`core/observability/` 提供对话持久化 `chat_store.py` 与任务历史 `task_history.py`，监控/告警/链路追踪仍为规划项）；**工作流引擎、调度算力**仍为规划中的预留模块（`core/engine/`、`core/scheduler/` 仅有包占位，尚无实现）。

### 4.1 资源生命周期管理子系统 (`core/resource/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| 资源路径模型 | `paths.py` | 双层资源根（`core/user_paths.py` 为唯一实现，本模块是薄封装）：用户私有根 = 家目录 `<USER_HOME>/<uid>/`（扁平布局，空间目录直接落顶层；USER_HOME 默认 `/root/SB-USER`，`SOTABAND_HOME` 可覆盖）+ 公共系统根 = 系统用户家目录 `<USER_HOME>/100/`；读取时私有优先合并（覆盖层语义，fork 副本覆盖公共版本），普通用户写入只进私有根。仓库内 `resources/` 降级为出厂种子包（经 `scripts/seed_system_resources.py` 幂等导入系统用户目录），不再参与运行期解析，仅作种子导入与 fork 升级哈希的参照 |
| 资源构建器 | `builder/tool_builder.py` | 根据用户需求自动生成工具代码，沙箱自测 + 自动调试循环，核验后注册 |
| Agent 构建器 | `builder/agent_builder.py` | MD 规范文档 → Agent Python 代码 |
| API 文档构建器 | `builder/api_doc_builder.py` | 为 Web 服务式 Agent 生成对外 API 文档 |
| 构建基类 | `builder/builder_base.py` | 构建器公共能力（LLM 调用、代码提取等） |
| 工具注册中心 | `registry/tool_registry.py` | 工具注册、描述、版本、可见性管理 |
| Agent 注册中心 | `registry/agent_registry.py` | Agent 注册登记 |
| 数据注册中心 | `registry/data_registry.py` | 数据集注册登记（按 owner 隔离） |
| 模型注册中心 | `registry/model_registry.py` | 模型注册登记 |
| 注册基类 | `registry/registry_base.py` | 注册器抽象基类（register / unregister / get / list_all / update）；各具体注册表实现「读按根顺序合并、写落当前身份可写层」的存储模型 |
| 资源发现器 | `discoverer/tool_discoverer.py`、`discoverer/data_discoverer.py` | 按能力、类型、标签等维度查询和获取资源 |
| 规格大纲 | `spec_outline.py` | 工具/Agent MD 规格文档的大纲解析 |

### 4.2 工具执行子系统 (`core/executor/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| 工具执行器 | `tool_executor.py` | 将工具代码包装为临时脚本，在子进程中 `exec` 执行并调用 `execute(**params)`；解释器按「工具 .venv → 项目 venv → 当前解释器」优先级选择（依赖经 uv 装入工具独立 `.venv`，见 tool-design 6.4）；支持超时与 TOOL_DIR 注入 |

### 4.3 系统 API 子系统 (`core/api/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| API 注册表 | `registry.json` | 19 个系统 API 的登记（LLM 对话、数据 CRUD、工具/模型注册、Agent 启停等） |
| API 规范文档 | `definitions/*.md` | 每个系统 API 的 Markdown 规范（供工具生成器与文档构建引用） |
| API 实现 | `implementations/api_llm.py` 等 5 个模块 | LLM / 数据 / 工具 / 模型 / Agent 五组系统 API 的实现 |
| 发现器 | `discoverer.py` | 按 id / 类别检索系统 API |

工具通过注入的 `_call_api("api-id", ...)` 调用系统 API，从而在不接触密钥原文的前提下使用 LLM、读写数据空间。

### 4.4 多智能体协作子系统 (`core/agent/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| Agent 基类 | `base.py` | `BaseAgent` 抽象 + 三类角色枚举（interactive / task / web_service） |
| 通信总线 | `bus.py` | Agent 间 Pub/Sub + RPC（todo 4.5 本地通信）：子进程侧 `AgentBusClient`（`self._bus`），主进程路由中枢在 `factory.py`；帧协议见 `protocol.py`，与 entrypoint 的 JSON-line 通道复用 |
| Agent 工厂 | `factory.py` | Agent 子进程的启动、停止、重启、健康检查与自动重建（直接以子进程方式托管生命周期） |
| 子进程入口 | `entrypoint.py` | 通过 stdin/stdout JSON-line 协议与主进程通信 |
| 交互基类 | `interactive_base.py` | 交互式 Agent 的共享逻辑（System Prompt 组装、LLM 流式对话） |
| Web 服务宿主 | `web_server.py` | Web 服务式 Agent 的 HTTP 宿主 |
| 工具常驻运行时 | `tool_runtime.py` | Agent 级工具常驻 Worker，预热重型库、循环处理任务 |
| 代码模板 | `templates/interactive.py.tmpl` 等 3 个 | 三类角色的代码生成模板 |

### 4.5 安全与治理子系统 (`core/security/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| 密钥防护 | `secrets.py` | 密钥脱敏（mask/scrub/redact），防止 API Key 出现在调试日志与 LLM 提示词中 |
| 代码核验器（底线闸门） | `code_scan.py` | AST 静态安全审查（todo 3.2）：两级结论——阻断 shell/动态执行/删除原语/ctypes/socket/动态导入，警告 subprocess/open/联网/`../` 上跳/内网 URL/无退出死循环/巨量分配/无守卫自递归/硬编码单一平台路径；未声明导入校验（依赖漂移提示）。落盘前闸门（工具/Agent 注册、保存、导入）+ 执行前 choke point（`ToolExecutor` 统一复查） |
| 静态审查（core API 约束线） | `review.py` | AST 级审查（block/warn 两级：BANNED_CALL 危险调用名单含系统命令/子进程/文件目录增删/权限与链接操作/代码执行原语，OBFUSCATED_CALL getattr 拼名绕过，DYNAMIC_IMPORT 动态导入，RAW_OPEN_WRITE 裸 open 写文件；另语法错误直接 block）：本地文件操作/子进程/目录增删必须走 core API 或 `_call_tool`，策略严于 code_scan 底线名单；默认 `review_mode=audit` 只记录，enforce 切换前需先清零存量内置工具命中。注册接口 enforce 时 400 拒绝，自动调试 round0 初审 + LLM 新代码先审后采纳 |
| 运行时审计 | 子进程注入（`ToolExecutor`） | 工具子进程注入 audit hook：写白名单（自动追加当前用户目录 `SOTABAND_USER_HOME`）/系统调用拦截/网络记录；配套 rlimit 四项默认 0=关闭（与「0=不限制」超时体系一致） |

看门狗为设计目标，尚未实现；对话内平台操作有独立审计流水（`core/chat_control/audit.py`），一般性操作审计仍为规划项。沙箱隔离当前由子进程执行 + 超时 + 静态审查双闸门（code_scan 底线 + review 约束线）+ 运行时审计 + 用户核验环节共同承担。

### 4.6 LLM 接入子系统 (`core/llm/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| 客户端 | `client.py` | OpenAI 兼容协议的统一 LLM 客户端（`create_llm_client()` 对话模型 / `create_gen_llm_client()` 生成模型，后者每次调用时才解析 `LLM_GEN_*` 配置，设置页保存即生效） |
| 服务商预设 | `providers.py` | 多服务商端点/模型预设，按 Key 前缀自动选择端点 |

对话链路由 `.env` 的 `LLM_PROVIDER` / `LLM_API_KEY` 统一配置，DeepSeek 仅为默认项，可切换 MiMo / OpenAI / Kimi / 智谱 / 通义 / 豆包等任意 OpenAI 兼容服务。规格/代码生成链路可用 `LLM_GEN_PROVIDER / LLM_GEN_MODEL / LLM_GEN_API_KEY / LLM_GEN_BASE_URL` 单独指定推理更强的「生成模型」（`create_gen_llm_client()`，未配置时自动回退全局对话模型），与对话模型分离。

### 4.7 可观测性子系统 (`core/observability/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| 对话存储 | `chat_store.py` | 会话与消息持久化（每用户一库 `<USER_HOME>/<uid>/platform.db` 的 `chat_sessions`/`chat_messages` 表，连接与写锁经 `core/user_db.py` 统一按当前身份选库，SQLite WAL）；会话列表、消息读写、标题管理 |
| 任务历史 | `task_history.py` | 任务执行历史（`task_history` 表）：Agent 运行与对话会话聚合为任务记录，记录轮数、耗时、状态；经 `/api/task/*` 暴露给任务空间 |

监控、告警、链路追踪仍为设计目标，尚未实现。

### 4.8 定时任务调度子系统 (`core/cron/`)

| 组件 | 文件 | 职责 |
|------|------|------|
| Cron 解析器 | `cron_parse.py` | 零依赖自研五段 cron 表达式解析与下次触发时间计算 |
| 调度器 | `scheduler.py` | 每个任务一个 asyncio 循环的常驻调度（`max_instances=1`、misfire 跳过/补火一次语义） |
| 存储 | `store.py` | `cron_jobs`/`cron_runs` 表读写（每用户一库 `<USER_HOME>/<uid>/platform.db`，运行记录按任务保留上限截断） |
| 执行器 | `runner.py` | 到点触发：Agent 任务经 `execute_stream` 执行，工具任务经 ToolExecutor 执行 |

引擎启动时随 lifespan 拉起，详见 `docs/cron-scheduler-design.md`。

### 4.9 对话内平台控制网关 (`core/chat_control/`)

对话内自然语言操作系统能力的统一入口：LLM 以 function calling 提议 `platform.*` 工具调用，网关在引擎进程内做身份/风险分级/参数白名单校验后转调现有 API，写操作走确认协议，全程落 `chat_ops` 审计表。组件：`registry.py`（工具注册表与风险分级）、`gateway.py`（校验/限流/执行）、`confirm.py`（待确认操作与指纹/幂等）、`guards.py`（路径白名单/输出截断）、`audit.py`（审计流水）、`handlers.py`（各工具处理器）。详见 `docs/chat-platform-control-design.md`。

### 4.10 工作流引擎 / 调度算力（规划中）

工作流引擎（意图解析、流程编译、DAG 执行器、状态管理、观测器）与异构调度（分配器、负载均衡）均为设计目标，对应模块 `core/engine/`、`core/scheduler/` 目前为空占位。当前阶段意图理解由交互 Agent 的 LLM 对话承担，工具调用走单工具执行路径。

---

## 五、资源层六空间

工具与 Agent 分**公共（系统用户层）**与**个人定制版**两层，全空间统一遵循
覆盖层模型（`core/resource/fork.py`，方案 C）：

- **公共层**（系统用户目录 `SB-USER/100/`，内置资源经 `scripts/seed_system_resources.py`
  从出厂种子包 `resources/` 运行期导入，owner=100、visibility=public）：默认
  只读使用。个人把公共工具/Agent 添加进空间后不做修改，就是直接使用公共
  实现；**修改或删除公共层版本必须是系统身份**（uid=100，经
  `POST /api/auth/act-as-system` 领取短时令牌；超管令牌也不例外，
  `app/api/deps.ensure_can_touch_builtin` 按 registry 合并视图命中层判定，
  非系统身份 403；对话内平台网关同源收口）；
- **个人定制版**：普通用户要改公共资源，先「复制为我的副本」（fork 到私有
  根，registry 条目记 `forked_from` 血缘哈希），此后编辑与执行都命中私有
  副本（读取私有优先），公共版本不受影响；公共层被系统身份升级时按哈希比对
  提示「内置已更新，重新复制」；
- **两层独立**：删除任一层不牵连另一层——删除公共层登记不影响个人定制版
  （合并视图继续命中私有副本），丢弃/删除个人副本回落到直接使用公共版本。
  registry `unregister` 的 scope 三态（system/private/all，system 兼容旧名
  builtin）即此语义的实现。

### 5.1 数据空间 (`<uid>/data/`，公共层 `SB-USER/100/data/`)

- **管理内容**: 多源异构数据（文本/图像/视频/EEG/fMRI/传感器等）的元数据及存储位置，以及 Memory 和 Result
- **记录信息**: 数据格式、Schema、版本、存储路径、血缘关系、生成时间、访问权限
- **资源描述**: 标准化描述（名称、类型、来源、格式、时间范围、质量评分等）
- **可见性**: 数据集是**私有**资源——数据目录指向本机路径，默认 `visibility=private`，按 `owner` 隔离；共享工具经系统 API 访问数据集时在 API 层强制按 owner 过滤

### 5.2 工具空间 (`<uid>/tools/`，公共层 `SB-USER/100/tools/`)

- **管理内容**: 数据处理工具、算法库、存储读写工具、特征提取工具、代码生成工具等（含用户探索生成的本地工具）
- **记录信息**: 工具名称、类型、调用接口定义、版本、代码存储位置、创建时间、使用次数（计数存私有目录 `usage.json`，与 registry 条目分离，读取时注入合并）
- **特殊规则**: 用户探索生成的本地工具经核验后注册入库；register 撞公共层同名 id 时非系统身份被前置拦截（防私有影子遮蔽公共工具），改公共工具须先 fork
- **可见性**: 工具条目默认 `visibility=public`、不做 owner 过滤——公共工具（出厂种子导入系统用户目录）全员可见可用；用户新建的工具落其私有 registry，物理上仅本人可见

### 5.3 模型空间 (`<uid>/models/`，公共层 `SB-USER/100/models/`)

- **管理内容**: 多模态模型（LLM/ViT/3D-CNN/时序模型等）
- **记录信息**: 模型名称、框架类型、输入输出格式、权重文件路径（落库归一为相对资源根路径，避免绝对路径把位置固化在某台机器上）、版本、部署状态、关联工具ID
- **关联关系**: 每个AI模型对应工具空间中的调用工具

### 5.4 智能体空间 (`<uid>/agents/`，公共层 `SB-USER/100/agents/`)

- **管理内容**: 多智能体定义，包括交互Agent、任务Agent、Web服务Agent三类
- **记录信息**: 唯一标识、能力描述（Markdown格式）、角色类型、依赖工具列表、协作协议、健康状态
- **可见性**: 系统身份注册的 Agent `visibility=public`（落系统用户目录，全员可用）；普通用户注册的 Agent `visibility=private`，仅属主可见可用
- **实现位置**: 实现在各资源根的 `agents/implementations/`（非 `core/agent/`，后者是运行时框架）

### 5.5 用户空间 (`SB-USER/_platform/auth/` + `<USER_HOME>/<uid>/`)

- **管理内容**: 本地多用户身份与每用户数据根。身份层在 `core/auth/`：注册/登录（PBKDF2-HMAC-SHA256 密码散列 + HMAC 签名令牌，零第三方依赖）；用户表与令牌密钥是**平台级实体**，存 `<USER_HOME_ROOT>/_platform/auth/users.json` / `secret.key`（0600，可用 `SOTABAND_AUTH_SECRET` 覆盖）。每用户数据根 `<USER_HOME>/<uid>/`（USER_HOME 默认 `/root/SB-USER`，`SOTABAND_HOME` 可覆盖）：platform.db（对话/任务/cron/审计/偏好）、资源空间目录（tools/agents/data/models，扁平布局）、uploads/、工作区清单等
- **三种身份**: 超级用户 uid=0（`.env` 密码登录）之外，还有**系统用户 uid=100**——公共资源（内置数据集/工具/模型/Agent、系统定时任务）的归属层，不可登录、无密码，仅超管经 `POST /api/auth/act-as-system` 领取短时令牌代管，公共层写入一律以系统身份署名
- **两种模式**（由 `.env` 是否配置 `SOTABAND_ROOT_PASSWORD` 决定）：**单用户兼容模式**（未配置）——匿名请求直接以超级用户（uid=0）身份执行，本机打开就用；**多用户模式**（已配置）——除登录/探活外的 `/api` 请求一律要求携带有效令牌，携带无效/过期令牌直接 401，不静默降级
- **账号模型**: 超级用户类比 Linux root——密码只存 `.env`（用户表中有行但无散列，验证走独立通道），uid 固定 `0`，是唯一能管理用户、改系统配置的角色；用户名/显示名存用户表，界面上可改。普通用户 uid 从 1000 起顺序分配、**永不回填**（uid 即用户目录名），只能登录、改自己的密码、保存自己的偏好；uid < 1000 全部是保留位（0=超管、100=系统用户）
- **记录信息**: 用户标识、用户名、显示名、角色（admin/user）、builtin 标记；对话内平台操作按 owner 落各自库的 `chat_ops` 审计流水；租户信息与资源配额为设计目标，尚未实现

### 5.6 任务空间 (`resources/tasks/`)

- **管理内容**: 智能处理流程定义及任务实例的运行时状态（`resources/tasks/` 仍为占位；运行时任务历史已由 `core/observability/task_history.py` 落 `task_history` 表，经 `/api/task/list`、`/api/task/recent-agents` 等接口供任务空间浏览，Agent 运行与对话会话各聚合为一条任务记录）
- **记录信息（目标）**: 流程定义、任务状态（Pending/Running/Done/Failed）、当前步骤、进度、起止时间、关联资源ID列表、执行日志

---

## 六、核心流程

### 6.1 探索式用户交互流程（简化模式，已实现）

```
用户选择数据 → 在前端对话区发出需求
    → 交互 Agent（子进程）构建 System Prompt（角色 + 可用工具/数据上下文）
    → LLM 流式对话理解意图（意图解析由交互 Agent 承担）
    → 检索工具空间
        ├─ 有工具 → ToolExecutor 子进程执行工具 → 结果返回对话
        └─ 无工具 → ToolBuilder 生成代码 → 沙箱自测 + 自动调试 → 用户核验 → 注册入库 → 执行
```

### 6.2 多智能体编排流程（复杂模式，规划中）

```
用户自然语言描述任务
    → 意图解析器解析步骤/依赖/约束
    → 流程编译器查询 Agent/工具/模型资源
    → 生成编排描述文件 (Markdown)
    → 用户交互修改
    → 确认提交
    → 执行器驱动多Agent协同 → 通信总线 → 状态管理 → 观测反馈
```

> 该流程的「自然语言 → 编排文件 → 编译」环节依赖工作流引擎与状态管理模块，尚未实现；当前多 Agent 协同以两种形式落地：「交互 Agent 生成/注册 Web 服务式 Agent，后者组合调用工具对外提供 HTTP API」，以及内置 `orchestrator-agent` 接收声明式步骤、经本地通信总线（4.5）委派给运行中子 Agent 的串行编排（todo 4.2 首版）。

### 6.3 AI模型生命周期

```
注册(模型空间) → 关联(工具空间生成调用工具) → 部署(加载到算力节点)
    → 推理(Agent通过调用工具发起请求) → 更新(新版本,旧版本可回溯) → 废弃(归档)
```

---

## 七、核心设计决策

| # | 决策 | 说明 |
|---|------|------|
| 1 | 探索式交互 | 用户在工作区间选数据、发需求，交互Agent引导全流程 |
| 2 | 工具自动生成 | 无工具时自动编写代码，用户核验后落地为本地工具 |
| 3 | 简化处理模式 | 简单需求确认工具后直接调用执行输出 |
| 4 | 复杂任务编排 | 自然语言驱动自动生成编排描述文件，用户可交互修改 |
| 5 | AI模型双重管理 | 模型空间+工具空间双重建模 |
| 6 | 资源全生命周期 | 所有资源标准化描述、注册登记、核心层统一管理 |

---

## 八、架构优势

- **探索式能力增长**: 用户每次探索产生的工具自动沉淀，工具空间持续增长
- **简化与复杂双模式**: 简单需求直接调用工具快速响应，复杂需求通过多智能体编排完成
- **自然语言驱动**: 用户只需描述需求，系统自动完成工具发现或代码生成
- **人机协同核验**: 自动生成的代码经沙箱自测与用户核验方可落地，确保安全性和正确性
- **多智能体协作**: 三类角色的智能体运行于独立进程，通过进程间协议与消息总线协作
- **密钥安全**: LLM 密钥集中在 `.env` 配置，调试日志与提示词经脱敏处理，工具代码无需硬编码密钥

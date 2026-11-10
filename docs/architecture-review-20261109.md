# 架构评审与工作计划核实（2026-11-09）

> 依据：HEAD `03b073c`（feat/one-command-serve）全量代码核对 + docs/ 全部设计文档逐篇比对。
> 原则：以代码为准（code wins）。文中 file:line 均为本次实际读到的位置。
> 对照基线：`docs/dev-plan-20261103.md`（含 2026-11-04 进度表与新增债务 N1–N4）、
> `docs/security-audit-20261031.md`、`docs/todo-list.md`、`docs/todo-detail.md`、`docs/target.md`。

---

## 一、架构画像（当前代码实况）

### 1.1 分层与模块

```
app/            FastAPI 入口 + HTTP 路由（app/main.py 中间件做身份/防护，13 组 router）
core/           引擎层：agent 工厂与子进程协议、chat_control 网关、executor、
                resource（registry/paths/fork）、auth、user_db、cron、llm、security
config/         settings.py 单文件配置（dataclass 默认值即读 os.getenv）
resources/      出厂种子包（git 分发，运行期不再解析，经 scripts/seed_system_resources.py 导入）
SB-USER/        运行期数据根（默认 /root/SB-USER，SOTABAND_HOME 可覆盖）
frontend/       React + zustand，无 react-router（activeView 驱动），SSE 流式
```

### 1.2 资源模型（与 2026-11-04 前文档已不同）

`core/resource/paths.py`（11-09 改）：运行期解析 `resource_roots() = [私有根, 系统根]`——

- **私有根** = 当前用户家目录 `<USER_HOME>/<uid>/`（扁平布局，**已无 `resources-private/` 中间层**）；
- **系统根** = `<USER_HOME>/100/`（系统用户 uid=100，公共层，owner=100、public）；
- 内置根 `resources/` 降级为出厂种子包，仅导入与 fork 哈希参照（`BUILTIN_ROOT`）；
- 公共层写闸门：只有 act-as-system 换发的 uid=100 短时令牌可写，超管也不例外
  （`app/api/deps.py:54-89` `is_system_identity` / `ensure_can_touch_builtin`）。

平台凭据迁出源码目录：`users.json` / `secret.key` 落 `<USER_HOME_ROOT>/_platform/auth/`
（`core/auth/__init__.py:44-49`、`core/user_paths.py:120`）。

### 1.3 Agent 运行时

全部 Agent 为独立子进程，stdin/stdout JSON-line 协议；主进程是唯一总线路由。
帧类型含 bus/RPC/`platform_call`/`workspace_audit`（`core/agent/protocol.py:23-45`）。
每轮 execute 由主进程注入 `context.user_id`（`app/api/routes/chat_routes.py:216` 取
`get_current_user_id()`），子进程 entrypoint 逐轮覆写 `SOTABAND_USER_ID`
（`core/agent/entrypoint.py:303-307`）。防卡死体系（11-05 起）：轮次中断统一入口、
心跳探活（`AGENT_IDLE_TIMEOUT` 300s）、busy 自动清理重试、`AGENT_ROUND_TIMEOUT` 7200s
兜底（`core/agent/factory.py:1016-1096`）。

### 1.4 工具执行

- **一次性路径**：`ToolExecutor.execute` → `_security_gate`（AST 扫描，执行前复查）→
  临时脚本模板注入安全前导（`sys.addaudithook` + setrlimit，`review_mode` 默认
  **audit**＝只记录不拦截，`config/settings.py:418`）→ 子进程执行，env 为
  `{**os.environ, ...}` **全量继承**（`core/executor/tool_executor.py:405`）。
- **常驻 Worker 路径**：`core/agent/tool_runtime.py` 为 web-service Agent 预热重型工具，
  worker 脚本直接 `exec(tool.py)`（`tool_runtime.py:62`），任务循环无任何闸门。
- 对话内所有工具执行（默认交互 Agent）收敛到网关 `run_tool` + 统一确认门 +
  `workspace_audit` 帧落 `chat_ops`（`resources/agents/implementations/interactive_agent/agent.py:1082-1109`、
  `core/agent/factory.py:188-217`、`core/chat_control/gateway.py:146-167`）。

### 1.5 身份与存储

- contextvar 请求级身份（`core/auth/session.py`）；`get_current_user_id()` 回退链
  = 登录身份 > `SOTABAND_USER_ID` env > "0"，**已显式去除进程级缓存**
  （`core/user_context.py:67-70,107-128`）。
- 每用户一库 `platform.db`，连接与写锁统一收在 `core/user_db.py` `UserDbPool`；
  消费者已达 7 个（chat_store / audit / cron store / task_history / user_prefs /
  user_files 等，grep `UserDbPool` 可证）。仍为进程内 `threading.Lock`，无跨进程互斥
  （单进程部署下可接受）。

---

## 二、缺陷清单（按严重度）

### Critical

**C-1 工具执行无 OS 级隔离，且子进程继承全部密钥（S1/P0-1 原样未动）**
- `config/settings.py:406-407` `sandbox_enabled=True / sandbox_type="docker"` 仍是死配置，
  全仓 grep 无任何消费点。
- `core/executor/tool_executor.py:405` `_launch_env_cwd` 返回 `{**_os.environ, **runtime_env(), ...}`：
  工具子进程继承 `LLM_API_KEY` 等全部环境变量。AST 黑名单可被字符串拼接绕过（
  security-audit-20261031 C4 结论未变）。
- 运行时审计钩子默认 **audit** 模式（只记录不拦截，`settings.py:418`），`enforce` 需手工切换
  且注释自认「静态名单比 code_scan 严，enforce 切换前需先清零内置工具命中」。
- 结论：「注册工具＝本机任意代码执行＋全量密钥」这一条从 10-31 审计到今日没有实质变化。

**C-2 常驻 Worker 路径完全绕过静态闸门与运行时前导（新发现，未列入任何计划）**
- `core/agent/tool_runtime.py:62` worker 启动即 `exec(compile(_TOOL_CODE...))`，
  不经 `ToolExecutor._security_gate`、不带 `_security_preamble`（无 audit hook、无 setrlimit）。
- `core/agent/base.py:206-218` `call_tool` **优先**走常驻 Worker，仅失败才回退 ToolExecutor。
- 影响：web-service 类 Agent 预热的工具即使含阻断级代码也从不被执行前复查；一次性执行
  被拦的代码常驻路径照跑。这是刚上线的常驻特性引入的旁路，dev-plan/todo 均无此项。

**C-3 get-key 族明文密钥 API 仍在线（P0-3 ①实质未闭环）**
- `core/api/registry.json` 仍登记 19 个 API，含 `api-doubao-get-key`、`api-deepseek-get-key`、
  `api-llm-get-config`；实现直接返回明文 `api_key`
  （`core/api/implementations/api_llm.py:501-524`，源头 `config/settings.py:343 get_llm_api_config`）。
- 7 个内置工具实际调用 get-key 族（如 `resources/tools/implementations/paper-summary/tool.py:203`、
  `synthesize-image/tool.py:117` 等）。
- dev-plan 验收「`api-llm-get-key` 从 registry 消失」按字面成立（该 id 确不存在），
  但 get-key **族**一个没下线——验收措辞掩盖了未完成的实质。`docs/api-design.md:226-233`
  也仍以「单机可信前提」为该设计背书；而 C-1 未解决时，「注册工具可执行任意代码」
  与 get-key 叠加，等于任何新装工具可稳定读取平台全部 LLM 密钥。

### High

**H-1 platform_call 帧内 `user_id` 仍可被子进程伪造（P0-2 ①半闭合）**
- 链路：`core/agent/factory.py:229-233` → `gateway.resolve_owner(frame.get("user_id"))`
  → `core/chat_control/gateway.py:44-55`。contextvar 有登录身份时以其为准（HTTP 路径安全），
  但**子进程协议帧路径**上主进程 reader 任务无请求上下文，`current_identity()` 为 None，
  落到 `raw`（帧内自报 user_id）——被注入的 Agent 代码发一枚 `user_id="1001"` 的帧即可
  以他人身份执行平台调用。bus.py:89-91 注释声称「非 Agent 代码自报」，实际该值经
  子进程可写的 stdout 通道传回，信任模型不成立。`workspace_audit` 帧同构（factory.py:202）。
- `role` 语义已闭合：网关按 uid 判 admin（`gateway.py:58-67`），`require_superuser` /
  `current_role` 同一口径（`app/api/deps.py:29-51`）——dev-plan P0-2 ③「role 恒为 user」
  已过时。

**H-2 `InteractiveAgentBase.run_tool` 仍直通 ToolExecutor（P0-2 ②半闭合）**
- 默认交互 Agent（resources 实现）已全部收敛到网关+确认门（agent.py:1082-1109，
  且有 `tests/test_workspace_confirm_gate.py` 回归），这部分 dev-plan 描述已过时。
- 但共享基类 `core/agent/interactive_base.py:316-321` `run_tool → call_tool → ToolExecutor`
  仍不经网关、不落 `chat_ops`；继承者（如 `resources/agents/implementations/city-info-query-agent/agent.py:28`）
  与 task Agent 的 `base.call_tool` 全部走此旁路。「审计点唯一」的文档承诺对非交互
  Agent 不成立。

**H-3 非回环监听 + 未设超管密码的组合仍无启动硬闸（N3 未动）**
- `config/settings.py:502-504` 默认回环、`app/main.py:201-202` 本机监听时拒非回环 Host，
  这些缓解在；但 lifespan（`app/main.py:47-93`）对「`api_host` 非回环 + 未配置
  `SOTABAND_ROOT_PASSWORD`」既不拒绝也不警告，匿名即以 uid=0 超管写 `.env`、管用户。
- 加重项：`scripts/serve.py:17` 帮助文本仍写「默认 0.0.0.0:8201」（实际代码默认
  127.0.0.1，serve.py:258）。dev-plan 小项表宣称「帮助文本与 settings 默认值已对齐为
  127.0.0.1（已修）」——**代码不支持该宣称**，帮助文本反而在教用户绑 0.0.0.0。

**H-4 审计写失败仍静默（P2-1 原样未动）**
- `core/chat_control/audit.py:99-100`：`record` 异常 print 后继续。「结果卡只认审计回执」
  的不变量在写路径（gateway.execute_read/execute_confirmed）没有强制——审计挂了，
  写操作照常执行且无痕。

### Medium

**M-1 LLM 全局单信号量未分池（P1-1 未动）**：`core/llm/client.py:40-41` 默认并发 1，
可经设置动态调整（`set_llm_concurrency`），但 chat/gen/aux 三池未做——一次长生成
仍串行标题摘要与工具匹配。

**M-2 SSE 断线无法续流（P1-3 未动）**：grep chat_routes/factory 无 replay/resume/序号缓冲；
`pending_op` 只恢复确认卡。

**M-3 `.env` 无 0600 加固（P0-3 ③未动）**：`app/api/routes/config_routes.py:180`
`ENV_FILE.write_text(...)` 后无 chmod，也无启动自检警告。对照：`secret.key` 已 0600
（`core/auth/__init__.py:491-493`）。

**M-4 `users.json` 读-改-写无锁（N2 未动）**：`core/auth/__init__.py:208-225` `_load_users`
种入逻辑无进程内锁也无文件锁，`_save_users` 虽走 `write_json_atomic`，并发首访的
「读-判-写」竞态依旧（重复种入 id=0/100 行的问题机制未变）。

**M-5 workspace_tree 整树覆盖写（P2-2 ③未动）**：`app/api/routes/file_routes.py:67-72,98-102`
PUT 全量覆盖，无版本号/乐观检查，多标签页后写覆盖前写。registry 各 store 仍只有
进程内 `_WRITE_LOCK`（grep `threading.Lock`），无 fcntl 文件锁。

**M-6 结构化日志仍未接线（P2-3 未动）**：grep 全仓无 `dictConfig` 消费点，
`config/logging.yaml` 仍是摆设；`ObservabilityConfig` 死配置未清理。

**M-7 `Settings.from_env` 空壳（N4 未动）**：`config/settings.py:531-534` 仍 `return cls()`。

### Low

- **L-1** `docs/agent-design.md:199,464` 私有根仍写 `../resources-private/`，
  `docs/architecture.md:71` 写 `<USER_HOME>/<uid>/resources-private/`；两者均与
  `core/resource/paths.py`（扁平家目录、无该中间层）不符，两文档之间也不一致。
- **L-2** `docs/architecture.md:71,206-208` 仍描述「内置根参与运行期解析」「用户表存
  storage/auth/」；11-05~11-09 的 7 个重构提交（`17f1439`、`6b2af6c`、`e323583` 等）
  把资源根收敛为 [私有, 系统 uid=100]、凭据迁 `_platform/auth`、act-as 闸门换轨，
  文档一字未跟（architecture.md 最后改动停在 11-04）。
- **L-3** `docs/api-design.md` 的「19 个 API」计数当前恰好正确，但含 get-key 族描述
  与安全提示（见 C-3）；`docs/patent-search-tool.md:55` 仍引用 get-key 现状。
- **L-4** dev-plan P1-2 遗留「超时终止后不会自动拉起 Agent」已过时：`factory.py:1072-1077`
  探活失败即强制重启、`_schedule_interrupt(force=True)` 路径统一处理（584c304, 11-05）。

---

## 三、工作计划逐条核实

### 3.1 dev-plan-20261103.md（含 2026-11-04 进度表）

| 计划项 | 表中状态 | 代码实况（2026-11-09） | 结论 |
|---|---|---|---|
| P0-1 沙箱 | ⬜ | 死配置原样；env 全量继承；新增常驻 Worker 旁路（C-2） | 表正确，且**恶化**（新旁路） |
| P0-2 信任边界 | 🔶「role 恒 user、run_tool 直通」 | role 已按 uid 闭合；默认交互 Agent 已走网关+确认门；但帧内 user_id 仍可伪造（H-1）、InteractiveAgentBase.run_tool 仍直通（H-2） | 表**部分过时**：②对主 Agent 已完成，①未闭合，③已完成 |
| P0-3 密钥收敛 | 🔶「get-key 已不存在；scan-directory 仍可任意路径」 | scan-directory 已收敛用户目录并 403 越界（data_routes.py:236-257）✅；file_routes 已无路径接口✅；但 get-key **族**（doubao/deepseek/get-config）在线且有 7 个内置工具调用（C-3）；`.env` 无 chmod | 表**反向过时**：目录项其实已修，密钥项其实没修 |
| P1-1 并发分池 | ⬜ | 单全局信号量（client.py:40） | 表正确 |
| P1-2 流收尾 | 🔶 | 哨兵、心跳探活、busy 自清理、强制重启齐备 | 基本**已完成**，表保守 |
| P1-3 SSE 续流 | ⬜ | 无重放缓冲 | 表正确 |
| P2-1 审计强制 | ⬜ | audit.py:99 仍吞异常 | 表正确 |
| P2-2 写路径收敛 | 🔶 | UserDbPool 已扩到 7 个 store；registry 无跨进程锁；workspace_tree 无乐观检查 | 表方向正确、细节过时 |
| P2-3 日志接线 | ⬜ | 无 dictConfig 消费点 | 表正确 |
| P3-1 契约测试 | 🔶「共 6 个测试文件」 | **23 个测试文件 / 314 条用例**（pytest --collect-only 实测），含路由鉴权矩阵（test_api_authorization.py）、总线越权（test_bus_authorization.py）、确认门回归 | **已完成大半，表严重过时** |
| P3-2 空目录处置 | 🔶「仅剩 __init__」 | tests/{unit,integration,e2e,api} 全部有实体用例 | **已完成，表过时** |
| 小项：serve.py 帮助文本 | ✅「已修」 | serve.py:17 仍写「默认 0.0.0.0:8201」 | **未修，表误报已完成** |
| N1 身份回退缓存 | — | 进程级缓存已删除（user_context.py:67-70）；「回退命中非默认 uid 记 warning」未做 | 主体已修 |
| N2 users.json 竞态 | — | 无锁，未动 | 未修 |
| N3 非回环+无密码硬闸 | — | 未动（H-3） | 未修 |
| N4 from_env 空壳 | — | 未动 | 未修 |

### 3.2 todo-list.md / todo-detail.md / target.md

- **计划错误（依赖/前提）**：todo 系列以「内置根 resources/ + 私有根 resources-private/」
  双根为前提的功能（集市、发布共享、组网）与 11-05 后的 [私有, 系统 uid=100] + act-as
  模型冲突——发布共享现在等价于「act-as 写入系统根」，todo 里所有「共享层」条目需要
  先按新模型重写语义，否则做出来就是双轨。
- **规划外已完成未回写**：workspace 工具统一确认门、workspace_audit 审计帧、act-as-system
  系统身份、资源根收敛、uid 号段规则（<1000 保留、普通用户 1000 起永不回填，
  `core/auth/__init__.py:270-278`）、文件接口全面 file_id 化——均无 todo 条目对应，
  属「实现先于计划文档」。
- **缺失必要项**（应立未立）：
  1. 常驻 Worker 安全闸门（C-2，最紧迫的新增缺陷）；
  2. platform_call / workspace_audit 帧身份去信任化（主进程侧按 agent_id→会话→uid 登记表
     解析，帧内 user_id 仅回显校验）——H-1 的收口方案 dev-plan 写了但没人认领进度；
  3. InteractiveAgentBase / task Agent 的 call_tool 网关化或显式豁免声明——H-2；
  4. get-key 族的替代方案（工具经 `_llm_chat` 代理已有，缺的是把 7 个内置工具迁移后下线 API）；
  5. `.env` chmod、users.json 锁、from_env 删除这三个「一小时工作量」项在两个计划里
     都挂了三周未动，应合并成一个卫生批次。

### 3.3 security-audit-20261031.md

- §十一补记（11-03）的 C8/C6/C7/D3/D4 修复仍成立；file_routes 重构（路径接口全删，
  file_routes.py:1-8 模块注释）超出当时要求，是本期最干净的一块。
- C2（同用户子进程）、C4（黑名单绕过）确实未变，与本文 C-1 一致。
- 该文档「单机可信」前提在 `api_host` 默认回环 + `_browser_guard` 下勉强自洽，
  但 H-3（帮助文本教人绑 0.0.0.0）正在从运维习惯上瓦解这个前提。

---

## 四、一致性结论

1. **文档整体落后代码约 5 天、两个大重构**（资源根收敛 17f1439、act-as 换轨 6b2af6c）。
   architecture.md / agent-design.md 的资源路径模型、凭据位置、公共层写权限三处
   均为错的；dev-plan 的进度表四处与实际相反（两处保守、两处误报）。
2. **代码内注释质量高于文档**：paths.py / user_context.py / session.py / deps.py 的
   模块注释准确解释了当前模型与动机，可作临时事实来源。
3. **安全叙事与实现仍有距离**：「确认卡+审计回执」对默认交互 Agent 已闭环，
   对基类 Agent、task Agent、常驻 Worker、以及伪造帧身份四种路径未闭环。
   「对话内控制可信」目前只对演示主路径成立。
4. todo-list 的产品规划（集市/组网）与刚完成的归属改造存在方向性耦合，建议在任何
   集市工作开始前先做一次架构文档重写冻结新模型。

---

## 五、改进计划（按优先级，均可独立落地）

**第 0 批（卫生，半天，先清零挂了 3 周的小项）**
1. `scripts/serve.py:17` 帮助文本改 127.0.0.1（并全仓 grep 0.0.0.0 残留）。
2. `config_routes.py` 写 `.env` 后 `os.chmod(0o600)` + 启动自检 world-readable 警告。
3. 删除 `Settings.from_env` 空壳（settings.py:531-534）。
4. `lifespan` 增加「非回环监听 + 未设密码」硬性拒绝启动（或 `SOTABAND_ALLOW_INSECURE=1` 显式放行）。

**第 1 批（安全收口，1–2 天）**
5. 常驻 Worker 补闸门：`tool_runtime._start_one` 启动前对 tool.py 跑 `scan_code`，
   worker 脚本模板复用 `_security_preamble`（C-2）。
6. 帧身份去信任化：工厂为每个运行中 Agent 维护「会话→uid」登记（execute 投递时写入），
   `_handle_platform_call` / `_handle_workspace_audit` 按登记表解析 owner，帧内 user_id
   不一致即拒并告警（H-1）；补「子进程伪造 user_id」单测（dev-plan P0-2 验收项，仍缺）。
7. `audit.record` 增加 `strict=True`：网关写路径失败即拒绝执行，只读路径维持宽松
   （H-4，对齐文档承诺）。
8. `InteractiveAgentBase.run_tool` 经 bus 走网关（有总线时），无总线退回现状并在启动
   日志声明该 Agent 不受对话审计（H-2）。

**第 2 批（密钥与可用性，3–5 天）**
9. 迁移 7 个内置 get-key 调用方到 `_llm_chat`/代理通道，下线 `api-*-get-key` 两个 API，
   `api-llm-get-config` 改掩码视图；registry 迁移脚本一份（C-3）。
10. LLM 三池（chat=2/gen=1/aux=4），设置 UI 合并进运行参数区（M-1）。
11. `.env` review_mode 分工具逐步 enforce，先清零内置工具命中（C-1 的过渡形态）。

**第 3 批（文档冻结 + 一致性，1 天）**
12. 重写 architecture.md / agent-design.md 的资源模型、凭据位置、act-as 闸门三节；
    dev-plan 进度表按本文 §3.1 校正（特别是 serve.py 撤回「已修」）。
13. SSE 断线续流（M-2）与 workspace_tree 乐观锁（M-5）并入 todo 排期。

**沙箱（P0-1 本体）** 保持 dev-plan 判断放最后，但第 1 批完成后其验收面显著缩小——
env 白名单化（密钥出进程）可与第 2 批 get-key 下线合并设计：两者本质是同一件事
「密钥不经工具进程」。

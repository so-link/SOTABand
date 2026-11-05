# 8. 用户空间与用户网络 — 文档索引

> 需求编号：8.1 用户注册与登录 / 8.2 用户组网
> 更新日期：2026-11-04 ｜ 状态：**已实现**（功能 + v2 多用户安全加固，见文末修订记录）

---

## 一、文档清单

| 文档 | 内容 | 状态 |
|---|---|---|
| [prd-08-user-space-and-networking.md](prd-08-user-space-and-networking.md) | 产品需求文档（v1.0，既有） | 待评审 |
| [database-selection-research.md](database-selection-research.md) | 数据库选型调研（既有） | 已评审建议 |
| [adr-08-user-space-and-network.md](adr-08-user-space-and-network.md) | **架构决策**：10 项决策 + 对 PRD 的 6 处修订 + 10 项已核实代码冲突 | 已接受 |
| [contract-08-auth-network.md](contract-08-auth-network.md) | **冻结契约**：`AuthContext`、12 张表、HTTP 接口、错误码、权限真值表、授权判定顺序 | v1.0 冻结（+Step 3/5 补充） |
| [design-08-01-auth.md](design-08-01-auth.md) | **8.1 设计**：要解决什么问题、威胁模型、注册登录流程、bcrypt、会话、安全基线 | 待评审 |
| [design-08-isolation.md](design-08-isolation.md) | **数据隔离设计**：四条隔离边界、四检查点同源、404 vs 403、IDOR、撤权、越权测试矩阵 | 待评审 |
| [design-08-schema.md](design-08-schema.md) | **数据表设计**：12 张表分 5 批、逐表字段与约束理由、索引、迁移策略、已知限制 | 已落地 11 张（`publication` 属 Step 5） |
| [design-08-02-network.md](design-08-02-network.md) | **8.2 设计**：网络/邀请状态机、角色、publication 四 scope、Agent 通信授权、撤权 | 待评审 |
| [user-manual-08-user-space-network.md](user-manual-08-user-space-network.md) | **系统使用手册**：部署、注册登录、管理员账号（租户管理员/平台管理员）、界面导览、网络/发布/安装/调用、资源权限与错误码、运维配置 | ✅ 可直接照着用 |
| [verify-08-guide.md](verify-08-guide.md) | **验收手册**：五分钟手工验收、双人协作链路、API 越权直打、自动化测试命令 | ✅ 可直接照着做 |

---

## 二、阅读顺序

**评审者 / 导师** → 依次读 4 份：

1. `design-08-01-auth.md` §1（**要解决什么问题**）——先看问题，再看方案
2. `design-08-isolation.md` §2–§6（**隔离怎么做的**）
3. `design-08-schema.md` §1–§3（**要建几张表**）
4. `design-08-02-network.md` §8（**Agent 通信**）

**下游队友（工具共享 / 数据共享 / Agent 发布 / 集市）** → 只读两份：

1. `contract-08-auth-network.md` §1（`AuthContext`）、§8（接入清单）
2. `design-08-02-network.md` §2（**边界：什么归我、什么归你**）

**接手实现的开发者** → `contract` 全文 + `design-08-schema.md` §3/§6 + `design-08-01-auth.md` §3–§5。

---

## 三、需要评审拍板的两件事

### ① ✅ 已拍板：新增第 12 张表 `installation`（2026-10-26）

PRD 的 11 张表里没有「安装」表，但 FR-N7（**P0**）要求「每次通信记录调用主体」。

**关键裁定依据**：FR-N8 原文是「吊销**或**等待极短有效期结束」—— 所以不建表、
只靠极短 TTL 在 PRD 自己的验收标准下**是合规的**。但审计需要稳定的工作负载主体 id，
所以最终**新增第 12 张表**。

**已落地**：`core/db/models/sharing.py::Installation` + 迁移 `0005_installation_table.py`。
详见 `design-08-02-network.md` §8.1。

### ② 模型空间是否纳入隔离范围

`core/resource/registry/model_registry.py` **完全没有 `owner`/`visibility`**，30 个模型条目全部共享，是四类资源中唯一零隔离的。详见 `design-08-isolation.md` §3。

**建议：本模块不处理**，避免范围蔓延。

---

## 四、已定决策（无需再议）

| 决策 | 取值 | 出处 |
|---|---|---|
| 认证方式 | MVP 自建账号+密码；GitHub OAuth 移至 Phase 2 | ADR-08-01 |
| 密码哈希 | **bcrypt**，`$2b$`，work factor 12；不用 passlib；须处理 72 字节上限 | ADR-08-01a |
| 邮箱验证 | **不做**（找回密码一并推迟） | ADR-08-01b |
| 数据库 | SQLAlchemy 2.0 async + Alembic；开发 SQLite、生产 PostgreSQL | ADR-08-02 |
| 认证路由 | 收敛到 `/api/auth/*`，落在 Vite 已有 `/api` 代理内 | ADR-08-05 |
| 角色词表 | `owner` / `admin` / `member` / `viewer` | ADR-08-03 |
| `visibility` vs `publication` | 两者共存，不做迁移；既有资源行为不变 | ADR-08-08 |
| Agent 通信传输层 | 复用既有 `InProcessMessageBus`，只加授权层 | ADR-08-07 |
| **数据库规范（阿里开发手册）** | **不使用外键与级联**；表名一律**单数**；每表必备 `id`/`gmt_create`/`gmt_modified`；索引命名 `pk_`/`uk_`/`idx_` | ADR-08-11 |
| 主键类型 | **保持 UUID**（有意不采纳手册的 `bigint` 自增 —— PRD §7.3 要求不可猜测 ID） | ADR-08-11 |
| 时间列类型 | `core.db.types.UTCDateTime`（不用裸 `DateTime(timezone=True)`） | ADR-08-11 |

---

## 五、正式开工前需要协调

| # | 事项 | 对象 |
|---|---|---|
| 1 | 共用 Identity/Network 模型，禁止两套账号表 | 7.x 全局服务器负责人 |
| 2 | 改 `app/api/routes/chat_routes.py:104` 的客户端 `user_id` 信任 | 对话模块同学 |
| 3 | 前端 `UserResource.role` 的 `'developer'` 值作废 | 前端同学 |

---

## 六、当前实施进度

| 步骤 | 状态 |
|---|---|
| Step 0 契约定稿 | ✅ 完成 |
| Step 1 持久化底座 | ✅ **完成** —— `core/db/` + Alembic + 批次 1 的 5 张表（**无外键**、表名单数、手册三字段） |
| Step 2 认证闭环 · 后端 | ✅ **完成** —— `/api/auth/*`、`/api/v1/me`、`AuthContext`、中间件、限流、审计；**批次 2 的 2 张表**（租户）一并落地 |
| Step 2 认证闭环 · 前端 | ✅ **完成** —— 登录/注册页、auth 状态机、auth gate、统一 API client、顶栏用户菜单 |
| Step 3 租户切换与加入 | ✅ **完成** —— `tenant_invitation` 表 + `session.active_tenant_id` + 邀请/加入/切换；**8.2 的多用户链路已打通** |
| Step 4 网络 · 后端 | ✅ **完成** —— 3 张表 + 授权层（11 权限码 / 真值表 / 判定链）+ 网络/成员/邀请/状态机 |
| Step 4 网络 · 前端 | ✅ **完成** —— 网络列表/创建、详情、成员与角色、邀请链接、状态机；空间页（成员/邀请/切换） |
| Step 5 共享 · 后端 | ✅ **完成** —— `publication` + `installation` 两张表 + 发布/撤回/网络目录/安装/卸载 |
| Step 5 共享 · 前端 | ✅ **完成** —— 资源目录（发布/安装/撤回）、我的安装（卸载）、权限可见性 |
| Step 5 Agent 工作负载身份 | ✅ **完成（含 3 项遗留）** —— 短期凭证 + 调用授权 + 即时撤权 + 审计 |
| FR-A5 改密码 | ✅ **完成** —— `POST /api/v1/me/password`；改后吊销全部会话；失败限流 |
| FR-N9 审计查询 | ✅ **完成** —— `GET /api/v1/networks/{id}/audit`（`network.audit.read`）+ 前端审计区块 |
| Docker 化 | ✅ **完成** —— 后端 + 前端（Nginx）+ PostgreSQL 三服务 compose |

**当前累计：后端 272 项（SQLite）测试通过，ruff 干净，`alembic check` 干净
（0001–0007，**13 张表 / 0 外键**）；前端 `tsc -b` 通过、oxlint **0 error**、
**裸 `fetch` 归零**（全部走统一 client）；端到端 Playwright **116 项断言**
（登录 17 / 网络 29 / 资源共享 20 / 账号+审计 19 / Agent 调用 16 / 调用策略 15）
+ 文件预览冒烟 10 项全通过；接口总数 **109**。**

### 批次 7：技术债清零 + FR-N7 三项遗留（2026-10-27）

**一、91 处裸 `fetch` → 统一 client（89 处迁移，2 处是 client 自身实现）**

改造前散落在 28 个文件里的裸 `fetch` 有三个问题：不设 `credentials`（认证后永远
"未登录"）、每处各写一遍 `if (!res.ok)`（错误处理规则漂移）、401 无人统一处理
（会话过期后卡在空白页）。

新增分层（`frontend/src/services/api/client.ts`）：

```text
apiFetchRaw   ← 带 Cookie、401 全局回调、网络错误包装。返回原始 Response
  ├─ apiFetch ← 解析 JSON（业务默认）
  ├─ apiText  ← 纯文本
  └─ apiBlob  ← 二进制
apiUrl(path)  ← <img src>/<a href> 这类不走 fetch 的用途
assertOk(res) ← 用 apiFetchRaw 时**必须**先调它（流式接口若忘，错误响应会被当 SSE 解析）
```

迁移中修掉的两个真实缺陷：

1. **`InProcessMessageBus.publish` 会 await handler** —— 见下方 FR-N7 第 3 项；
2. **`ToolDetailView` 的 hooks 在提前 `return` 之后** —— `useCallback` 变成条件调用，
   当选中工具变为空时 React 会抛 "Rendered fewer hooks than expected"。
   oxlint 的 `react-hooks/rules-of-hooks` 抓到了它（**这是预先存在、非本次引入**）。

**二、FR-N7 三项遗留全部补齐**

| 遗留项 | 实现 |
|---|---|
| 白名单粒度（原为网络级） | `publication.invoke_policy` + `invoke_allowlist`（迁移 0007）；授权链新增**第 8 步**按目标校验；`PATCH /publications/{id}/invoke-policy`；前端目录行盾牌图标 + 内联编辑器 |
| 速率限制（原缺失） | 按**调用方 installation** 计数，默认 120 次/60 秒；超限 429 + `Retry-After`；**在授权之前**执行 |
| 消息总线（原未接线） | `core/agent/invoke_bus.py`：授权通过后投递并等响应；响应新增 `delivered` / `delivery_status` / `response`；**离线与超时返回 200 而非错误** |

新增测试：`test_agent_invoke.py` 从 24 项扩到 **39 项**（+15）；
新增 E2E `verify_invoke_policy_ui.py`（15 项，含布局重叠检测）。

### FR-A5 / FR-N9 收尾交付物

```text
core/auth/service.py::change_password      改密码 + 吊销全部会话
core/auth/ratelimit.py::password_change_limiter + SlidingWindowCounter.peek
core/network/service.py::list_network_audit
app/api/routes/me_routes.py                POST /api/v1/me/password
app/api/routes/network_routes.py           GET  /api/v1/networks/{id}/audit
frontend/src/lib/password.ts               密码规则前端唯一副本（注册/改密共用）
frontend/src/components/account/ChangePasswordDialog.tsx
frontend/src/components/center-panel/NetworksView.tsx   +审计日志区块
tests/integration/test_account_and_audit.py            14 项
.planning/project-status/verify_account_audit_ui.py    19 项（含布局检测）
```

**四个决定**：

1. **改密码后吊销「全部」会话**（含当前这条）—— PRD FR-A5 原文如此。代价是改完要
   重新登录，收益是"怀疑密码泄露所以改密"这个最常见动机真正生效。
2. **当前密码错误返回 400 而不是 401** —— 前端对 401 注册了全局登出处理器，
   用 401 会让用户打错一次密码就被踢下线。
3. **改密码也要限流** —— 它同样校验当前密码，持有有效会话的攻击者可以在这里
   爆破且**绕过登录限流**。为此给 `SlidingWindowCounter` 补了只读的 `peek()`
   （`hit()` 会记录，用它做准入判断会让成功请求也消耗配额）。
4. **审计接口不做 COUNT 总数** —— audit_event 只追加，大表上 COUNT 成本高价值低；
   前端用"还有更多就继续拉"的方式。

**顺手修掉一个真实 UI 问题**：改密码弹窗的新密码框里有**两个眼睛图标** ——
浏览器原生的密码显示按钮与我们自定义的按钮叠在一起。在 `index.css` 里用
`input[type='password']::-ms-reveal { display: none }` 隐藏原生按钮。这个问题只有
**看截图**才发现，DOM 重叠检测量不出来（原生伪元素不在 DOM 里）。

### Step 5 前端已交付物（FR-N5 / FR-N6）

```text
frontend/src/services/api/sharing.ts          发布/目录/安装/卸载 API
frontend/src/stores/collaboration-store.ts    +catalog / installations 与对应 action
frontend/src/components/center-panel/NetworksView.tsx
                                              +资源目录区块、+「我的安装」页签
.planning/project-status/verify_sharing_ui.py 20 项断言（含 2 项布局重叠检测）
```

**四个 UX 决定**：

1. **发布表单用 `datalist` 自动补全，不强制从列表选** —— `release_id` 是跨系统
   版本号（数据库无法校验其存在），强制选择会挡住"发布一个还没同步到本机 registry
   的版本"。给候选又不锁死。
2. **「我的安装」独立成页签**，不塞进网络详情 —— 安装是**用户级**的（跨网络），
   挂在某个网络下面会让用户以为"换网络就看不到自己装的东西了"。
3. **权限可见性按真值表推导**：member 默认看不到发布表单（换成一句权限说明），
   看不到撤回按钮，但能看到目录、能安装。
4. **切网络 / 切租户都会清空目录与安装缓存** —— 不清会出现"上一个网络的资源还在"
   的错觉，这是最容易漏的一类状态 bug。

### Step 5 Agent 相互通信已交付物（FR-N7 / FR-N8）

```text
core/auth/workload.py            工作负载凭证：HMAC 短期令牌（默认 10 分钟）
core/sharing/service.py          issue_credential + authorize_agent_invocation
app/api/deps.py                  get_workload_claims（只认 Bearer，不读 Cookie）
app/api/routes/sharing_routes.py POST /installations/{id}/credential 与 /invoke
config/settings.py               WorkloadConfig（secret + ttl）
tests/integration/test_agent_invoke.py   24 项
```

**核心设计：撤权不等凭证过期。** 判定链每一步都**现查数据库**，所以卸载安装 /
撤回发布 / 移除成员 / 降级角色 / 暂停网络之后，**下一次调用立刻失败**。
测试逐条覆盖了这 5 条路径（复用调用前签发的 token，不重新签发）。

**为什么签名令牌不违反「不用 JWT」的既有决策**：
会话令牌是**通行证**（有效性只由令牌决定，必须查库确认没被吊销）；
工作负载令牌是**工牌**（签名只防伪造 installation_id，**不承载任何权限**，
每次调用都重新推导授权）。

**实现在设计之外还多做了一层校验**：除了"同一网络"，还校验「调用方凭证的来源
publication 属于本次请求的网络」—— 否则在 N1 装的 Agent 能拿 N1 凭证去调 N2 的资源。

#### ⚠️ 三项遗留（已在设计文档 §8.3 记录，不影响 P0 验收但要知道）

1. **白名单粒度偏粗**：实现是「目标已发布到该网络即可被网络成员调用」，
   而设计原本设想「目标发布者配置允许哪些 Agent 调用我」。前者防住了"调用网络外
   的东西"，没防住"网络内任意 Agent 互调"。
2. **速率限制未做**：设计 §8.3 第 5 步留空。
3. **未接线消息总线**：接口只做身份与授权判定，不投递消息（PRD §2.4 把传输层
   列为非目标，所以这是刻意的）。

### Step 5 共享侧已交付物

```text
core/db/models/sharing.py        publication + installation（两个批次同文件）
core/sharing/service.py          发布 / 撤回 / 网络目录 / 安装 / 卸载
app/api/routes/sharing_routes.py /api/v1/{resource_type}-releases/*、/network-catalog、/installations
app/api/schemas/sharing_schemas.py
migrations/versions/0006_publication_table.py
tests/integration/test_sharing_api.py   22 项（发布幂等、目录越权、安装私有性）
tests/unit/test_db_foundation.py        +12 项（publication 约束）
```

**两个值得记的实现细节**：

1. **部分唯一索引补口子**。契约给的
   `UNIQUE(resource_type, release_id, scope, scope_id)` 对
   `scope ∈ {private, public}`（`scope_id` 为 NULL）**完全失效** —— SQL 里 NULL
   互不相等，同一版本能被重复发布出多行。补了一条
   `WHERE scope_id IS NULL` 的部分唯一索引（SQLite 3.8+ 与 PG 都支持），
   并写了测试钉住它。
2. **安装列表用 outer join**。`publication` 若被清理，inner join 会让安装记录
   **整条消失** —— 用户看到"我装的东西自己没了"却查不出原因。outer join 后前端
   可以显示"资源已下架"。

### Step 4 前端已交付物

```text
frontend/src/services/api/tenant.ts          租户 API（列表/创建/切换/成员/邀请/接受）
frontend/src/services/api/network.ts         网络 API（CRUD/成员/邀请/状态机/接受）
frontend/src/stores/collaboration-store.ts   租户 + 网络联合状态（切换租户会让网络列表失效）
frontend/src/components/center-panel/NetworksView.tsx   网络列表 + 详情 + 空间页
frontend/src/components/InviteHandler.tsx    接住 ?invite= / ?tenantInvite= 邀请链接
frontend/src/stores/ui-store.ts              + 'networks' 视图
frontend/src/components/topbar/UserMenu.tsx  + 「协作网络与空间」入口
.planning/project-status/verify_network.py   端到端验证（29 项断言）
```

**几个值得记的决定**：

1. **不用参数化标签页**。网络详情做成「左列表 + 右详情」的 master-detail，而不是
   像 Agent 那样每个网络开一个标签页 —— 网络可能很多，开一堆标签是坏体验。
2. **邀请链接真的能用**。令牌明文只在创建时返回一次，前端拼成
   `?invite=<token>` 展示并复制；被邀请人点开后 `InviteHandler` 调接受接口，
   **解析后立刻清掉 URL 参数**（否则刷新会二次提交 → 410）。
3. **权限 → UI 显隐**按契约 §5.2 真值表推导，不让用户"点了才报 403"。
   端到端测试专门断言了普通成员看不到邀请/改角色/归档/移除入口。
4. **登出清空协作状态**用 zustand 订阅 auth 状态实现，而不是在每个登出入口手写
   —— 登出路径有 4 条（主动退出、退出全部设备、401 过期、账号禁用），漏一条
   就会串号。

**端到端测试抓到的真 bug**：`Badge` 组件不透传 `...props`，导致挂在它上面的
`data-testid` 静默消失 —— 测试报"找不到元素"时容易被误判成测试写错。
已修（组件缺陷，不是测试问题）。

### ✅ PostgreSQL 并发复验已完成（Docker）

按 ADR-08-02 的红线做了实际复验（`postgres:16-alpine`，端口 55432）：
4 个迁移全部在 PG 上跑通、11 张表、0 外键、`alembic check` 干净。

**发现并修掉两个真问题**：

1. **驱动必须用 `asyncpg` 而不是 `psycopg`** —— psycopg 的 async 模式不支持
   Windows 默认的 ProactorEventLoop，而 Agent 子进程又必须 Proactor。
   `[postgres]` extra 已改为 `asyncpg`。
2. **「最后一位 Owner」守卫在 PG 上确实是竞态的** —— 实测两个并发移除都通过守卫，
   结果网络 0 位 Owner。修法：成员变更前对 `network` 行加排他锁
   （`core/auth/authorize.py::lock_network()`）。回归测试已验证「去掉锁立即失败」。

**双库结果**：SQLite 169 passed / 3 skipped（并发用例显式跳过，不给假绿）；
PostgreSQL **171 passed / 1 skipped**（SQLite 特有的 Uuid 存储格式用例）。

### ✅ 已解决的依赖顺序问题：8.2 的多用户流程打通了

之前发现的阻碍：注册给每人一个个人租户，而网络属且仅属一个租户 → Bob 无法加入
Alice 的网络，除非先成为 Alice 租户的成员且该租户是他的激活租户。

**Step 3 补上了这两块**：租户邀请（进组织）+ 切换激活租户（在哪个租户下操作）。
现在这条链路**纯 API 可达**，并有集成测试与真实 HTTP 冒烟双重验证：

```text
Bob 注册 → Alice 邀请 Bob 进租户 → Bob 接受 → Bob 切到该租户
→ Alice 建网络 → Alice 邀请 Bob 入网 → Bob 接受 → Bob 读网络（role=member）
```

### Step 3 已交付物

```text
core/db/models/tenant.py            + tenant_invitation（邀请制）
core/db/models/auth.py              + session.active_tenant_id（服务端事实）
core/auth/service.py                + resolve_active_tenant（失效回退）+ get_tenant_membership
core/tenant/{__init__,service}.py   创建租户 / 邀请 / 接受 / 切换 / 成员列表
app/api/routes/tenant_routes.py     /api/v1/tenants/* 与租户邀请接受
app/api/routes/me_routes.py         + POST /api/v1/me/switch-tenant
app/api/schemas/tenant_schemas.py
migrations/versions/0004_*.py
tests/integration/test_tenant_api.py   含端到端多用户链路
```

**为什么用邀请制而不是管理员直接添加**：PRD FR-A6 原文是「**接受**租户邀请」；
直接添加可以在对方不知情时把人拉进组织（缺少同意），且打错邮箱会产生悬空成员。
代价是多一个接受步骤 —— 前端可在接受后立刻调 `switch-tenant` 实现「加入即进入」。

**一个刻意的设计**：创建租户与接受邀请都**不自动切换**激活租户 —— 切换永远是
显式动作（`POST /me/switch-tenant`），API 语义保持单一。

### Step 4 后端已交付物

```text
core/db/models/network.py            network / network_membership / network_invitation
core/auth/permissions.py             11 个权限码 + 角色真值表（契约 §5.2 的代码化）
core/auth/authorize.py               契约 §6 的判定链 + 最后 Owner 计数 + version 推进
core/errors.py                       领域异常（AppError 家族，core 不依赖 FastAPI）
core/network/service.py              网络/成员/邀请/状态机业务规则
app/api/routes/network_routes.py     /api/v1/networks/* 与邀请接受
app/api/schemas/network_schemas.py   请求/响应模型
migrations/versions/0003_networks.py
tests/unit/test_network_permissions.py    真值表（表驱动）
tests/integration/test_network_api.py     隔离、邀请、角色、状态机、并发 CAS
```

**测试抓到的两个真 bug**（都已修）：

1. **归档后无法恢复** —— `authorize()` 的状态门禁把「恢复归档」这个操作本身也挡了
   （归档状态下任何非只读权限都 409，而恢复恰恰需要 `network.archive`）。
   修法：状态管理权限对状态门禁豁免。
2. **退出网络绕过状态检查** —— `leave_network` 直接查网络没走 `authorize`，
   暂停状态下仍能退出。修法：手动补状态判定。

### Step 3 租户切换（下一步）

要做的事（按依赖顺序）：

1. `session` 表加 `active_tenant_id`（可空）—— 迁移 0004；
2. `load_principal` 优先用 `session.active_tenant_id`，为空时回退到主租户；
3. `POST /api/v1/me/switch-tenant`（契约 §3.1）—— 服务端校验 active membership；
4. **租户加入**：现有用户如何进入他人租户（契约只提到「接受租户邀请」，未定义 API，
   需要先补契约）—— 这是打通多人协作的关键一步。

### Step 2 前端已交付物

```text
frontend/src/types/auth.ts                 类型（契约 §1.4 的 camelCase 模型）
frontend/src/services/api/client.ts        统一 API client（credentials / 401 全局回调 / ApiError）
frontend/src/services/api/auth.ts          认证 API + snake→camel 映射
frontend/src/stores/auth-store.ts          auth 状态机（unknown/authenticated/unauthenticated/expired）
frontend/src/views/LoginView.tsx           登录/注册页（按 UTF-8 字节校验密码）
frontend/src/App.tsx                       auth gate（unknown 阶段渲染骨架，避免未登录闪现）
frontend/src/components/topbar/UserMenu.tsx 顶栏用户菜单（显示用户/租户/角色 + 退出）
```

**关键取舍**：

- **不引路由库**。项目原本没有 react-router，为一个登录页引入会带来"守卫怎么写、
  刷新丢路径"等新问题。用状态机在 `App.tsx` 分流更贴合现有风格。
- **`unknown` 阶段渲染骨架屏**，而不是先画主界面再跳走 —— 这是 PRD FR-A7
  「受保护页面无未登录闪现」的直接实现。
- **统一 API client 只覆盖认证链路**。既有 36 处裸 `fetch`（chat/tool/data…）
  暂未迁移：它们走同源代理、Cookie 自动携带，功能不受影响；迁移是独立的
  重构项，不阻塞 8.1 验收。

### Step 2 后端已交付物

```text
core/auth/
  context.py        AuthContext（冻结契约 §1.1）
  passwords.py      bcrypt + 72 字节边界 + 恒定耗时 + needs_rehash
  tokens.py         会话令牌生成与 SHA-256 哈希（明文不落库）
  ratelimit.py      进程内滑动窗口（账号维度 + IP 维度）
  cookies.py        Set-Cookie / 清除（属性一致性）
  audit.py          审计事件写入
  service.py        注册 / 登录 / 会话校验与吊销
core/db/models/tenant.py                tenant / tenant_membership（批次 2）
app/api/deps.py                         get_auth_context / get_optional_auth / require_role
app/api/middleware/request_context.py   request_id 纯 ASGI 中间件
app/api/routes/auth_routes.py           /api/auth/*
app/api/routes/me_routes.py             /api/v1/me*
app/api/schemas/auth_schemas.py         请求/响应模型
migrations/versions/0002_tenants.py
tests/unit/test_auth_core.py            密码/令牌/限流
tests/integration/test_auth_api.py      端到端 HTTP 契约
```

**为什么批次 2（租户）提前到 Step 2**：冻结契约里 `AuthContext.tenant_id` 是**必填**
字段，而注册时就要自动创建个人租户。若 Step 2 不给 `tenant_id`，就是破坏性变更 ——
下游按契约写好代码后要返工。宁可把两张表提前，也不让已冻结的契约打折。

### Step 1 已交付物

```text
core/db/__init__.py          包导出
core/db/base.py              DeclarativeBase + 命名约定 + utcnow
                             + UUIDPrimaryKeyMixin / TimestampMixin（手册三字段）
core/db/types.py             UTCDateTime（保证读写都是 aware UTC）
core/db/session.py           异步引擎、会话工厂、SQLite PRAGMA
core/db/models/auth.py       user / user_identity / user_credential / session
core/db/models/audit.py      audit_event
alembic.ini, migrations/     Alembic 配置与 0001 迁移
tests/unit/test_db_*.py      数据库底座测试
tests/unit/test_password_hashing.py   bcrypt 72 字节边界
```

启用方式：`DATABASE_URL` 环境变量；默认 `sqlite+aiosqlite:///./storage/sotaband.db`，
零安装即可运行。切换 PostgreSQL 只需改这一个变量并 `pip install -e ".[postgres]"`。

**本次改造不触碰任何既有数据**——48 个工具、30 个模型、数据集的既有行为不变
（ADR-08-08）。没有数据迁移任务，只有建表。

---

## 十、v2 修订记录（2026-11-04，评审后安全加固）

> 状态：**已实现**（8.1/8.2 功能 + 多用户安全加固）；合并 upstream/main 后
> 后端 553 passed / 3 skipped，前端 tsc/build 通过。

| 修订 | 内容 | 权威文档 |
|---|---|---|
| 身份统一 | owner 一律 users.id UUID；`current_owner_id()` 三层解析、env 不缓存；执行链显式下传至工具子进程/常驻 Worker/系统 API/migrate | [design-08-isolation.md §12.1](design-08-isolation.md)、[design-08-01-auth.md §9](design-08-01-auth.md) |
| 认证基线（D1） | 全部业务 API 需登录；仅 `/api/auth/*`、`/api/health` 公开 | [contract-08-auth-network.md](contract-08-auth-network.md) §A |
| 资源写权限（D2） | owner 或租户 Owner/Admin；内置仅租户管理员；普通成员互不编辑 | 同上 §B；`core/auth/ownership.py` |
| 发布归属 | 发布前校验资源存在性与归属；拒绝 404 + 审计 | [design-08-02-network.md](design-08-02-network.md) v2 节；契约 §C |
| 文件访问边界（D3） | 允许根 + 敏感目录黑名单；上传文件名净化/大小限制；数据集注册路径校验 | [design-08-isolation.md §12.2](design-08-isolation.md) |
| 工作区隔离 | 工具空间/文件树按 user_id 命名空间存储 | 同上 §12.2 |
| 迁移包归属（D4） | 导入 owner=导入者 UUID；`exported_by` 溯源；包可见性按导出者；overwrite 权限 | 同上 §12.2 |
| 审计补全 | 代码/规范变更、删除、数据集/文件删除、LLM 配置、claim-local、迁移导入导出 | 同上 §12.4 |
| 验收 | 新增安全加固验收 16 条 | [acceptance-08-submission.md](acceptance-08-submission.md) §7 |
| v2 复审补强 | 文件接口补「归属」维度（他人数据集/上传不可读写删；registry 等元数据禁删）；register/upsert 禁止接管他人资源；写守卫覆盖 refine/register/运行控制；导出需归属；chat 停止按用户隔离；Agent Web 令牌 fail-closed | 同 §12.2 / 契约 §B·§C |

| v3 终审补强 | Agent 通道并发隔离/Web 服务身份透传/任务归属；数据集删除边界+迁移包盘符 slip；config .env 注入与 LLM 重定向 SSRF；发布撤权/幂等/代发/删除撤发布/凭证链路；网络最后一位 Owner 在 SQLite 的并发不变量；平台管理员口径 | 契约 §G、isolation §12.6 |

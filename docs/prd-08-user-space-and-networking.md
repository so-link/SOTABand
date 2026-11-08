# SOTABand 产品需求文档（PRD）：用户空间与用户网络

> 需求编号：8.1 用户注册与登录 / 8.2 用户组网
> 产品：SOTABand（优智联邦）— 多智能体驱动的多模态智能处理引擎
> 版本：v1.0 ｜ 状态：待评审 ｜ 文档类型：产品需求文档（PRD）
> 关联需求：7.1 全局服务器·用户注册与登录（服务端视角）、7.2 全局服务器·局部网络构建（服务端视角）、4.6 Agent 发布、9.4 工具共享、10.1 数据共享

---

## 1. 需求原文与解读

| 编号 | 需求原文 | 本 PRD 解读 |
|---|---|---|
| 8.1 | 用户注册与登录 | 建立可信用户身份体系：注册/登录、会话管理、当前用户上下文、账号安全与租户归属，支撑后续所有资源的对象级授权 |
| 8.2 | 用户组网：构成虚拟局部网络，用户可在网络中共享工具、数据，Agent 之间可相互通信 | 在平台内构建**虚拟协作网络**（成员、角色、邀请、资源共享、Agent 间通信授权），而非实现 VPN/TCP/IP/P2P 等物理组网 |

一句话定位：**8.1 回答「你是谁」，8.2 回答「你属于哪个协作网络」，两者共同构成资源可见性、共享与调用的授权底座。**

```text
8.1 认证（你是谁）
  → 租户上下文（你代表哪个组织）
  → 8.2 组网（你属于哪个协作网络）
  → 资源授权（你能发现、安装、调用什么）
```

---

## 2. 背景与目标

### 2.1 项目背景

SOTABand 是一个多智能体驱动的多模态智能处理引擎，资源层包含六大空间：数据、工具、模型、智能体、用户、任务。当前系统已具备工具自动生成、Agent 编排、资源注册等核心能力，但**用户空间与用户网络尚未建立**。

### 2.2 现状差距（Gap Analysis）

| 现状 | 问题 |
|---|---|
| 无用户、凭证、会话、租户、网络、成员、邀请等持久化模型 | 无法回答「谁在操作」 |
| ChatRequest 的 user_id / session_id 由客户端提交，默认值 default | 客户端声明身份不可信，无法作为授权主体 |
| Agent、Tool、Data 接口未按用户/租户/网络过滤 | 无隔离，任何人可访问任何资源 |
| CORS 已允许 credentials 但来源未收敛 | 无法安全使用 Cookie 会话 |
| 资源使用 JSON registry 管理 | 无法表达成员关系、唯一约束、并发邀请、权限查询与审计 |
| 前端仅有 UserResource 类型与 mock 用户 | 界面草图级，无真实登录流程 |
| Agent 仅进程内通信雏形 | 无法表达跨用户网络的通信授权 |

**结论：不能从「画一个登录页」或「给资源加一个 network_id 字段」开始，第一步必须建立可信身份、关系模型与统一授权入口。**

### 2.3 产品目标

1. 建立**可信身份体系**：服务端生成认证上下文，不再信任客户端提交的 user_id；
2. 建立**租户上下文**：用户归属租户并可切换，所有资源绑定租户；
3. 建立**虚拟协作网络**：创建、邀请、加入、成员角色、资源共享与撤权；
4. 打通**资源共享闭环**：用户在网络中共享工具、数据、Agent，成员按策略发现/安装/调用；
5. 支撑**Agent 间通信**：网络内 Agent 以独立短期工作负载身份相互通信，可审计可撤权；
6. 全程**审计可追溯**：认证、组网、共享、通信关键事件均可通过 request_id 追踪。

### 2.4 非目标（Non-Goals）

- 不实现 VPN、SDN、P2P 打洞、局域网广播等物理网络能力；
- 不实现 Agent 底层通信协议（RPC/消息总线传输层），仅定义网络内通信的**身份与授权**；
- MVP 不支持跨租户网络联盟、嵌套网络、自定义角色、企业 MFA/联邦；
- 不自研复杂账号安全能力（如强制 MFA），优先采用成熟方案。

---

## 3. 术语定义

| 术语 | 定义 |
|---|---|
| 用户（User） | 平台账号主体，具有唯一主键，可属于一个或多个租户 |
| 租户（Tenant） | 资源与成员的归属组织边界；MVP 下网络属于租户 |
| 网络（Network） | 平台内虚拟协作网络：成员关系 + 角色 + 共享资源 + 策略 |
| 成员（Membership） | 用户与网络的关系记录，带角色与状态 |
| 邀请（Invitation） | 通过邮箱/已有用户发起的入网凭证，短期、一次性 |
| 发布（Publication） | 资源版本向某个 scope（private/tenant/network/public）的授权发布记录 |
| 安装（Installation） | 成员将网络共享的 Agent/Tool/Data 安装到自己的工作空间 |
| 工作负载身份（Workload Identity） | Agent 运行时实例使用的独立短期凭证，不复用用户长期 Token |
| 撤权（Revocation） | 成员退出/被移除后，目录、安装、调用、缓存、凭证、长连接的即时或限时失效 |
| AuthContext | 认证中间件生成的服务端上下文：user_id、session_id、active_tenant_id、tenant_role、request_id |

---

## 4. 用户与场景

### 4.1 用户画像

| 画像 | 描述 |
|---|---|
| 平台用户 | 注册登录 SOTABand，创建/加入租户与网络，共享与使用工具/数据/Agent |
| 网络所有者（Owner） | 创建网络，管理成员与角色，管理共享资源 |
| 网络管理员（Admin） | 协助管理成员与发布资源 |
| 网络成员（Member） | 使用网络资源，按策略发布/调用 |
| 网络访客（Viewer） | 仅可浏览网络目录，默认不可调用 |
| Agent 实例 | 网络内运行的工作负载，以独立身份与其他 Agent 通信 |

### 4.2 MVP 核心用户故事

1. Alice 注册/登录并进入个人租户 T1；
2. Alice 创建网络 N1，自动成为 Owner；
3. Alice 邀请 Bob，Bob 接受邀请成为 Member；
4. Alice（或获授权成员）将 Agent/Tool/Data 发布到 N1；
5. Bob 能在网络目录中搜索、查看、安装或按策略调用这些资源；
6. Carol 属于 T1 但不在 N1 中：列表、详情、安装、调用均被拒绝；
7. 网络内 Agent A 可调用 Agent B 提供的工具/数据，调用使用独立工作负载身份并被审计；
8. Bob 退出或被移除后，新访问立即失败，缓存、运行凭证在目标时间内失效；
9. 审计中可追踪谁邀请了谁、谁发布/撤回了什么、Agent 间调用了什么。

### 4.3 用户旅程总览

```text
注册/登录 → 选择租户 → 创建/加入网络
  → 查看成员与角色 → 发布资源到网络
  → 成员发现/安装/调用 → Agent 网络内通信
  → 退出/被移除 → 撤权
```

---

## 5. 功能需求详述

### 5.1 8.1 用户注册与登录

#### FR-A1 用户注册（P0）

- **描述**：**已确定接入 GitHub 登录**：用户点击「Sign in with GitHub」经 Authorization Code + PKCE 流程授权，首次登录自动创建本地用户并生成个人租户；自建邮箱密码注册仅作为可选后备，不进入 MVP。
- **业务规则**：
  - GitHub 路径：以 (issuer=github, subject=GitHub 用户数字 ID) 作为外部身份主键映射本地用户，不以邮箱作为外部身份主键；昵称与头像取 GitHub，邮箱取 GitHub 已验证主邮箱（用户未公开邮箱时留空并提示补充）；
  - 邮箱规范化后唯一存储；
  - 自建密码路径：密码 Argon2id 自适应哈希、邮箱验证、密码强度策略；
  - 注册成功即创建个人租户并成为租户 Owner；
  - 注册/登录/重置分别独立限流，失败响应不泄露「邮箱是否存在」（防用户枚举）。
- **验收标准**：重复邮箱注册被拒绝且响应一致；并发注册仅创建一个账号；OIDC 首次登录自动建号且二次登录映射到同一用户。

#### FR-A2 用户登录（P0）

- **描述**：浏览器点击「Sign in with GitHub」→ 跳转 GitHub 授权页 → 回调后端（GitHub OAuth App，Authorization Code + PKCE）。
- **业务规则**：
  - 校验 state（防 CSRF）、PKCE code_verifier，redirect URI 与 GitHub OAuth App 注册值精确匹配；
  - 授权码换 access token 后调用 GitHub /user 与 /user/emails 获取身份信息；
  - 登录成功后发放 HttpOnly / Secure / SameSite 的随机 opaque 会话 Cookie（服务端只存 token hash）；
  - 登录失败限流，达到阈值临时锁定并告警；
  - 禁用用户、未验证邮箱用户登录被明确拒绝。
- **验收标准**：state 复用、篡改 redirect_uri、伪造授权码均失败；Cookie 属性正确；连续失败触发限流。

#### FR-A3 会话管理与当前用户（P0）

- **描述**：GET /api/v1/me 返回当前用户、会话与激活租户信息；支持会话列表与单设备撤销。
- **业务规则**：
  - 会话含 token hash、过期时间、撤销状态，支持全局退出与单设备退出；
  - 修改密码/禁用用户/退出全部设备后，既有会话全部吊销；
  - 浏览器端不保存长期 Token 到 localStorage；CLI/服务调用使用 Bearer Token 并校验 issuer/audience/signature/expiry/scope。
- **验收标准**：过期/吊销/伪造会话均返回 401；/me 恢复会话正确；多标签页退出状态同步。

#### FR-A4 退出登录（P0）

- **描述**：退出当前设备；账号中心支持「退出全部设备」。
- **业务规则**：退出后服务端会话立即吊销；前端清空认证状态并跳转登录页，return URL 仅允许站内路径（防开放重定向）。
- **验收标准**：退出后刷新页面仍为未登录态；被吊销会话无法继续调用 API。

#### FR-A5 账号安全与审计（P1）

- **描述**：用户禁用、密码重置（自建路径）、认证事件审计。
- **业务规则**：
  - 禁用用户后新请求立即被拒，存量会话吊销；
  - 重置 Token 短期、一次性、数据库只存 hash；
  - 审计记录 login succeeded/failed、session created/revoked 等事件，日志不含密码、Cookie、Token、验证码。
- **验收标准**：禁用用户旧会话立即失效；重置 Token 重放被拒；日志脱敏检查通过。

#### FR-A6 租户上下文与切换（P0）

- **描述**：用户查看所属租户，切换当前激活租户；支持创建新租户与接受租户邀请。
- **业务规则**：
  - 切换租户必须由服务端校验 active membership，客户端提交的 tenant_id 只是意向；
  - 所有资源操作携带并校验 active_tenant_id；
  - 切换失败时保持原租户上下文，不允许仅改前端状态。
- **验收标准**：切换到非所属租户返回 403 且前端不显示目标租户缓存内容；跨租户隔离测试通过。

#### FR-A7 前端认证体验（P0）

- **描述**：GitHub 登录入口、授权回调（callback/loading/error 状态）、当前用户头像与账号状态、租户切换、会话过期引导。
- **业务规则**：
  - Auth 状态机：unknown / authenticated / unauthenticated / expired / disabled，unknown 时不渲染受保护内容；
  - API client 统一处理 401/403：401 触发一次会话恢复或跳登录，403 显示权限不足，不无限刷新；
  - 401 与 403 呈现不同但不泄露资源存在性；
  - callback 页面重复刷新/重复打开安全（state/code 一次性）；
  - 会话过期保留安全 return URL 与可恢复草稿，不重放高风险写操作。
- **验收标准**：受保护页面无未登录闪现；开放重定向用例全失败；键盘/焦点/移动端可用；E2E 与后端契约一致。

### 5.2 8.2 用户组网

#### FR-N1 网络创建（P0）

- **描述**：用户创建虚拟网络，自动成为 Owner，网络归属当前租户。
- **业务规则**：
  - MVP 一个网络属于一个租户；网络名称租户内唯一；
  - 创建者自动成为唯一 Owner；初始状态 active；
  - 网络策略可配置：谁可邀请、发布、安装、调用（默认 Owner/Admin 可邀请与发布）。
- **验收标准**：创建者成为唯一 Owner；重名网络被拒；网络绑定正确租户。

#### FR-N2 网络生命周期（P1）

- **描述**：网络状态机 active / suspended / archived；支持修改信息、暂停、归档。
- **业务规则**：
  - archived 后所有写操作停止，历史审计保留；
  - 网络停用后邀请不可接受、发布不可新增；
  - 归档与恢复仅 Owner/Admin 可操作。
- **验收标准**：归档后邀请/发布/调用被拒，恢复后按策略继续。

#### FR-N3 邀请与加入（P0）

- **描述**：Owner/Admin 邀请邮箱或已有用户加入网络；受邀人接受/拒绝；支持撤销与过期。
- **业务规则**：
  - 邀请状态机：pending / accepted / rejected / expired / revoked；
  - 邀请 Token 唯一、短期、一次性，数据库只存 hash；
  - 重复邀请幂等；已是成员返回一致结果；并发接受只生成一条 membership；
  - 邀请被撤销、网络停用、Token 过期后不可接受；
  - 邀请邮箱与登录账号匹配校验。
- **验收标准**：同一 Token 二次使用被拒；并发接受仅一条成员关系；过期/撤销邀请接受失败。

#### FR-N4 成员与角色管理（P0）

- **描述**：成员列表、角色调整、成员移除、主动退出。
- **业务规则**：
  - 角色：Owner / Admin / Member / Viewer；
  - 普通成员不能邀请、不能改角色、不能移除他人；
  - 最后一位 Owner 不可被移除/退出（事务内锁定检查）；
  - 角色调整使用版本号/条件更新，冲突返回 409；
  - 成员退出/被移除后触发撤权（见 FR-N8）。
- **验收标准**：越权改角色失败；并发改角色仅一个成功；删除最后 Owner 被拒。

#### FR-N5 网络资源共享（P0）

- **描述**：将 Tool / Data / Agent 的固定版本发布到网络；支持撤回。
- **业务规则**：
  - 共享通过独立 publication 表表达，scope 为 private / tenant / network / public，而非资源上一个 is_public 布尔字段；
  - 同一版本可发布到多个网络，每个 publication 独立状态、时间、审核与撤回记录；
  - 发布范围选择网络时校验发布者网络角色与策略；
  - 撤回 publication 后目录不可见，但不影响历史安装与审计。
- **验收标准**：同一 release 发布两个网络互不影响；撤回单一网络 publication 后另一网络仍可见。

#### FR-N6 网络资源目录（P0）

- **描述**：成员浏览网络内共享的 Agent/Tool/Data，查看详情、安装、调用。
- **业务规则**：
  - 列表、详情、安装、调用必须**复用同一授权策略**（详情接口同样校验，防 IDOR）；
  - 可见性规则：存在 active publication 且 scope 匹配成员关系（network 成员 / tenant 成员 / public）；
  - can_install、can_invoke 为额外权限，不简单等同 can_view；
  - 对敏感私有资源统一返回 404 避免泄露存在性；
  - 展示资源的固定版本与访问模式。
- **验收标准**：Carol（同租户非成员）在列表/详情/安装/调用全部被拒；猜 ID 访问失败。

#### FR-N7 网络内 Agent 相互通信（P0）

- **描述**：网络内 Agent 实例可以相互通信、调用对方提供的工具与数据；用户成员身份与 Agent 运行身份分离。
- **业务规则**：
  - 用户成员身份表示「某人可管理/使用网络资源」，Agent 运行身份表示「某运行实例可调用什么」，二者**不复用个人长期 Token**；
  - 网络内 Agent 通信使用独立 principal + 短期工作负载凭证 + 最小 scope；
  - 目标 Agent、Tool、Data 白名单校验；
  - 每次通信记录调用主体、目标、权限与结果审计；支持速率限制；
  - 成员退出、安装停用或密钥轮换后，相关 Agent 凭证被吊销或在短期有效期后失效；
  - MVP 复用现有进程内/本地消息机制承载通信，仅新增身份与授权层；底层协议不实现。
- **验收标准**：Agent A 仅能调用白名单内目标；成员移除后 Agent 通信在 SLA 内被拒；审计可还原完整调用链。

#### FR-N8 撤权（P0）

- **描述**：成员退出/被移除、网络停用、publication 撤回后，权限按规则失效。
- **业务规则**：

| 对象 | MVP 规则 |
|---|---|
| 目录与详情 | 立即不可见 |
| 新安装/调用 | 立即拒绝 |
| 权限缓存 | 主动失效 + 短 TTL 兜底 |
| Agent 短期凭证 | 吊销或等待极短有效期结束 |
| WebSocket/SSE 长连接 | 重新认证或断开受保护连接 |
| 排队任务 | 执行前再次校验权限 |
| 运行中任务 | 按数据风险选择终止或完成当前原子步骤 |
| 网络私有数据/密钥 | 立即停止访问 |
| 历史安装与审计 | 保留，避免破坏追踪 |

- **验收标准**：成员退出后 API 立即（或目标 SLA 内）拒绝；前端缓存页面即时清理；Agent 凭证失效时间达标。

#### FR-N9 网络策略与审计（P1）

- **描述**：网络策略配置与全量审计。
- **业务规则**：
  - 策略项：谁可邀请、发布、安装、调用、读审计；
  - 审计记录 actor、tenant、network、action、target、result、request_id；
  - 管理员查询同样留痕，不成为无痕绕过入口。
- **验收标准**：越权尝试、误拒绝均可在审计中定位。

---

## 6. 角色与权限矩阵

| 角色 | 网络管理 | 邀请成员 | 发布资源 | 查看/安装 | 调用 | 读审计 |
|---|---|---|---|---|---|---|
| Owner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Admin | 部分 | ✅ | ✅ | ✅ | ✅ | ✅ |
| Member | ❌ | ❌ | 按策略 | ✅ | ✅ | ❌ |
| Viewer | ❌ | ❌ | ❌ | ✅ | 默认否 | ❌ |
| 非成员（同租户） | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

角色映射 permission codes（后续扩展自定义权限的基础）：

```text
network.members.manage    成员与角色管理
network.invite.create     发起邀请
network.resource.publish  发布资源到网络
network.resource.install  安装网络资源
network.agent.invoke      调用 Agent
network.audit.read        查看网络审计
```

---

## 7. 数据模型设计

### 7.1 ER 概览

```text
users 1─n user_identities      (issuer, subject) 唯一
users 1─n sessions             token 只存 hash
users n─m tenants              tenant_memberships (tenant_id, user_id) 唯一
tenants 1─n networks           owner 属于租户，名称租户内唯一
networks 1─n network_memberships (network_id, user_id) 唯一
networks 1─n network_invitations token_hash 唯一，有效邀请去重
releases 1─n publications      scope + scope_id check constraint
一切写操作 → audit_events      actor/tenant/network/action/target/result/request_id
```

### 7.2 核心表

| 表 | 关键字段 | 关键约束 |
|---|---|---|
| users | id、email、display_name、status | 规范化 email 唯一 |
| user_identities | user_id、issuer、subject | (issuer, subject) 唯一 |
| user_credentials | user_id、password_hash、algorithm、changed_at | 仅自建密码路径 |
| sessions | id、user_id、token_hash、expires_at、revoked_at | token 只存 hash，到期清理索引 |
| tenants | id、name、slug、status | slug 唯一 |
| tenant_memberships | tenant_id、user_id、role、status | (tenant_id, user_id) 唯一 |
| networks | id、tenant_id、name、owner_id、status、policy | 名称租户内唯一，owner 属于租户 |
| network_memberships | network_id、user_id、role、status、joined_at、version | (network_id, user_id) 唯一 |
| network_invitations | network_id、email、token_hash、status、expires_at、invited_by | token_hash 唯一，有效邀请去重 |
| publications | resource_type、release_id、scope、scope_id、status | scope=network 时 scope_id 必填 |
| audit_events | actor_id、tenant_id、action、target、result、request_id | 保留期独立于成员删除 |

### 7.3 数据库设计原则

- 用户/租户/网络/资源使用不可猜测 ID（UUID），但 UUID 不能替代授权；
- 所有租户资源显式携带 tenant_id；
- 关系表联合唯一约束防并发重复；
- 状态迁移代替物理删除（关键记录软删除）；
- 普通应用连接不持有绕过 RLS 的权限，RLS 作为纵深防御，授权仍在应用层判定；
- 数据库选型：**FastAPI 模块化单体 + PostgreSQL 16+（含 pgvector 扩展，向量检索在 Phase 5 按需启用）**；SQLite 仅用于本地零配置开发；Redis、消息队列（outbox 通知）在有明确规模证据后引入。详细论证见《数据库与向量检索选型调研》（docs/database-selection-research.md）。

---

## 8. API 契约

### 8.1 身份与租户

```text
GET    /auth/login                        发起 OIDC 登录（跳转）
GET    /auth/callback                     OIDC 回调（code + PKCE）
POST   /auth/logout                       退出当前设备
GET    /api/v1/me                         当前用户 + 激活租户
GET    /api/v1/me/sessions                会话列表
DELETE /api/v1/me/sessions/{session_id}   吊销指定会话
GET    /api/v1/me/tenants                 所属租户列表
POST   /api/v1/tenants                    创建租户
POST   /api/v1/me/switch-tenant           切换激活租户（服务端校验）
```

自建密码路径补充：/auth/register、/auth/password/forgot、/auth/password/reset（分别限流）。

### 8.2 网络

```text
POST   /api/v1/networks                           创建网络
GET    /api/v1/networks                           我的网络列表
GET    /api/v1/networks/{network_id}              网络详情
PATCH  /api/v1/networks/{network_id}              更新/暂停/归档
POST   /api/v1/networks/{network_id}/invitations  发起邀请
GET    /api/v1/networks/{network_id}/invitations  邀请列表
DELETE /api/v1/networks/{network_id}/invitations/{invitation_id}  撤销邀请
POST   /api/v1/network-invitations/{token}/accept 接受邀请
GET    /api/v1/networks/{network_id}/members      成员列表
PATCH  /api/v1/networks/{network_id}/members/{user_id}   调整角色
DELETE /api/v1/networks/{network_id}/members/{user_id}   移除成员
POST   /api/v1/networks/{network_id}/leave        主动退出
```

### 8.3 资源共享与调用

```text
POST /api/v1/agent-releases/{release_id}/publications   发布 Agent 到网络/租户/公开
POST /api/v1/tool-releases/{release_id}/publications    发布工具
POST /api/v1/data-releases/{release_id}/publications    发布数据
GET  /api/v1/network-catalog?network_id=...             网络资源目录
GET  /api/v1/network-catalog/{resource_type}/{release_id}  资源详情
POST /api/v1/agent-releases/{release_id}/installations  安装
POST /api/v1/agent-installations/{id}/invoke            调用
```

> ⚠️ 列表、详情、安装、调用必须复用同一授权策略。只在目录列表过滤、详情接口可猜 ID 访问，是典型 IDOR 漏洞。

### 8.4 统一错误码

| 状态码 | 语义 | 前端行为 |
|---|---|---|
| 401 | 未认证/会话过期 | 会话恢复或跳登录，保留安全 return URL |
| 403 | 已认证但无权限 | 显示权限不足，不泄露资源存在性 |
| 404 | 不存在或无权限的敏感资源 | 与 403 策略一致 |
| 409 | 并发冲突（角色、最后 Owner、重复邀请） | 提示刷新重试 |
| 410 | 邀请已过期/已使用 | 明确引导 |

---

## 9. 关键流程

### 9.1 登录（OIDC + BFF 服务端会话）

```text
浏览器 → BFF GET /auth/login → 跳转 IdP（Authorization Code + PKCE）
浏览器 → IdP 完成登录/验证 → 回调 BFF（code + state）
BFF → IdP 用 code + verifier 换 token
BFF → PostgreSQL 按 (issuer, subject) 映射本地用户
BFF → 浏览器 下发 HttpOnly/Secure/SameSite opaque 会话 Cookie
浏览器 → 后续 API 携带 Cookie → BFF 生成 AuthContext → 业务授权
```

### 9.2 邀请与接受

```text
Alice → POST /networks/{N1}/invitations (email=bob) → 落库 token_hash（一次性、短期）
Bob → 接受链接 → POST /network-invitations/{token}/accept
事务内：校验 token hash → 校验邮箱匹配 → 校验网络 active → 校验唯一 membership → 写入成员
```

### 9.3 网络内 Agent 通信

```text
成员将 Agent 安装到网络 → 安装生成短期工作负载凭证（独立 principal、最小 scope、目标白名单）
Agent A → 调用 Agent B（携带工作负载凭证）
授权层：校验凭证有效性 → 校验 A 与 B 均属 N1 → 校验 B 对 A 的调用白名单 → 记录审计
成员退出/安装停用 → 凭证吊销，通信在 SLA 内被拒
```

### 9.4 授权判定顺序（每次网络资源操作）

1. 请求者已认证 → 2. Session 有效未吊销 → 3. active tenant 存在 → 4. 租户 active member → 5. network 属当前租户且 active → 6. 网络 active member → 7. 角色拥有 permission → 8. resource 属合法租户 → 9. publication 允许该动作 → 10. 配额/限流/审批通过。**未命中允许规则默认拒绝。**

---

## 10. 安全需求

- 密码不明文、不使用快速散列；OIDC 使用 Authorization Code + PKCE，redirect URI 精确匹配；
- Cookie HttpOnly/Secure/SameSite，Cookie 写请求 CSRF 防护；
- 登录、注册、验证码、重置分别限流；登录失败不泄露用户枚举差异；
- Session、重置 Token、邀请 Token 数据库只存 hash；
- 日志不记录密码、Cookie、Bearer Token、验证码；
- 不信任客户端 user_id、tenant_id、network_id、role；UUID 只是标识不是权限；
- 防普通成员改自己/他人角色、防移除最后 Owner、邀请 Token 一次性；
- Agent 使用独立短期身份，不复用个人 Token；
- 超级管理员与运维访问同样留痕；
- 修改密码、禁用用户、退出全部设备能撤销会话。
- 参考基线：RFC 9700（OAuth 2.0 Security BCP）、OWASP Authentication Cheat Sheet、OWASP Password Storage Cheat Sheet。

---

## 11. 非功能需求

| 类别 | 要求 |
|---|---|
| 性能 | 登录 P95、授权查询 P95 达标；邀请接受/成员操作 P95 达标（目标值待压测基线确定） |
| 可用性 | 会话过期引导不丢失用户工作；/me 偶发 5xx 显示连接异常而非误判未登录 |
| 安全 | 越权测试全通过；撤权传播时间达标（缓存/凭证 SLA） |
| 可观测 | 认证与组网事件全审计，request_id 贯穿；登录成功率、限流次数、撤权延迟可监控 |
| 兼容性 | 前端兼容主流浏览器密码管理器与自动填充；移动端可用 |
| 数据 | 审计保留期独立于成员删除；关键记录软删除 |

---

## 12. 测试与验收矩阵

### 12.1 认证测试

注册重复邮箱/并发注册；正确/错误密码；禁用用户、未验证用户；Session 正常/过期/吊销/伪造/重放；单设备与全部设备退出；重置 Token 过期/重放/账号不匹配；CSRF、非法 origin、Cookie 属性；登录限流与用户枚举防护；OIDC state/nonce/PKCE/issuer/audience 校验。

### 12.2 组网测试（两租户、三网络、四用户）

- Alice 创建 N1 邀请 Bob → Bob 可访问网络资源 ✅
- Carol 同租户非成员 → 列表/详情/安装/调用全部失败 ✅
- Bob 退出后缓存页面清理，API 立即或 SLA 内拒绝 ✅
- 普通成员不能邀请、改角色、移除 Owner ✅
- 重复点击邀请/接受不产生重复关系 ✅
- 切换租户/网络不闪现旧私有内容 ✅
- 并发接受邀请仅一条 membership；并发改角色返回 409 ✅
- 修改 header/body 中 tenant_id、user_id、network_id 的伪造上下文攻击全部失败 ✅
- 猜 ID 访问失败；network suspended/archived 行为正确 ✅

### 12.3 与 Agent 发布联动

private/tenant/network/public 四 scope；一个 release 发布两个网络；只撤回一个 publication；用户退出 N1 仍属 N2 不受影响；Agent 调用期间成员被移除；后台任务入队后执行前权限变化。

---

## 13. 实施里程碑

| 阶段 | 内容 | 交付物 |
|---|---|---|
| Phase 0 语义对齐 | ADR：7.1/8.1 共用 Identity、7.2/8.2 共用 Network；认证方案已定：GitHub OAuth App（Authorization Code + PKCE）；确认网络归属、角色与 permission codes | ADR、ER 图、接口契约、授权真值表 |
| Phase 1 可信身份 | 注册 GitHub OAuth App（本地 callback）、users/user_identities/sessions 模型、/auth/*、/me、认证中间件、收敛 CORS/Cookie/CSRF、去除客户端 user_id 信任 | 认证与会话测试、安全基线 |
| Phase 2 租户基础 | tenants/memberships、个人租户创建、租户邀请、切换；新资源绑定 tenant_id | 租户隔离测试 |
| Phase 3 虚拟网络 | networks/memberships/invitations、四角色、创建/邀请/接受/移除/退出、状态机、幂等与审计 | 组网 API 与并发测试 |
| Phase 4 资源共享闭环 | publication 四 scope、目录/详情/安装/调用统一授权、Agent 工作负载身份与通信审计、撤权 | 三用户演示 + 越权矩阵 |
| Phase 5 规模化扩展 | Redis 缓存与失效、outbox 通知、跨租户网络评估、WebSocket/A2A 动态撤权 | 压测报告、Runbook |

---

## 14. 风险与依赖

| 风险/依赖 | 说明 | 应对 |
|---|---|---|
| 前端隐藏 ≠ 安全 | 前端路由守卫不是授权 | 所有授权在服务端判定 |
| localStorage Token | 易受 XSS | 服务端会话 Cookie 方案 |
| 权限缓存撤权延迟 | 缓存权限过期 | 主动失效 + 短 TTL，敏感操作查事实源 |
| Agent 身份混用 | 复用个人长期 Token | 独立工作负载身份 + 短期凭证 |
| 依赖面 | 7.1、7.2、4.5、4.6、9.4、10.1 | Phase 0 对齐契约，避免双账号表/双 Token |
| 自建认证运营成本 | 密码维护、找回、安全响应 | 优先 OIDC；自建则遵循完整安全基线 |
| 跨租户组网 | 合规、数据出域、责任模型 | MVP 不支持，Phase 5 再评估 |

---

## 15. 开放问题（待评审确认）

1. ~~认证方案：OIDC 优先还是自建密码注册？~~ **已确定（2026-08）：接入 GitHub OAuth App 登录（Authorization Code + PKCE），首次登录自动建号并创建个人租户；自建邮箱密码仅作可选后备，不进入 MVP。**
2. 一个网络是否严格属于一个租户？（本 PRD 默认是）
3. Viewer 角色默认是否允许调用？（本 PRD 默认否，可配置）
4. 成员退出后已安装资源如何处理？（本 PRD 默认保留历史安装记录、停止新增调用）
5. Agent 间通信复用本地消息机制还是引入网络消息总线？（本 PRD 默认 MVP 复用本地机制，只新增授权层）
6. 数据库是否引入 PostgreSQL + RLS？（本 PRD 默认应用层授权，RLS 纵深防御）

---

## 16. 成功指标

| 指标 | 说明 |
|---|---|
| 登录成功率 / P95 | 认证可用性 |
| 登录失败与限流次数 | 安全异常监控 |
| 活跃会话数与吊销延迟 | 会话治理 |
| 邀请接受率 / 过期率 | 产品体验 |
| 授权查询 P95 | 网络规模增长后的性能 |
| 越权测试通过率 | 隔离正确性证明 |
| 成员移除后撤权时间 | 缓存/凭证失效能力 |
| permission denied 按原因分布 | 配置与攻击问题发现 |

> 备注：所有性能或成功率数字必须来自固定环境、数据规模与测试方法的真实结果，验收时不得虚构。

---

## 附录 A：完成定义（Definition of Done）

当以下条件全部成立，8.1/8.2 才构成可交付闭环：

- [ ] 请求主体来自认证结果，业务接口不再信任客户端 user_id；
- [ ] 用户能查看并切换自己真实所属的租户；
- [ ] 网络创建、邀请、接受、移除、退出具备完整状态机与审计；
- [ ] 数据库约束阻止重复 membership 与非法关系；
- [ ] private / tenant / network / public 可见性语义明确且被测试覆盖；
- [ ] 列表、详情、安装、调用均有服务端对象级授权；
- [ ] 同租户不同网络、跨租户、猜 ID、伪造上下文全部通过隔离测试；
- [ ] 成员移除后缓存、凭证、长连接、后台任务有明确撤权规则；
- [ ] 网络内 Agent 通信使用独立工作负载身份并可审计、可撤权；
- [ ] 关键事件可通过 request_id 追踪；
- [ ] 性能与权限测试结果真实可复现。
---

## 附录 B：GitHub 登录接入前提与 OAuth App 注册步骤

### B.1 「callback 必须 HTTPS」的准确含义

- GitHub 官方要求 OAuth App 的 Authorization callback URL 使用 HTTPS，**唯一例外是 http://localhost 与 http://127.0.0.1**（本地开发放行）；
- 回调本质是**浏览器重定向**：GitHub 服务器不会主动请求你的回调地址，而是把用户浏览器 302 回你注册的 URL。因此**开发/本机演示不需要公网 IP 或域名**；
- 只有生产环境（用户从公网访问你的服务）才必须有 HTTPS 域名（Nginx/Caddy + 证书）；
- redirect_uri 必须与注册值逐字符一致（协议、主机、端口、路径全部参与匹配）。

### B.2 三种场景对照

| 场景 | 回调地址 | 是否需要 HTTPS/域名 |
|---|---|---|
| 本机开发与演示（浏览器在本机） | http://localhost:8001/auth/callback | **不需要**，GitHub 放行 localhost |
| 局域网多人访问（别人浏览器访问你的机器） | http://192.168.x.x:8001/auth/callback | GitHub 会拒绝注册非 localhost 的 http 地址 → 改用免费隧道（cloudflared / ngrok）拿 https 临时域名，或正式部署 |
| 生产部署 | https://your-domain/auth/callback | 需要域名 + HTTPS |

### B.3 接入前提清单

GitHub 侧：

1. 一个 GitHub 账号（OAuth App 可挂在个人或组织名下）；
2. Settings → Developer settings → OAuth Apps → New OAuth App：Application name 填 SOTABand；Homepage URL 填 http://localhost:8001；Authorization callback URL 填 http://localhost:8001/auth/callback（与后端路由完全一致）；
3. 记录 Client ID；生成 Client Secret（只显示一次，存 .env，不进代码库不进 git）。

网络侧（国内环境）：

4. 用户浏览器能打开 github.com 授权页（偶有延迟，必要时代理）；
5. 后端服务器能出站访问 github.com/login/oauth/access_token 与 api.github.com（换 token、拉用户信息）；
6. 每个登录用户拥有自己的 GitHub 账号（三用户演示 = 三个账号，或同一浏览器用三个浏览器配置文件）。

代码侧（Phase 1 范围）：

7. .env 配置 GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET / GITHUB_REDIRECT_URI（.env.example 已添加模板）；
8. GET /auth/login：生成 state（防 CSRF，服务端保存）+ PKCE code_verifier（S256 challenge），拼 GitHub 授权 URL，scope=read:user user:email；
9. GET /auth/callback：校验 state → 用 code + code_verifier + secret 换 access token → 调 /user、/user/emails → 按 (issuer=github, subject=GitHub 数字 ID) upsert users/user_identities → 首次登录创建个人租户 → 下发会话 Cookie；
10. 数据库：PostgreSQL（或开发期 SQLite）先建 users / user_identities / sessions 三张基础表（见 PRD 7.2 节）；
11. 用户邮箱未公开时 email 为 null：本地留空并引导用户补充，不影响账号主键。

### B.4 常见坑

- redirect_uri 带/不带结尾斜杠会被 GitHub 视为不同地址——前端、后端、OAuth App 注册值三处必须逐字符一致；
- Secret 泄露等于账号体系被接管：不进前端代码、不进 git、不在日志打印；
- GitHub 授权码 10 分钟有效且一次性，callback 重复刷新会报 bad_verification_code——前端按 FR-A7 的 callback 状态机处理；
- 生产环境务必新建 OAuth App（或新增生产 callback），不要把 localhost 回调带到线上。


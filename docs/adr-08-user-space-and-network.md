# ADR-08：用户空间与用户网络的架构决策

> 状态：**已接受** ｜ 日期：2026-10-15 ｜ 编号：8.1 用户注册与登录 / 8.2 用户组网
> 上游需求：`docs/prd-08-user-space-and-networking.md`（PRD v1.0）
> 选型依据：`docs/database-selection-research.md`
> 冻结契约：`docs/contract-08-auth-network.md`

---

## 0. 为什么需要这份 ADR

8.1/8.2 在 `docs/target.md` 里只是第 6 节，但它是**全组的地基**。PRD 第 13 节 Phase 0 写明：

> ADR：**7.1/8.1 共用 Identity、7.2/8.2 共用 Network**

依赖本模块的下游需求：

| 需求 | 依赖本模块的什么 | 负责人 |
|---|---|---|
| 7.1 全局服务器·用户注册与登录 | `user` / `user_identity` / `session` 表定义与 `/api/auth/*` 契约 | 他人 |
| 7.2 全局服务器·局部网络构建 | `network` / `network_membership` 表定义 | 他人 |
| 7.1 工具共享 | `AuthContext`、`network_id`、`publication` | 他人 |
| 8.1 数据共享 | 同上 | 他人 |
| 10.1 智能体发布 | 同上 | 他人 |
| 9.3–9.5 三大集市 | 身份与授权主体 | 他人 |

**因此本 ADR 的第一目标是「冻结接口」，第二目标才是「实现功能」。** 契约一旦冻结，下游五位同学即可并行开工；契约若反复，五人一起返工。

---

## 1. 决策清单（速览）

| 编号 | 决策 | 是否修订 PRD |
|---|---|---|
| ADR-08-01 | MVP 认证走**自建账号+密码**，OIDC 预留 | ⚠️ 修订 PRD FR-A1/A2 |
| ADR-08-01a | 密码哈希用 **bcrypt**（work factor 12），不用 passlib | ⚠️ 修订 PRD §10 |
| ADR-08-01b | **不做邮箱验证**（含不做找回密码） | ⚠️ 修订 PRD FR-A1 |
| ADR-08-02 | **SQLAlchemy 2.0 async + Alembic**，开发 SQLite，`DATABASE_URL` 可切 PostgreSQL | 细化 |
| ADR-08-03 | 租户角色与网络角色**分两个字段**，词表统一为 `owner/admin/member/viewer` | 修订前端类型 |
| ADR-08-04 | 业务接口**不再信任客户端 `user_id`** | 落实 PRD 附录 A |
| ADR-08-05 | 认证路由收敛到 **`/api/auth/*`**，全部落在 Vite 已有 `/api` 代理内 | ⚠️ 修订 PRD §8.1 路径 |
| ADR-08-06 | 与 7.x **共用** Identity/Network 模型，禁止两套账号表 | 落实 PRD 风险表 |
| ADR-08-07 | Agent 通信**只加授权层，不换传输层** | 落实 PRD 开放问题 5 |
| ADR-08-08 | 现有 `visibility` 字段**保持不动**，`publication` 另起一套，两者共存 | 修订 PRD 隐含假设 |
| ADR-08-09 | MVP **不做**的清单（防范围蔓延） | — |
| ADR-08-10 | 分期与 PRD Phase 的映射 | — |
| ADR-08-11 | **不使用外键与级联**（阿里开发手册），引用完整性归应用层；表名一律**单数**；每表必备 `id`/`gmt_create`/`gmt_modified`；索引命名 `pk_`/`uk_`/`idx_` | ⚠️ 修订 PRD §7.1/§7.2 隐含假设 |

---

## 2. 决策详述

### ADR-08-01　认证方式：MVP 自建邮箱+密码，OIDC 预留

**背景**：PRD FR-A1/A2 已确定「接入 GitHub OAuth App（Authorization Code + PKCE）」，自建密码路径「不进入 MVP」。本节修订该决定。

**决策**：MVP 实现自建「账号 + 密码」+ 服务端会话 Cookie；**表结构与接口按「多身份并存」设计**，Phase 2 再接 GitHub OAuth。

子决策：**ADR-08-01a 密码哈希用 bcrypt**、**ADR-08-01b 不做邮箱验证**（见下方）。

**理由**：

1. 演示/评审的网络环境不可控，`github.com` 授权页在受限网络下可能打不开，评审现场无法复现是硬伤；
2. PRD 附录 B.3 自述「三用户演示 = 三个账号」，即需要三个真实 GitHub 账号；
3. 后端还需能出站访问 `github.com/login/oauth/access_token` 与 `api.github.com`；
4. 自建路径零外部依赖，现场必定可跑。

**为什么这不违背 PRD 的架构**：PRD 的 `user_identities(issuer, subject)` 表**本来就是为多身份并存设计的**。Phase 1 建表并写 `issuer='local'`, `subject=<user_id>`；Phase 2 只需为同一 `user_id` 追加一行 `issuer='github'`, `subject=<GitHub 数字 ID>`，**表结构不动**。

**后果**：

- PRD FR-A1/A2 中所有 OIDC 相关验收标准推迟至 Phase 2；
- 自建路径必须完整满足 PRD 第 10 节安全要求（自适应哈希、注册/登录分别限流、失败响应不泄露用户枚举）；
- `.env` 需新增 `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` / `GITHUB_REDIRECT_URI` 的**预留占位**（Phase 2 再补，现状见 §3 冲突表第 8 项）。

---

### ADR-08-01a　密码哈希：bcrypt

> 状态：**修订 PRD §10**。PRD 原文要求 Argon2id。

**决策**：密码哈希使用 **bcrypt**（官方 `bcrypt` 包，`$2b$` 版本，work factor **12**）。

**理由**：

- 生态成熟、跨语言可验证（后期若引入其他语言的服务端，`$2b$` 格式通用）；
- Python 官方 `bcrypt` 包由 bcrypt 原作者维护，无中间层；
- OWASP Password Storage 基线将 bcrypt（work factor ≥ 10）列为可接受方案。

**必须处理的三个 bcrypt 特性**（否则会出真实故障）：

| 特性 | 后果 | 处置 |
|---|---|---|
| **72 字节输入上限** | `bcrypt` 4.x 对超长密码**直接抛 `ValueError: password cannot be longer than 72 bytes`**，不是静默截断 | 注册与登录入口统一按 **UTF-8 字节长度**校验；超长时**拒绝并提示**，不得静默截断（静默截断会让 "同一密码不同后缀" 都能登录） |
| 无内存硬度 | 相对 Argon2id 更易被 GPU/ASIC 加速破解 | 用 **work factor 12** 补偿（约 250ms/次量级，需实测标定）；登录限流作为第二道防线 |
| `$2a$/$2y$/$2b$` 版本差异 | 部分历史实现有 8-bit 截断缺陷 | 统一生成 `$2b$`；校验时接受历史前缀但登录成功后**自动重哈希升级** |

**明确不使用的库**：

- ❌ **`passlib`** —— 其 1.7.4 与 `bcrypt >= 4.1` 不兼容，会因读取已被移除的 `bcrypt.__about__` 而在启动时报错（社区已多次报告）。直接用官方 `bcrypt` 包。
- ❌ 任何 md5 / sha1 / sha256 裸哈希、加盐但不迭代的方案。

**依赖**：`pyproject.toml` 新增 `bcrypt>=4.1`。

**后果**：PRD §10「不使用快速散列」的意图被满足（bcrypt 是自适应慢哈希）；但相对 Argon2id 的抗 GPU 能力弱一档，已在文档中留痕，后期如评审要求可平滑升级（`user_credentials.algorithm` 字段已为此预留，支持登录时重哈希迁移）。

---

### ADR-08-01b　MVP 不做邮箱验证

> 状态：**回答 PRD §5.1 FR-A1 中的「邮箱验证」要求**。

**决策**：MVP **不做**邮箱验证；账号创建后即可登录。

**理由**：

1. 自建路径下没有邮件服务（SMTP 配置、发信配额、垃圾邮件合规都是额外成本）；
2. 本模块的 MVP 目标是**打通身份与授权链路**，不是构建可对外运营的账号系统；
3. 邮箱验证的**唯一实际作用**是防止他人用不属于自己的邮箱注册——在单团队、本地部署场景下威胁很低。

**后果**：

- `users.email` 允许直接写入且**不做所有权验证**；文档与 UI 必须如实提示，避免让使用者误以为邮箱已验证；
- `user` 表**不设 `email_verified` 字段**（避免留下永远为 false 的误导性状态）；如后续需要，走新增迁移；
- 注册接口**仍然**要做邮箱规范化（去空格、小写域名部分）与唯一性约束——这与验证无关，是防重复账号的必要条件；
- 找回密码功能**随邮箱验证一并推迟**（没有可信邮箱就无法安全找回）。

---

### ADR-08-02　数据库：SQLAlchemy 2.0 async + Alembic，开发 SQLite，可切 PostgreSQL

**背景**：调研文档（`docs/database-selection-research.md`）的结论是 PostgreSQL 16+。但本机实测：`psql` 不存在、无 `postgres*` 服务、Docker 已安装但**守护进程未启动**（`npipe:////./pipe/dockerDesktopLinuxEngine` 连接失败）。而项目当前是「零配置、`pip install -e .` 即可运行」的形态。

**决策**：

- 技术栈：**SQLAlchemy 2.0（async）+ Alembic 迁移 + `aiosqlite`（开发）/ `psycopg3`（生产）**；
- `DATABASE_URL` 走环境变量，默认 `sqlite+aiosqlite:///./storage/sotaband.db`；
- 启用已存在但无人引用的 `config/settings.py:344` `StorageConfig.database_url`；
- **schema 只由 Alembic 管理**，两种库共用同一套迁移，避免行为漂移。

**这不违背调研文档**——该文档第 3 节自述：「本地开发用 SQLite 兜底（零配置跑通测试），但 schema 由 Alembic 统一管理，避免两种库行为漂移」。

**为保持可移植性，必须遵守的写法约束**：

| 约束 | 原因 |
|---|---|
| 不写裸 SQL，一律走 ORM | SQLite/PG 方言差异 |
| 主键用 `sqlalchemy.Uuid`（2.0 跨方言类型），不用 PG 原生 `UUID` | 避免方言绑定 |
| JSON 列用 `sqlalchemy.JSON`，**不用 `JSONB`** | `JSONB` 仅 PG 有 |
| 时间列用 `core.db.types.UTCDateTime`（**不用裸 `DateTime(timezone=True)`**） | SQLite 无时区类型会丢 tz，裸类型读出来是 naive，与 `utcnow()` 比较抛 `TypeError` |
| 并发控制用**唯一约束 + 版本号条件更新**，不用 `SELECT ... FOR UPDATE` | ⚠️ **SQLite 无行级锁，`FOR UPDATE` 是空操作** |
| Alembic `env.py` 开启 `render_as_batch=True` | SQLite 的 `ALTER TABLE` 能力极弱 |
| SQLite 连接开启 WAL 模式 | 缓解单写者串行 |

**⚠️ 重点风险：FR-N4「最后一位 Owner 不可被移除（事务内锁定检查）」在 SQLite 上无法用行锁实现。**

必须改为：在事务内**重新统计 Owner 数量**并配合 `version` 字段做条件更新（compare-and-set），冲突返回 409。注意：这一守卫在 SQLite 上是「串行写恰好掩盖了竞态」，**测试通过不等于 PG 上正确**。因此：

> `network_membership` 的并发用例（并发接受邀请、并发改角色、并发移除最后 Owner）**必须在 PostgreSQL 上跑一遍**才算验收通过。CI 里加一个 PG service container，或至少在 Phase 3 结束前手工跑一次。

**后果**：Phase 1 不需要安装任何东西即可开工；PostgreSQL 的引入被降级为「一个环境变量 + 一次迁移重放」。

#### ✅ PostgreSQL 复验已完成（2026-10-26，Docker）

按上面那条红线做了实际复验，**发现并修掉了两个真问题**。环境：`postgres:16-alpine`
容器（`localhost:55432`），4 个迁移全部在 PG 上跑通，11 张表、0 外键，
`alembic check` 干净。

**① 驱动必须用 `asyncpg`，不能用 `psycopg`（Windows 硬约束）**

实测：

| 驱动 | Windows 默认（Proactor） | Selector |
|---|---|---|
| `psycopg` async | ❌ `Psycopg cannot use the 'ProactorEventLoop'` | ✅ |
| `asyncpg` | ✅ | ✅ |

而本项目的 Agent 工厂依赖 `asyncio.create_subprocess_exec` —— **Windows 上它只在
Proactor 下可用**。两者直接冲突：为修 psycopg 而切到 Selector，会让 Agent 子进程
彻底不可用。

**结论：`[postgres]` extra 从 `psycopg[binary]` 改为 `asyncpg`。**
（调研文档推荐的 psycopg 在「Windows + asyncio 子进程」这个组合下不可行。）

**② 「最后一位 Owner」守卫在 PG 上确实是竞态的（已修）**

SQLite 的单写者掩盖了 check-then-act 竞态。在 PG 上用 barrier 强制两个事务同时
进入临界区后，实测得到：

```text
T1: SELECT count(owner) -> 2  ✓     T2: SELECT count(owner) -> 2  ✓
T1: 移除 Bob  -> 1                  T2: 移除 Alice -> 0
结果：网络 0 位 Owner，变成无人可管的孤儿 —— 并发移除结果 ['ok', 'ok']
```

**修法**：成员变更前对 `network` 行加排他锁 ——
`core/auth/authorize.py::lock_network()`（`SELECT … FOR UPDATE`）。
PG 上它让并发事务排队；SQLite 方言忽略该子句，但 SQLite 本来就是库级写锁，等价。
**只加在写路径**，读接口不加锁（否则会无谓地串行化只读请求）。

回归测试 `test_concurrent_remove_owners_never_leaves_zero_owners` 已验证：
**去掉锁它立刻失败**，加回锁通过。

**双库验证结果**：

| 数据库 | 结果 |
|---|---|
| SQLite（默认） | 169 passed，3 skipped（并发用例显式跳过，不给假绿） |
| PostgreSQL 16（Docker） | **171 passed，1 skipped**（SQLite 特有的 Uuid 存储格式用例） |

跑法见 `tests/integration/test_network_concurrency.py` 的模块文档。

---

### ADR-08-03　角色词表统一

**背景**：现存三套互不兼容的角色词：

| 来源 | 取值 |
|---|---|
| `frontend/src/types/resources.ts:95`（`UserResource.role`） | `'admin' \| 'developer' \| 'viewer'` |
| PRD §6（网络角色） | `Owner / Admin / Member / Viewer` |
| PRD §7.2 `tenant_memberships.role` | 未明确 |

**决策**：

- **租户角色与网络角色是两个独立字段**，不合并——PRD §6 的权限矩阵是按**网络角色**写的，租户角色另有语义；
- 两者取值统一为小写 `owner` / `admin` / `member` / `viewer`；
- `UserResource.role` 的 `'developer'` **作废**，改为引用租户角色；
- 前端 `UserResource` 是 `resources/users/` 空间的资源类型，与「当前登录用户」是两个概念，不要混用（见 ADR-08-04）。

---

### ADR-08-04　`user_id` 的信任边界

**背景**：当前代码存在两套并存的用户标识来源：

| 位置 | 来源 | 问题 |
|---|---|---|
| `app/api/routes/chat_routes.py:104` | `request.user_id or "default"` —— **客户端提交** | 可伪造 |
| `core/api/implementations/api_data.py:41` | `get_current_user_id()` —— 服务端本地标识 | 单机版，无认证 |
| `core/resource/registry/*.py` | `get_current_user_id()` 注入 `owner` | 同上 |

**决策**：

- Phase 1 起，**所有业务接口的主体身份只从 `AuthContext` 取**，客户端提交的 `user_id` 一律忽略并记警告日志；
- `core/user_context.py::get_current_user_id()` **不删除**，降级为「未登录时的本地回退标识」，供 8.1/8.2 尚未覆盖的既有接口平滑过渡；一旦某接口接入 `AuthContext`，就不得再读 `get_current_user_id()`；
- `ApiDataRegister` 等 API 层实现改为接受注入的 `owner`，不再自行读取。

**后果**：需要修改 `app/api/routes/chat_routes.py`——**这是对话模块同学的文件，必须协调后改**，不能单方面动。

---

### ADR-08-05　认证路由命名空间：收敛到 `/api/auth/*`

**背景**：实测 `frontend/vite.config.ts:13-19` **只代理 `/api`**：

```ts
server: { proxy: { '/api': { target: 'http://localhost:8001', changeOrigin: true } } }
```

PRD §8.1 定义的是 `GET /auth/login`、`GET /auth/callback`、`POST /auth/logout`——**这些路径不在 `/api` 前缀内，不会被代理**。开发期前端在 `:5173`、后端在 `:8001`，这些请求会变成真正的跨域请求，而 `config/settings.py:404` 的 `cors_origins` 默认是 `["*"]`，与 `app/main.py:60` 的 `allow_credentials=True` **在浏览器规范下不能共存**（`Access-Control-Allow-Origin: *` 不允许携带凭证）。结果是：Cookie 会话**静默失败**。

**决策**：认证接口统一挂到 `/api` 前缀下：

```text
POST /api/auth/register          注册（限流）
POST /api/auth/login             登录
POST /api/auth/logout            退出当前设备
POST /api/auth/logout-all        退出全部设备
GET  /api/auth/session           会话探测（供前端 Auth 状态机，不返回 401）
GET  /api/auth/callback          OAuth 回调（Phase 2 预留）
```

**收益**：现有 `/api` 代理规则**无需修改**即覆盖全部认证请求，开发期全程同源——**CORS 与 SameSite 问题在开发期彻底消失**。

**同时必须做的收敛**（防御非代理场景，如生产前端独立部署）：

```python
# config/settings.py
cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
```

**⚠️ OAuth 回调路径一旦确定，必须在 GitHub OAuth App 注册值里逐字符一致**（协议、主机、端口、路径、结尾斜杠全部参与匹配）。Phase 2 接入时统一为 `http://localhost:8001/api/auth/callback`。

**Cookie 属性约定**：

| 属性 | 开发（http://localhost） | 生产（https） |
|---|---|---|
| `HttpOnly` | ✅ | ✅ |
| `SameSite` | `Lax` | `Lax` |
| `Secure` | **按环境条件开启** | ✅ |
| `Path` | `/` | `/` |
| 值 | 随机 opaque token；**服务端只存 hash** | 同左 |

> 注意：Cookie **不区分端口**，`localhost:8001` 下发的 Cookie 会同样发往 `localhost:5173`。但 `localhost` 与 `127.0.0.1` 在 SameSite 计算中是**不同站点**——前后端必须统一使用 `localhost`，不要混用。

---

### ADR-08-06　与 7.x 全局服务器共用 Identity / Network 模型

**决策**：8.x（客户端本地视角）与 7.x（全局服务器视角）**共用同一套表定义与同一份 `/api/auth/*` 契约**。

- `user` / `user_identity` / `session` / `tenant` / `tenant_membership` 的表结构由本 ADR 冻结，7.x 不得另建；
- `network` / `network_membership` / `network_invitation` 同理；
- 差异只在**部署形态**：8.x 是单实例本地库，7.x 是集中的全局服务；两者 schema 相同，便于后期「本地账号上云」。

**这是本模块最大的架构风险**（PRD §14 风险表原文：「避免双账号表/双 Token」）。**必须在 Phase 1 开工前与 7.x 负责人对齐并留痕。**

---

### ADR-08-07　Agent 通信：只加授权层，不换传输层

**背景**：`core/agent/bus.py` 已有 `InProcessMessageBus`（Pub/Sub，`asyncio`）；`core/agent/process.py` 已实现 `agent.{id}.task` 订阅 + `reply_to` 回发，且消息上下文里已带 `user_id`。**通信链路本身已经通了**，缺的是身份与授权。

> 注：`bus.py` 的模块 docstring 写「提供 Pub/Sub 和 RPC 两种通信模式」，但**实际只实现了 Pub/Sub，RPC 并不存在**。不要被注释误导。

**决策**（对应 PRD 开放问题 5 的默认答案）：

- MVP **不引入**网络消息总线、不实现 RPC 传输、不做 P2P；
- 在现有 `InProcessMessageBus` 之上增加**授权层**：

```text
Agent A 发起调用
  → 携带工作负载凭证（workload principal，非用户长期 Token）
  → 授权层校验：凭证有效 → A 与 B 同属网络 N → B 的白名单允许 A → 速率限制
  → 放行 → 现有 bus 投递 → 记录审计
```

- 工作负载凭证：独立 principal + 短期有效期 + 最小 scope + 目标白名单，**绝不复用用户的会话 Cookie**；
- 撤权：成员退出 / 安装停用时吊销凭证，短期有效期作为兜底。

---

### ADR-08-08　`visibility` 与 `publication` 共存，不互相迁移

**背景**：仓库**已经实现**了一套资源可见性模型（`core/user_context.py`）：

| 资源 | 现有模型 | 实现位置 |
|---|---|---|
| 数据集 | `private`，按 `owner` 隔离 | `data_registry.py`（含历史条目认领启发式） |
| 工具 | `public`，全局共享 | `tool_registry.py:213` |
| Agent | `public`，全局共享 | `agent_registry.py:156` |

而 PRD FR-N5 要求用 `publication` 表表达 `private / tenant / network / public` 四种 scope，并明确「而非资源上一个 `is_public` 布尔字段」。

**决策**：**两者共存，各管一段，不做迁移**。

- `visibility` 保留为**资源条目的默认可见性**（粗粒度，现有 48 工具 / 30 模型 / 数据集的既有行为**完全不变**）；
- `publication` 是**「某资源版本 → 某 scope」的授权发布记录**（细粒度，带独立状态、时间、撤回记录）；
- 网络目录（`/api/network-catalog`）**只读 `publication`**，不读 `visibility`；
- 既有的直接列表接口（`/api/tool/list` 等）**继续读 `visibility`**。

**理由**：把现有 registry 的全部条目迁移到 publication 模型，是 PRD 里被严重低估的隐性工作量（读 registry 的代码散落在 `core/resource/registry/` 多处，且数据集还有「本机是否存在」的历史启发式）。在 Phase 1–3 阶段保持既有行为不动，可让下游同学不被打断。

---

### ADR-08-09　MVP 明确「不做」清单（防范围蔓延）

PRD 第 5/7/13 节描述的是终态。以下明确**不在本模块 MVP 范围**，避免被误认为欠交付：

| 不做 | 理由 / 归属 |
|---|---|
| RLS（行级安全） | 应用层授权是主防线，RLS 仅纵深防御；PG 专属，与 ADR-08-02 冲突 |
| Redis、消息队列、outbox 通知 | 调研文档已挂到 Phase 5 决策门 |
| pgvector / 向量检索 | 同上 |
| `audit_event` 按月分区 | MVP 单表足够 |
| MFA、企业联邦、自定义角色 | PRD §2.4 明确的 Non-Goals |
| 跨租户网络联盟、嵌套网络 | PRD §2.4 |
| 物理组网（VPN / SDN / P2P 打洞 / 局域网广播） | PRD §2.4 第一条 |
| RPC / 网络消息总线传输层 | ADR-08-07 |
| 把现有 JSON registry 整体迁移进数据库 | 见 ADR-08-08 |

---

### ADR-08-10　分期与 PRD Phase 的映射

按「**先解锁下游**」排序，而非严格照抄 PRD 的 Phase 顺序。

| 步 | 内容 | PRD Phase | 交割给下游的接口 |
|---|---|---|---|
| **0** | 本 ADR + 冻结契约 + 授权真值表 | Phase 0 | `AuthContext`、权限码、错误码 |
| **1** | 持久化底座：`core/db/`、Alembic、`user`/`user_identity`/`session` | Phase 1 | 表结构；7.1 可开工 |
| **2** | 认证闭环：`/api/auth/*`、`/api/v1/me`、认证中间件、前端登录与 Auth 状态机 | Phase 1 | **`AuthContext` 可被依赖**；所有下游解锁 |
| **3** | 租户：`tenant`/`tenant_membership`、个人租户、切换 | Phase 2 | `tenant_id` 语义 |
| **4** | 网络：`network`/`memberships`/`invitations`、四角色、状态机 | Phase 3 | `network_id` + 授权判定入口 |
| **5** | 共享与 Agent 通信授权：`publication`、`catalog`、工作负载身份、撤权 | Phase 4 | 7.1 工具共享 / 8.1 数据共享 / 10.1 Agent 发布 |
| **6** | Redis / 通知 / 分区（仅在有真实证据时） | Phase 5 | — |

**关键交付判据**：**Step 2 结束时，`AuthContext` 必须冻结并可被下游 import**。这是整条链路的瓶颈点。

---

### ADR-08-11　不使用外键与级联（阿里开发手册）

**决策**：数据库层**不建任何外键约束，也不使用级联删除/更新**。一切引用完整性由**应用层**解决。

**依据**（阿里巴巴 Java 开发手册，ORM 映射 / 数据库设计）：

> 【强制】不得使用外键与级联，一切外键概念必须在应用层解决。
> 说明：外键与级联更新适用于单机低并发，不适合分布式、高并发集群；级联更新是强阻塞，存在数据库更新风暴的风险；外键影响数据库的插入速度。

**落地方式**：

| 项 | 做法 |
|---|---|
| 列 | 不写 `ForeignKey`，不写 `ondelete`；`user_id` 等仅为**逻辑引用** |
| ORM 关系 | 显式写 `primaryjoin` + `foreign()` —— 无外键后 SQLAlchemy **无法推断**连接条件 |
| 索引命名 | 同步改为手册规定：主键 `pk_`、唯一索引 `uk_`、普通索引 `idx_`（原 `ix_`/`uq_`） |
| `PRAGMA foreign_keys=ON` | 保留为**纵深防御**：当前 schema 下是空操作，但万一有人加了 FK，它能保证约束真的生效 |

**代价与对冲**（这是本决策必须留痕的部分）：

| 代价 | 对冲措施 |
|---|---|
| 数据库不再拦"孤儿行"（凭证指向不存在的用户） | 应用层写入前校验引用存在性；删除按依赖顺序显式执行 |
| ORM 连接条件写错可能**静默连错行** | 回归测试 `test_relationships_join_correctly_without_foreign_keys` 用两个用户交叉验证 |
| 无法级联删除 | 删除逻辑集中在服务层，不散落各处 |
| 跨表一致性需要人工维护 | 批次 2/3 落地时把"引用校验"抽成公共函数，避免每处重写 |

**测试固化**（两条互补）：

- `test_no_foreign_keys_anywhere` —— 任何人加回外键都会失败；
- `test_referential_integrity_is_not_enforced_by_db` —— 把「数据库不管」这一事实**显性化**，避免有人误以为有保护。

**与 PRD 的关系**：PRD §7.1 的 ER 概览（`users 1─n sessions`）表达的是**逻辑关系**，本 ADR 不改变逻辑关系，只改变**约束的落地层** —— 由"数据库保证"变为"应用层保证"。ER 图仍然有效。

#### 手册中其余相关条款的处理

"按手册来"不能机械套用 —— 有一条与既定需求直接冲突：

| 手册条款 | 处理 | 理由 |
|---|---|---|
| 【强制】不得使用外键与级联 | ✅ **采纳** | 本 ADR |
| 【强制】主键索引 `pk_`、唯一索引 `uk_`、普通索引 `idx_` | ✅ **采纳** | 命名，无冲突 |
| 【强制】表名不使用复数名词 | ✅ **采纳**（2026-10-15 评审确认） | 表名一律单数：`user` / `session` / `network` / `publication` … |
| 【强制】表必备三字段 `id`、`gmt_create`、`gmt_modified` | ✅ **采纳** | 做成 `UUIDPrimaryKeyMixin` + `TimestampMixin`，**结构性保证**不被漏写 |
| 【强制】`id` 为 `bigint unsigned` 自增 | ❌ **不采纳** | **与 PRD §7.3「使用不可猜测 ID（UUID）」直接冲突**。UUID 是安全要求（防遍历/枚举），优先级高于该条命名规范。手册该条的立论是"单表自增性能"，本项目无此压力 |

#### 采纳单数表名带来的实际代价：`user` 是 SQL 保留字

`user` 在 PostgreSQL 中等价于 `CURRENT_USER`，属**保留字**。实测结论见
`tests/unit/test_db_foundation.py::test_reserved_word_table_names_require_quoting`：

| 表名 | PostgreSQL | SQLite |
|---|---|---|
| `user` | **需要引号** → `"user"` | 不需要 |
| `session` / `user_identity` / `user_credential` / `audit_event` | 不需要 | 不需要 |

- **ORM 用法不受影响** —— SQLAlchemy 的 identifier preparer 自动加引号，生成的 DDL 就是 `CREATE TABLE "user"`；
- **裸 SQL 必须手写 `"user"`** —— `SELECT * FROM user` 在 PostgreSQL 上报语法错误，**在 SQLite 上却能过**。典型的"开发没问题、切库才炸"；
- 若将来觉得引号成本太高，可改名为 `user_account` / `sys_user`。

#### 时间字段：用 `UTCDateTime` 而不只是改名

改为 `gmt_create` / `gmt_modified` 的同时，列类型用 `core/db/types.py::UTCDateTime`
而非裸 `DateTime(timezone=True)`。原因：SQLite 没有时区类型，裸类型读出来是
naive，与 `utcnow()` 比较会抛 `TypeError: can't compare offset-naive and
offset-aware datetimes` —— 而且**只在"从库里读出来的对象"上出现**，刚 `add()`
未刷新的对象却是 aware，于是同一段代码有时报错有时不报。

`UTCDateTime` 在读写两端自动归一为 aware UTC，让这类 bug 无法出现。
**DDL 与裸 `DateTime(timezone=True)` 完全一致**（`alembic check` 无新增操作）。

---

## 3. 已核实的代码冲突与待协调事项

以下问题经逐文件核实，**开工前需处理**：

| # | 位置 | 问题 | 处置 | 需协调 |
|---|---|---|---|---|
| 1 | `config/settings.py:404` + `app/main.py:60` | `cors_origins=["*"]` 与 `allow_credentials=True` 并存 | 收敛为显式 origin 列表 | 否 |
| 2 | `frontend/vite.config.ts:15` | 只代理 `/api`，`/auth/*` 会跨域 | 见 ADR-08-05，认证挂 `/api/auth/*` | 否 |
| 3 | `app/api/routes/chat_routes.py:104` | 信任客户端 `user_id` | 改为读 `AuthContext` | ⚠️ 对话模块同学 |
| 4 | `frontend/src/App.tsx` | **全站没有路由**，直接渲染 `<MainLayout/>`；`package.json` 无 router 依赖 | 按现有 Zustand 风格在 `App.tsx` 加 auth gate，渲染 `<LoginView/>` 或 `<MainLayout/>`；**不引路由库** | 否 |
| 5 | `frontend/src/services/api/*.ts` | 36 处裸 `fetch()`，无统一 client、无 `credentials` 设置 | 新建 `services/api/client.ts` 统一处理 401/403/凭证（PRD FR-A7 的前置条件） | 否 |
| 6 | `core/agent/bus.py` docstring | 声称有 RPC，实际只有 Pub/Sub | 修正注释，避免误导下游 | 否 |
| 7 | `frontend/src/types/resources.ts:95` | `role: 'admin'\|'developer'\|'viewer'` 与 PRD 角色冲突 | 见 ADR-08-03 | 前端同学 |
| 8 | `.env.example` | **没有** PRD 附录 B.3 声称的 `GITHUB_CLIENT_ID` 等模板 | 本 ADR 记录该不一致；Phase 2 再补 | 否 |
| 9 | `tests/` | 只有空 `__init__.py`，`pytest` **未安装** | 装 `[dev]` 依赖，建立测试基线 | 否 |
| 10 | `config/settings.py:344` | `StorageConfig.database_url` 已声明但**全库无人引用** | ADR-08-02 启用之 | 否 |

---

## 4. 后果与风险

**正面后果**

- 下游五位同学在 Step 2 结束后即可并行开工，且不需要数据库环境；
- 开发期零跨域问题（同源代理），消除一整类静默失败；
- 现有 48 工具 / 30 模型 / 数据集的行为**不受影响**（ADR-08-08）。

**负面后果 / 风险**

| 风险 | 说明 | 应对 |
|---|---|---|
| SQLite 与 PG 行为漂移 | 并发、`ALTER TABLE`、大小写、类型亲和性 | 只用 ORM；`render_as_batch`；**并发用例必须在 PG 上复验** |
| 最后 Owner 守卫在 SQLite 上「假通过」 | 无行级锁，串行写掩盖竞态 | 条件更新 + 版本号；PG 复验（见 ADR-08-02） |
| 与 7.x 双账号表 | 后期合并极痛 | ADR-08-06，Phase 1 前对齐并留痕 |
| 自建密码的运营成本 | 找回密码、安全响应 | MVP 只做注册/登录/登出；找回密码列入 Step 2 可选 |
| 契约冻结后仍被要求改 | 下游已按契约写代码 | 契约变更走「新 ADR + 全员通知」，禁止就地修改 |

---

## 5. 待办（本 ADR 未覆盖、需另行确认）

1. `tenant_memberships.role` 的租户角色权限矩阵尚未定义（PRD §6 只定义了网络角色）；
2. 资源配额（PRD 术语表提到「资源配额：工具数量、数据容量、Agent 数量」）在 MVP 是否落地未定；
3. ~~邮箱验证是否进入 MVP~~ —— **已决：MVP 不做邮箱验证，找回密码一并推迟**（见 ADR-08-01b）；
4. `audit_event` 的保留期与清理策略未定；
5. 前端 `LoginView` 的视觉规范未定（需与 `ui-design.md` 对齐）。

---

## 附录：与 PRD 的差异汇总（供 PRD 修订）

| PRD 位置 | 原文 | 本 ADR 修订 |
|---|---|---|
| FR-A1 / FR-A2 | 接入 GitHub OAuth App，自建路径不进入 MVP | MVP 先自建，OIDC 移至 Phase 2（ADR-08-01） |
| FR-A1 | 自建路径需「邮箱验证」 | **MVP 不做邮箱验证**，找回密码一并推迟（ADR-08-01b） |
| §10 | 密码用 Argon2id 自适应哈希 | 改用 **bcrypt work factor 12**，并处理 72 字节上限（ADR-08-01a） |
| §7.3 | PostgreSQL 16+ 为主库 | 同一套 Alembic schema，开发 SQLite、生产 PG（ADR-08-02） |
| §8.1 | `/auth/login`、`/auth/callback`、`/auth/logout` | `/api/auth/*`（ADR-08-05） |
| §9.4 / FR-N4 | 「事务内锁定检查」 | SQLite 无行锁，改用条件更新 + 版本号，PG 复验（ADR-08-02） |
| FR-N5 | publication 取代布尔可见性字段 | 与既有 `visibility` 共存，不做迁移（ADR-08-08） |
| §15 开放问题 2–6 | 待评审 | 已由 ADR-08-03/06/07/08 及 ADR-08-09 作答；问题 3（Viewer 默认可否调用）见契约 §5 |

# 冻结契约：8.1 用户注册与登录 / 8.2 用户组网

> 版本：**v1.0（冻结）** ｜ 冻结日期：2026-10-15
> 决策依据：`docs/adr-08-user-space-and-network.md`
> 上游需求：`docs/prd-08-user-space-and-networking.md`

---

## 0. 冻结声明

**本文件是下游模块的唯一真值来源。** 7.1 工具共享、8.1 数据共享、10.1 Agent 发布、9.3–9.5 三大集市**只允许依赖本文件定义的接口与语义**，不得自行猜测字段名或自行扩展。

变更流程：**新 ADR → 全员通知 → 本文件升版本号**。禁止就地修改本文件。

冻结范围：

| 冻结项 | 章节 |
|---|---|
| `AuthContext` 结构与获取方式 | §1 |
| 数据表与约束 | §2 |
| HTTP 接口契约 | §3 |
| 错误码语义 | §4 |
| 权限码与授权真值表 | §5 |
| 授权判定顺序 | §6 |
| 前端集成约定 | §7 |

**未冻结**（可变）：表内部索引、日志格式、审计保留期、UI 视觉。

---

## 1. AuthContext（核心契约）

### 1.1 定义

```python
# core/auth/context.py —— 归属 8.1

from dataclasses import dataclass

@dataclass(frozen=True)
class AuthContext:
    """认证中间件生成的**唯一可信**请求主体上下文。

    下游模块**只允许**从这里获取 user_id / tenant_id。
    客户端在 body、query、header 中提交的同名字段一律忽略。
    """

    # ── 主体 ──
    user_id: str            # user.id（UUID 字符串）
    session_id: str         # sessions.id（UUID 字符串）
    tenant_id: str          # 当前激活租户 tenants.id
    tenant_role: str        # owner | admin | member | viewer
    request_id: str         # 贯穿审计与日志

    # ── 展示用（可为空）──
    email: str | None = None
    display_name: str | None = None

    # ── 主体类型（Phase 5 启用）──
    actor_kind: str = "user"        # user | workload
    workload_id: str | None = None  # actor_kind == "workload" 时的安装实例 id
```

### 1.2 获取方式（FastAPI 依赖）

```python
# app/api/deps.py —— 归属 8.1

async def get_auth_context(request: Request) -> AuthContext:
    """必需认证。缺失/过期/吊销 → 401。"""

async def get_optional_auth(request: Request) -> AuthContext | None:
    """可选认证。用于公开接口的个性化，不抛异常。"""

def require_permission(permission: str, *, scope: str = "tenant"):
    """返回一个依赖，校验权限码。scope: tenant | network"""
```

**下游用法（约定）**：

```python
@router.get("/api/tool/shared")
async def list_shared(
    ctx: AuthContext = Depends(get_auth_context),          # ← 唯一身份来源
    _: None = Depends(require_permission("network.resource.install")),
):
    tenant_id = ctx.tenant_id        # ✅
    # user_id = request.query_params["user_id"]   # ❌ 禁止
```

### 1.3 网络角色是**按需解析**，不进 AuthContext

理由：`AuthContext` 是「当前会话 + 激活租户」的量；网络角色是「针对某个 network_id」的量，一个用户在不同网络中角色不同。

```python
# core/auth/authorize.py —— 归属 8.2

async def resolve_network_role(ctx: AuthContext, network_id: str) -> str | None:
    """返回 owner/admin/member/viewer，非成员返回 None。"""

async def authorize(
    ctx: AuthContext,
    *,
    permission: str,
    network_id: str | None = None,
    resource: tuple[str, str] | None = None,   # (resource_type, release_id)
) -> None:
    """授权入口。不通过时抛 HTTPException，状态码遵循 §4。"""
```

**所有网络资源操作必须且只能调用 `authorize()`**，禁止各路由自行写 if-else 判定——这是防 IDOR 的结构性保证（PRD §8.3 警告）。

### 1.4 TypeScript 对应（前端）

```ts
// frontend/src/types/auth.ts —— 归属 8.1

export type TenantRole = 'owner' | 'admin' | 'member' | 'viewer'
export type NetworkRole = 'owner' | 'admin' | 'member' | 'viewer'
export type AuthStatus = 'unknown' | 'authenticated' | 'unauthenticated' | 'expired' | 'disabled'

export interface CurrentUser {
  userId: string
  email: string | null
  displayName: string | null
  activeTenantId: string
  tenantRole: TenantRole
  tenants: Array<{ id: string; name: string; role: TenantRole }>
}
```

---

## 2. 数据表与约束

### 2.1 ER 概览

```text
users ─1:n─ user_identities        (issuer, subject) 唯一
users ─1:1─ user_credentials       仅自建密码路径
users ─1:n─ sessions               token 只存 hash
users ─n:m─ tenants                via tenant_memberships (tenant_id, user_id) 唯一
tenants ─1:n─ networks             (tenant_id, name) 唯一
networks ─1:n─ network_memberships (network_id, user_id) 唯一，带 version
networks ─1:n─ network_invitations token_hash 唯一
releases ─1:n─ publications        scope + scope_id
一切写操作 ──→ audit_events        actor/tenant/network/action/target/result/request_id
```

### 2.2 表定义

> **可移植性约束**（强制，见 ADR-08-02）：主键用 `sqlalchemy.Uuid`；JSON 列用 `sqlalchemy.JSON`（**不用 `JSONB`**）；时间列用 `core.db.types.UTCDateTime`（impl 即 `DateTime(timezone=True)`，但保证读写两端都是 **aware UTC**）；**不写裸 SQL**。
>
> **⚠️ 不使用外键**（强制，见 ADR-08-11）：所有 `*_id` 列都是**逻辑引用**，数据库层**没有** `FOREIGN KEY`、**没有** `ON DELETE CASCADE`。引用完整性由**应用层**负责：写入前校验引用存在、删除时按依赖顺序显式清理。
>
> **命名遵循阿里开发手册**（见 ADR-08-11）：
> - 表名**一律单数**，不使用复数名词；
> - 每表必备三字段 `id`、`gmt_create`、`gmt_modified`（由 `UUIDPrimaryKeyMixin` + `TimestampMixin` 结构性保证，不是靠自觉）；
> - 索引命名：主键 `pk_`、唯一索引 `uk_`、普通索引 `idx_`；
> - ⚠️ **`user` 是 PostgreSQL 保留字** —— ORM 会自动加引号，**裸 SQL 必须手写 `"user"`**。

| # | 表 | 关键列 | 约束 |
|---|---|---|---|
| 1 | `user` | `id` PK Uuid、`email` String(320) NULL、`display_name` String(128)、`avatar_url` Text NULL、`status` String(16)、`gmt_create`、`gmt_modified` | `email` 规范化后唯一（可空，OIDC 未公开邮箱时为空）；`status ∈ {active, disabled}`；**MVP 不做邮箱验证，故不设 `email_verified` 字段**（ADR-08-01b） |
| 2 | `user_identity` | `id` PK、`user_id`（逻辑引用 `user.id`）、`issuer` String(32)、`subject` String(255)、`gmt_create`、`gmt_modified` | **UNIQUE(issuer, subject)**；`user_id` 建普通索引；Phase 1 写 `issuer='local'`，Phase 2 追加 `issuer='github'` |
| 3 | `user_credential` | `id` PK、`user_id`（逻辑引用 `user.id`，**UNIQUE**）、`password_hash` Text、`algorithm` String(32)、`changed_at`、`gmt_create`、`gmt_modified` | **UNIQUE(user_id)** 保证"一人一条凭证" —— 手册要求每表都有独立 `id` 主键，故 `user_id` 从主键退为唯一列，**保证未减弱**；`algorithm='bcrypt'`，`$2b$` + work factor 12；**bcrypt 72 字节上限必须在入口按 UTF-8 字节校验并拒绝**（ADR-08-01a） |
| 4 | `session` | `id` PK、`user_id`（逻辑引用 `user.id`）、`token_hash` String(64)、`expires_at`、`revoked_at` NULL、`user_agent` Text NULL、`ip` String(45) NULL、`gmt_create`、`gmt_modified` | **UNIQUE(token_hash)**；索引 `(user_id, revoked_at)`；**明文 token 永不落库** |
| 5 | `tenant` | `id` PK、`name` String(128)、`slug` String(64)、`status` String(16)、`gmt_create`、`gmt_modified` | **UNIQUE(slug)** |
| 6 | `tenant_membership` | `id` PK、`tenant_id`（逻辑引用）、`user_id`（逻辑引用 `user.id`）、`role` String(16)、`status` String(16)、`gmt_create`、`gmt_modified` | **UNIQUE(tenant_id, user_id)** |
| 7 | `network` | `id` PK、`tenant_id`（逻辑引用）、`name` String(128)、`owner_id`（逻辑引用 `user.id`）、`status` String(16)、`policy` JSON、`gmt_create`、`gmt_modified` | **UNIQUE(tenant_id, name)**；`status ∈ {active, suspended, archived}` |
| 8 | `network_membership` | `id` PK、`network_id`（逻辑引用）、`user_id`（逻辑引用 `user.id`）、`role` String(16)、`status` String(16)、`joined_at`、`version` Integer、`gmt_create`、`gmt_modified` | **UNIQUE(network_id, user_id)**；`version` 用于条件更新（ADR-08-02 的并发守卫） |
| 9 | `network_invitation` | `id` PK、`network_id`（逻辑引用）、`email` String(320)、`token_hash` String(64)、`status` String(16)、`expires_at`、`invited_by`（逻辑引用 `user.id`）、`gmt_create`、`gmt_modified` | **UNIQUE(token_hash)**；`status ∈ {pending, accepted, rejected, expired, revoked}` |
| 10 | `publication` | `id` PK、`resource_type` String(16)、`release_id` String(64)、`scope` String(16)、`scope_id` Uuid NULL、`status` String(16)、`published_by`（逻辑引用 `user.id`）、`gmt_create`、`gmt_modified` | `resource_type ∈ {agent, tool, data}`；`scope ∈ {private, tenant, network, public}`；**CHECK: scope='network' 时 scope_id 非空**；UNIQUE(resource_type, release_id, scope, scope_id) |
| 11 | `audit_event` | `id` PK、`actor_id` String(64)、`actor_kind` String(16)、`tenant_id` NULL、`network_id` NULL、`action` String(64)、`target_type` String(32)、`target_id` String(64)、`result` String(16)、`request_id` String(64)、`gmt_create`、`gmt_modified` | 只追加（故 `gmt_modified` 恒等于 `gmt_create`）；索引 `(tenant_id, gmt_create)`；**保留期独立于成员删除**；全部为逻辑引用，**必须能在被引用对象删除后存活** |

> **应用层必须做的三件事**（无外键方案的固定配套，批次 2/3 落地时抽成公共函数）：
> 1. **写子表前校验父行存在**（如写 `session` 前确认 `user.id` 存在）；
> 2. **删除按依赖顺序显式执行**（先子后父），不做级联；
> 3. **审计表例外**：它引用的是"历史"，**不校验存在性**，也不随父行删除而清理。

### 2.3 `networks.policy` 结构

```jsonc
{
  "allowMemberPublish": false,   // 默认 false（PRD §6「Member 发布资源 = 按策略」）
  "allowViewerInvoke": false     // 默认 false（PRD §15 开放问题 3 取默认否）
}
```

---

## 3. HTTP 接口契约

> **命名空间约定**（ADR-08-05）：认证接口挂 `/api/auth/*`，业务接口挂 `/api/v1/*`。
> **两者都落在 `frontend/vite.config.ts` 已有的 `/api` 代理规则内**，开发期全程同源。

### 3.1 认证与会话（Phase 1–2）

| 方法 | 路径 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `POST` | `/api/auth/register` | 否 | `{email, password, displayName?}` | `201 {user, tenant}` + Set-Cookie |
| `POST` | `/api/auth/login` | 否 | `{email, password}` | `200 {user}` + Set-Cookie |
| `POST` | `/api/auth/logout` | 是 | — | `204` + 清 Cookie |
| `POST` | `/api/auth/logout-all` | 是 | — | `204`，吊销该用户全部会话 |
| `GET` | `/api/auth/session` | 否 | — | `200 {authenticated: bool, user?: CurrentUser}`；**未登录返回 200 而非 401**（供前端状态机探测） |
| `GET` | `/api/auth/callback` | 否 | `?code&state` | `302` 回前端（**Phase 2 预留**） |
| `GET` | `/api/v1/me` | 是 | — | `200 CurrentUser` |
| `GET` | `/api/v1/me/sessions` | 是 | — | `200 {sessions: [...]}` |
| `DELETE` | `/api/v1/me/sessions/{session_id}` | 是 | — | `204` |
| **`POST`** | **`/api/v1/me/password`** | 是 | `{currentPassword, newPassword}` | `200 {revokedSessions, message}`；**成功吊销全部会话**；当前密码错 → **400**（不是 401，否则前端会把人登出）；失败限流 → 429 |
| `GET` | `/api/v1/me/tenants` | 是 | — | `200 [TenantSummary]` |
| `POST` | `/api/v1/me/switch-tenant` | 是 | `{tenantId}` | `200 TenantDetail`；**服务端校验 active membership**，不属于则 `403` 且保持原租户 |
| `POST` | `/api/v1/tenants` | 是 | `{name}` | `201 TenantDetail`（创建者成为 Owner；**不自动切换激活租户**） |

**租户邀请（Step 3 补充，契约原文未定义，采用与网络邀请一致的形状）**：

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `POST` | `/api/v1/tenants/{tenant_id}/invitations` | Owner / Admin | `201 {invitation, token, accept_path}`；**`token` 只此一次可见** |
| `GET` | `/api/v1/tenants/{tenant_id}/invitations` | Owner / Admin | `200 [InvitationSummary]`（不含令牌） |
| `DELETE` | `/api/v1/tenants/{tenant_id}/invitations/{invitation_id}` | Owner / Admin | `204`，幂等 |
| `POST` | `/api/v1/tenant-invitations/{token}/accept` | 已登录 + 邮箱匹配 | `200 TenantDetail`；已用/过期 `410`，邮箱不符 `403` |

> **为什么租户用邀请制而不是管理员直接添加**：
> ① PRD FR-A6 原文是「**接受**租户邀请」；② 直接添加可以在对方不知情时把人拉进
> 组织，缺少**同意**；③ 邀请制复用网络邀请那套令牌机制（只存 hash、状态机、条件更新）。
> 代价是多一个接受步骤 —— 前端可在接受后立刻调 `switch-tenant` 实现「加入即进入」。
>
> **`tenant_id` 只能是自己当前激活租户**：不允许「我是 A 租户管理员，所以能管 B 租户」。
> 要管别的租户先切过去（切换时服务端校验成员关系）。
>
> **激活租户是服务端事实**：存在 `session.active_tenant_id`，客户端无法通过改请求头
> 伪造（PRD FR-A6「不允许仅改前端状态」）。该值失效时（成员被移除、租户停用）认证
> 链路会**回退到主租户**而不是 401 —— 否则用户会被过期偏好锁在门外。

### 3.2 网络（Phase 3–4）

| 方法 | 路径 | 权限码 |
|---|---|---|
| `POST` | `/api/v1/networks` | 已认证 + 租户成员 |
| `GET` | `/api/v1/networks` | 已认证（返回自己所属的网络） |
| `GET` | `/api/v1/networks/{network_id}` | `network.read` |
| `PATCH` | `/api/v1/networks/{network_id}` | `network.update` / `network.archive` |
| `GET` | `/api/v1/networks/{network_id}/members` | `network.members.read` |
| `PATCH` | `/api/v1/networks/{network_id}/members/{user_id}` | `network.members.manage` |
| `DELETE` | `/api/v1/networks/{network_id}/members/{user_id}` | `network.members.manage` |
| `POST` | `/api/v1/networks/{network_id}/leave` | 网络成员（自己） |
| `POST` | `/api/v1/networks/{network_id}/invitations` | `network.invite.create` |
| `GET` | `/api/v1/networks/{network_id}/invitations` | `network.invite.create` |
| `DELETE` | `/api/v1/networks/{network_id}/invitations/{invitation_id}` | `network.invite.revoke` |
| `POST` | `/api/v1/network-invitations/{token}/accept` | 已认证 + 邮箱匹配 |
| **`GET`** | **`/api/v1/networks/{network_id}/audit`** | **`network.audit.read`**（Owner/Admin）；`?limit=1..200&offset&action`，新的在前，无 COUNT 总数 |

### 3.3 共享与调用（Phase 5）

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| `POST` | `/api/v1/{agent\|tool\|data}-releases/{release_id}/publications` | 用户 | 发布到某 scope；**幂等** |
| `DELETE` | `/api/v1/publications/{publication_id}` | 用户 | 撤回（软删除） |
| **`PATCH`** | **`/api/v1/publications/{publication_id}/invoke-policy`** | **用户** | **设置谁能调用**：`{invokePolicy: network\|allowlist, invokeAllowlist: [userId]}`。发布者本人或网络 Owner/Admin；`allowlist` 时名单不能为空且必须是该网络 active 成员 |
| `GET` | `/api/v1/network-catalog?network_id=...` | 用户 | 网络资源目录 |
| `GET` | `/api/v1/network-catalog/{resource_type}/{release_id}` | 用户 | 资源详情 |
| `POST` | `/api/v1/{agent\|tool\|data}-releases/{release_id}/installations` | 用户 | 安装到工作空间；**幂等** |
| `GET` | `/api/v1/me/installations` | 用户 | 我安装的资源 |
| `DELETE` | `/api/v1/installations/{installation_id}` | 用户 | 卸载（仅安装者本人） |
| **`POST`** | **`/api/v1/installations/{installation_id}/credential`** | **用户** | **签发短期工作负载凭证**（仅安装者本人） |
| `POST` | `/api/v1/installations/{installation_id}/invoke` | **工作负载** | Agent 调用网络内资源 |

> ⚠️ **列表、详情、安装、调用必须复用同一个 `authorize()`**。只在目录列表过滤、详情接口可猜 ID 访问，是典型 IDOR（PRD §8.3）。

#### 工作负载身份（FR-N7 / FR-N8，2026-10-27 落地）

`invoke` 接口**只认 `Authorization: Bearer <工作负载令牌>`，绝不回退到 Cookie**
（PRD FR-N7 硬性要求：Agent 不能用用户会话冒充）。用 Cookie 调用返回 **401**。

**令牌形状**：`w1.<installation_id>.<exp>.<jti>.<HMAC-SHA256>`

**为什么它不违反"不用 JWT"那条既有决策**（见 `core/auth/tokens.py`）：

| | 会话令牌 | 工作负载令牌 |
|---|---|---|
| 有效性由谁决定 | **令牌本身**（所以必须查库才能吊销） | **每次调用现查数据库** |
| 签名的作用 | 无（本来就要查库） | 仅防伪造 `installation_id`，**不承载任何权限** |
| 撤权方式 | 置 `revoked_at` | 卸载/撤发布/移成员 → **下一次调用**即失效 |

一句话：会话令牌是**通行证**（必须查库确认没被吊销），工作负载令牌是**工牌**
（只用来认人，能不能进门每次都看门禁）。

**调用判定链（顺序即优先级，全部现查 DB）**：

1. 调用方 installation 存在且 active
2. 其来源 publication 存在且 active
3. 来源 publication **属于本次请求的网络**（否则跨网络越权）
4. 网络存在且 active
5. 安装者仍是该网络的 active 成员
6. 角色拥有 `network.agent.invoke`（受 `policy.allowViewerInvoke` 约束）
7. 目标在该网络有 active publication（= **网络级白名单**）
8. 目标 publication 的 `invoke_policy='allowlist'` 时，**调用方安装者**必须在
   `invoke_allowlist` 里（= **按目标**粒度的白名单）

**失败也写审计**：无论放行还是拒绝都写一条 `network.agent.invoke`，主体是
`actor_kind='workload'` + `actor_id=installation_id`。拒绝的 `result='denied'` ——
它是识别越权尝试的唯一依据，所以接口**先 commit 审计再抛状态码**。

**速率限制**：按**调用方 installation** 计数，默认 120 次/60 秒
（`SOTABAND_INVOKE_MAX_PER_WINDOW` / `SOTABAND_INVOKE_WINDOW`），超限 **429 +
`Retry-After`**。限流在授权**之前**执行——超限请求不做任何数据库查询。

**投递（FR-N7 接线，2026-10-27）**：授权通过后经 `core/agent/invoke_bus.py` 的
`InvocationBus` 把调用投递给目标 Agent 并等待响应。响应新增：

| 字段 | 含义 |
|---|---|
| `delivered` | 是否真的送到目标 Agent |
| `delivery_status` | `ok` / `offline` / `timeout` |
| `response` | 目标 Agent 的响应体（可能为空） |

> ⚠️ **`delivered=false` 仍然返回 200**。授权已成立，只是目标没上线（`offline`）
> 或卡住（`timeout`），调用方稍后重试即可 —— 混成 4xx 会让调用方误判为权限问题。
>
> PRD §2.4 把「Agent 底层通信协议（RPC/消息总线传输层）」列为非目标：本实现
> **没有引入新传输层依赖**，只是把已有的 `MessageBus` 抽象接到授权结果上，
> 传输实现可替换。

### 3.4 密码与会话规则

> **2026-10-27 变更**：密码长度从"8–72 **字节**"改为"**8–20 位字符**"，并新增
> "必须同时包含字母和数字"的硬性要求。原因是旧规则无法向用户解释 ——
> 「至少 8 个字节，中文按 3 字节计」普通用户既看不懂也没法自己数。
> 真正影响面见下方表格最后三行。

| 项 | 约定 |
|---|---|
| 哈希算法 | **bcrypt**，`$2b$`，work factor **12**（ADR-08-01a） |
| 库 | 官方 `bcrypt` 包；**禁止 `passlib`**（与 `bcrypt>=4.1` 不兼容） |
| **密码长度** | **8 ≤ 字符数 ≤ 20**（按**字符**计，不按字节）；区间外拒绝并给出「需为 8-20 位，当前 N 位」 |
| **密码组合** | **必须同时包含字母和数字**；缺哪条就提示哪条（「需包含至少一个字母」/「需包含至少一个数字」） |
| **bcrypt 硬上限** | 仍然守 **72 字节**。20 位以内若塞满多字节字符仍可能超限（如 18 个 4 字节字符 + 2 个 3 字节数字 = 78 字节），**必须单独拒绝并提示**，不得静默截断 |
| 前端反馈 | 注册页实时显示三条规则清单（长度 / 字母 / 数字）并逐条打勾；服务端仍是最终裁决 |
| 邮箱验证 | **MVP 不做**；注册成功即可登录（ADR-08-01b） |
| 会话令牌 | 32 字节随机（`secrets.token_urlsafe`）；**数据库只存 SHA-256 hash**；明文仅出现在 Set-Cookie |
| 会话有效期 | 绝对过期 30 天；登录成功后刷新（滑动过期）可选，MVP 可只做绝对过期 |
| 吊销 | 登出 → `revoked_at` 置值；改密码 / 禁用用户 / 退出全部设备 → 该用户全部会话置 `revoked_at` |
| 限流 | 注册、登录**分别**限流；按 IP + 账号双维度；连续失败触发临时锁定 |
| 用户枚举 | 「邮箱已存在」与「密码错误」在响应体、状态码、**耗时量级**上必须一致；注册重复邮箱返回统一错误 |
| 日志 | 禁止记录密码、Cookie、令牌；可复用 `core/security/secrets.py` 的 `scrub_text()` |

**旧的「密码强度」行已删除** —— 它和上面的「密码组合」重复，且"不做复杂度硬性要求"
与现在的字母+数字要求矛盾。以本表为准。

---

## 4. 错误码语义

| 状态码 | 触发条件 | 前端行为 |
|---|---|---|
| `400` | 请求体不合法 | 显示字段错误 |
| `401` | 未认证 / 会话过期 / 会话已吊销 | 尝试一次会话恢复，失败则跳登录，**保留安全 return URL** |
| `403` | 已认证，资源**对调用者可见**，但该动作被角色/策略拒绝 | 显示「权限不足」，**不刷新、不重试** |
| `404` | 资源不存在，或存在但调用者**无权知晓其存在** | 按不存在处理，**不泄露存在性差异** |
| `409` | 并发冲突（角色调整 version 不匹配、移除最后 Owner、重复邀请） | 提示刷新重试 |
| `410` | 邀请已过期 / 已使用 / 已撤销 | 明确引导 |
| `429` | 触发限流（登录、注册、重置分别限流） | 显示等待时间 |

**关键判定规则**：

- 「同租户但非网络成员」访问网络资源 → **404，不是 403**（否则泄露网络/资源存在性）；
- 登录失败与「邮箱不存在」返回**完全一致**的响应体与耗时量级（防用户枚举）；
- 日志**不得**记录密码、Cookie、Bearer Token、邀请 Token、验证码（`core/security/secrets.py` 的脱敏能力可复用）。

---

## 5. 权限码与授权真值表

### 5.1 权限码

PRD §6 原有的 6 个（**保持原名**）：

```text
network.members.manage      成员与角色管理
network.invite.create       发起邀请
network.resource.publish    发布资源到网络
network.resource.install    安装网络资源
network.agent.invoke        调用 Agent
network.audit.read          查看网络审计
```

本契约补充的 5 个（用于表达 PRD §6「部分」「按策略」等模糊格）：

```text
network.read                查看网络详情
network.update              修改网络名称等信息
network.archive             暂停 / 归档 / 恢复
network.members.read        查看成员列表
network.invite.revoke       撤销邀请
```

### 5.2 网络角色 → 权限真值表

| 权限码 | `owner` | `admin` | `member` | `viewer` | 非成员 |
|---|:---:|:---:|:---:|:---:|:---:|
| `network.read` | ✅ | ✅ | ✅ | ✅ | ❌ `404` |
| `network.update` | ✅ | ✅ | ❌ | ❌ | ❌ `404` |
| `network.archive` | ✅ | ❌ | ❌ | ❌ | ❌ `404` |
| `network.members.read` | ✅ | ✅ | ✅ | ✅ | ❌ `404` |
| `network.members.manage` | ✅ | ⚠️ 受限 ¹ | ❌ `403` | ❌ `403` | ❌ `404` |
| `network.invite.create` | ✅ | ✅ | ❌ `403` | ❌ `403` | ❌ `404` |
| `network.invite.revoke` | ✅ | ✅ | ❌ `403` | ❌ `403` | ❌ `404` |
| `network.resource.publish` | ✅ | ✅ | ⚠️ 策略 ² | ❌ `403` | ❌ `404` |
| `network.resource.install` | ✅ | ✅ | ✅ | ✅ | ❌ `404` |
| `network.agent.invoke` | ✅ | ✅ | ✅ | ⚠️ 策略 ³ | ❌ `404` |
| `network.audit.read` | ✅ | ✅ | ❌ `403` | ❌ `403` | ❌ `404` |

- ¹ **Admin 受限**：不可对 `owner` 行使任何成员管理动作；不可授予或剥夺 `owner`；不可移除自己（须走 `leave`）。
- ² **策略开关**：`networks.policy.allowMemberPublish`，**默认 `false`**。
- ³ **策略开关**：`networks.policy.allowViewerInvoke`，**默认 `false`**（PRD §15 开放问题 3 采纳默认否）。

### 5.3 租户层规则

| 场景 | 结果 |
|---|---|
| 未认证 | `401` |
| 会话有效但激活租户非自己所属 | `403`，且**保持原租户上下文**，前端不得仅改本地状态 |
| 访问的 `network.tenant_id` ≠ `ctx.tenant_id` | `404`（跨租户不可见） |
| 是自己所属租户的成员，但非目标网络成员 | `404` |

### 5.4 工作负载主体（Phase 5）

| 场景 | 结果 |
|---|---|
| Agent A 调用 Agent B | 校验：凭证有效 → A、B 同属网络 N → B 白名单允许 A → 速率限制 |
| 成员退出网络 | 相关工作负载凭证**吊销**；短期有效期作为兜底 |
| 工作负载凭证调用非白名单目标 | `403` |
| 使用**用户会话 Cookie** 发起 Agent 间调用 | **`401`，禁止**（PRD §10「Agent 使用独立短期身份，不复用个人 Token」） |

---

## 6. 授权判定顺序（每次网络资源操作）

严格按序，**未命中任何允许规则则默认拒绝**：

```text
1. 请求者已认证                       → 否则 401
2. Session 有效且未吊销               → 否则 401
3. 存在激活租户                       → 否则 401
4. 是激活租户的 active 成员           → 否则 403
5. 目标是合法网络（属于激活租户）      → 否则 404
6. 网络状态允许该动作                 → suspended: 拒绝写；archived: 拒绝全部写 → 409
7. 是网络的 active 成员               → 否则 404
8. 网络角色拥有该 permission          → 否则 403
9. 资源属于合法租户                   → 否则 404
10. publication 允许该动作            → 否则 404
11. 配额 / 限流 / 审批通过            → 否则 429
```

> **第 4、6 步是整体拒绝（403/409），第 5、7、9、10 步是「不可见」（404）**——区分依据是「调用者是否有权知道这个东西存在」。

---

## 7. 前端集成约定

### 7.1 Auth 状态机

```text
unknown ──探测 /api/auth/session──→ authenticated | unauthenticated
                                       │
                      401 ────────────┴──→ expired ──→ 跳登录（保留安全 return URL）
                      status=disabled ────→ disabled ──→ 专用提示页
```

- `unknown` 时**不渲染任何受保护内容**（PRD FR-A7：受保护页面无未登录闪现）；
- 状态存于 `frontend/src/stores/auth-store.ts`（Zustand，与现有 store 风格一致）。

### 7.2 入口改造（不引路由库）

现状：`frontend/src/App.tsx` 只有 5 行，直接渲染 `<MainLayout />`；`frontend/package.json` **无 react-router 依赖**。

约定：

```tsx
// frontend/src/App.tsx
export default function App() {
  const status = useAuthStore((s) => s.status)
  if (status === 'unknown') return <AuthBootstrap />   // 全屏骨架，不渲染业务
  if (status !== 'authenticated') return <LoginView />
  return <MainLayout />
}
```

**不引入路由库**（ADR-08-10 冲突表 #4），除非后续确有深链接需求。

### 7.3 统一 API client（前置基础设施）

现状：36 处裸 `fetch()` 分散在 `frontend/src/services/api/{agent,api,chat,config,data,tool}.ts`，**无统一 client、未设置 `credentials`**。

约定：新建 `frontend/src/services/api/client.ts`，统一负责：

| 职责 | 说明 |
|---|---|
| 凭证 | `credentials: 'include'` |
| 401 处理 | **全局至多触发一次**会话恢复，失败则置 `expired` 并跳登录；**禁止无限刷新** |
| 403 处理 | 抛类型化错误，UI 显示「权限不足」，不重试 |
| 404 处理 | 按不存在处理，不提示「无权限」 |
| request_id | 从响应头读取并附在错误对象上，供反馈定位 |

> `BASE_URL` 保持 `''`（相对路径），依赖 Vite 同源代理与生产环境同源部署。

### 7.4 Cookie 与站点一致性

| 项 | 约定 |
|---|---|
| 主机名 | 前后端**统一用 `localhost`**，不得一处用 `localhost` 一处用 `127.0.0.1`（SameSite 语义上属不同站点） |
| 端口 | Cookie 不区分端口，`localhost:8001` 下发的 Cookie 会发往 `localhost:5173`，无需额外配置 |
| `Secure` | 按环境条件开启（开发 http 关闭，生产 https 开启） |
| localStorage | **禁止**存放长期 Token（PRD §14 风险表） |

---

## 8. 下游接入清单（给 7.1 / 8.1 数据 / 10.1 / 9.x）

下游同学改自己模块时，只需做这三件事：

1. **删掉**任何读取客户端提交的 `user_id` / `tenant_id` / `network_id` / `role` 的代码；
2. **加** `ctx: AuthContext = Depends(get_auth_context)`；
3. 网络资源操作**必须**过 `await authorize(ctx, permission=..., network_id=...)`，不要自己写 if-else。

**Step 2 交付后即可开工**（见 ADR-08-10），无需等待数据库或网络表就绪——先用 `AuthContext`，`network_id` 到 Step 4 再接入。

---

## v2 修订：认证基线与资源权限（2026-11-04）

> 本修订为**行为变更**，实现对应 PR #5 评审修复。冻结契约 §3 的路径/字段不变，
> 新增以下约束。

### A. 认证基线（D1）

- **所有业务 API 未登录一律 401**，包括 8.1 之前的历史接口（data/file/tool/
  agent/model/task/config/llm/migrate/chat/apis）。
- **公开白名单**仅两项：`/api/auth/*` 认证生命周期（注册/登录/会话探测/登出）
  与 `/api/health`。
- 前端已强制登录，不需要新的调用方式；统一 client 的 401 会切到 `expired` 状态。

### B. 资源写权限矩阵（D2）

| 操作 | user-generated（有 owner） | builtin（无 owner） |
|---|---|---|
| 读/列出/执行（工具/Agent/模型） | 登录即可（共享资产） | 登录即可 |
| 改代码/规范/标签/参数/装依赖/删除 | **owner 或租户 Owner/Admin** | **仅租户 Owner/Admin** |
| 启动/停止/重启 Agent（运行控制） | **owner 或租户 Owner/Admin** | **仅租户 Owner/Admin** |
| 停止自动调试 | 同上（按工具归属校验） | 同上 |

- 同一租户的普通成员之间**不允许**互相编辑对方资源（用户明确拍板）；
- 失败统一 404（不确认存在性）；实现见 `core/auth/ownership.py`。
- 前端据 `is_owner` / `can_edit` 渲染只读态（后端为唯一判定方）。

### C. 发布归属校验（管理员评审意见 2）

- 发布（`POST /api/v1/{type}-releases/{release_id}/publications`）新增**资源归属校验**，
  位于范围权限校验之前：
  - `data`：必须存在且 owner = 当前用户（历史无 owner 按本机有数据启发式）；
  - `tool` / `agent`：存在且（owner = 当前用户 或 `visibility=public`）；
  - 不存在 / 无权：统一 **404「资源版本不存在或无权发布」**（fail-closed，不泄露存在性）；
- 拒绝也写审计（`publication.create` result=denied，覆盖 404 与 403 两种拒绝），
  再由路由层 commit。
- **`release_id` 必须等值于本机 registry 中的资源 id**（v2 明确）：原文档的
  "跨系统引用"语义只保留在**查询侧降级**；发布侧为了做归属校验，无法识别
  的版本号一律 404。前端手输任意版本号会被拒绝。
- 前端发布候选列表只列当前用户可发布的资源（输入框仍允许手输，后端兜底）。

### D. 其它行为变更

| 接口 | 变更 |
|---|---|
| `POST /api/v1/.../installations`（tenant 范围） | 发布记录必须属于当前激活租户，否则 404 |
| `POST /api/chat/stop` | 仅本人会话；租户 Owner/Admin 可兜底；否则 404 |
| `POST /api/data/claim-local` | 仅租户 Owner/Admin，写审计 |
| `POST /api/config/llm` | 仅租户 Owner/Admin，写审计 |
| `GET /api/llm/test-connection` | 仅租户 Owner/Admin；`base_url` 只允许 http/https，默认拒绝内网/回环（`SOTABAND_LLM_ALLOW_LOCAL=1` 显式放开） |
| `/api/file/*` | 需登录；路径必须在允许根内且不命中敏感黑名单，否则 403 |
| `/api/migrate/*` | 需登录；导入 owner=导入者 UUID；包可见性按导出者；`overwrite` 需写权限 |

### E. 已知限制：LLM 配置是安装级全局配置

`LLM_PROVIDER / LLM_API_KEY` 写在安装目录的 `.env`，被**同一安装下的所有租户**
共用。因此「仅租户 Owner/Admin 可改」只解决"普通成员不能改"，不能防止
同一安装内的多个租户管理员互相影响；多租户共用一个安装时，需通过运维手段
（独立安装/独立 OS 用户）隔离。这是当前架构的已知限制，不是遗漏。

### F. 不变的部分

- 会话 Cookie 属性（HttpOnly + SameSite=Lax + Secure 可配）、工作负载令牌格式与
  校验链、发布/安装/调用授权链、网络角色真值表、404 优先于 403 的语义。

### G. v3 终审补充口径（2026-11-04 晚）

- **平台管理员**（`SOTABAND_PLATFORM_ADMINS`，逗号分隔 user UUID，空=无人）：
  安装级全局配置（`GET/POST /api/config/llm`、`GET /api/llm/test-connection`）、
  跨用户数据集管理视图（`/api/data/*` 的 `include_others`）与
  `POST /api/data/claim-local` 仅平台管理员。理由：个人租户下"租户 Owner"
  对每个注册用户恒真，不能作为安装级管理权限依据。
- **数据集删除**：仅当 `data_path` 位于安全数据集根内（且为本人资源）才允许；
  否则 409（不再直接 rmtree 任意目录）。允许根外的历史数据集需手工清理。
- **发布撤权**：非发布者撤回 publication 需 `network.update`；tool/agent 代他人
  发布需租户 Owner/Admin；重复发布命中他人条目：private → 404、network → 409。
- **凭证/调用**：`issue_credential` 校验网络装载权限；invoke 链额外校验发起者
  租户成员/租户/用户状态。
- **删除资源**：工具/Agent/数据集删除时自动撤回其 active publication。
- **LLM 测试连接**：不跟随 HTTP 重定向、拒绝 URL userinfo（防 SSRF 重定向绕过）；
  `.env` 写入对 api_key/model 做字符白名单（防换行注入）。
- **升级兼容（历史机器身份数据集）**：8.1 之前注册的数据集 owner 为机器身份（`local-xxxx`），升级后按「未认领的历史条目」处理——本机有数据即对登录用户可见，平台管理员可用 `POST /api/data/claim-local` 批量打上 UUID 归属；不会静默失联。

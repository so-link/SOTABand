# 设计：数据表设计（8.1 / 8.2）

> 状态：待评审 ｜ 日期：2026-10-15
> 决策依据：`docs/adr-08-user-space-and-network.md`（ADR-08-01a/01b/02/03/06/08）
> 冻结契约：`docs/contract-08-auth-network.md` §2

---

## 1. 一句话回答：13 张表，分 5 批建

| 批次 | 对应步骤 | 表 | 张数 |
|---|---|---|---|
| **批次 1** | Step 1–2（认证） | `user`、`user_identity`、`user_credential`、`session`、`audit_event` | **5** |
| **批次 2** | Step 3（租户 + 邀请） | `tenant`、`tenant_membership`、`tenant_invitation` | **3** |
| **批次 3** | Step 4（网络） | `network`、`network_membership`、`network_invitation` | **3** |
| **批次 4** | Step 5（安装） | `installation` | **1** |
| **批次 5** | Step 5（发布） | `publication` | **1** |
| | | **合计** | **13** |

> **第 12 张表 `installation` 是用户 2026-10-26 拍板新增的**，理由见
> `design-08-02-network.md` §8.1：FR-N8 允许「等极短 TTL 过期」所以技术上不建表
> 也合规，但 FR-N7 要求审计「调用主体」，没有安装记录就没有稳定的工作负载主体 id。
>
> 第 13 张 `publication` 是 PRD 原计划的表，属批次 5。

### 实施进度

| 批次 | 状态 | 迁移 | 测试 |
|---|---|---|---|
| 批次 1 | ✅ **已实现** | `0001_auth_core.py` | `tests/unit/test_db_foundation.py` |
| 批次 2 | ✅ **已实现**（含 `tenant_invitation`） | `0002_tenants.py` / `0004_tenant_invitations_and_active_tenant.py` | 同上 + `test_tenant_api.py` |
| 批次 3 | ✅ **已实现** | `0003_networks.py` | 同上 + `test_network_api.py` / `test_network_concurrency.py` |
| 批次 4 | ✅ **已实现** | `0005_installation_table.py` | `test_db_foundation.py::TestInstallationTable` |
| 批次 5 | ✅ **已实现** | `0006_publication_table.py` | `test_db_foundation.py::TestPublicationTable` + `test_sharing_api.py` |

批次 1 已落地内容：`core/db/{base,session}.py`、`core/db/models/{auth,audit}.py`、
`alembic.ini`、`migrations/{env.py,script.py.mako}`。
`alembic check` 报告 *No new upgrade operations detected*（模型与迁移一致），
升降级往返已验证。

**注意批次划分与 PRD 的差异**：`audit_event` 放在**批次 1**而不是最后一批。原因是认证事件（注册、登录、失败、限流）从 Step 2 就要写审计——PRD FR-A5 要求「认证事件审计」是登录功能的一部分。等到 Step 5 才建审计表，Step 2 的审计就得事后补，容易漏。

---

## 2. 为什么是 11 张，而不是更少或更多

### 2.1 不合并的理由

| 如果合并 | 会失去什么 |
|---|---|
| 把 `user_credential` 并入 `user` | ① GitHub 登录的用户没有密码，字段必须可空，语义混乱；② 密码哈希是最高敏感字段，独立成表便于限制访问范围与单独审计 |
| 用 `users.github_id` 代替 `user_identity` | 加第二种登录方式就要加一列、改一次表；`(issuer, subject)` 三元组从第一天就支持任意多种身份并存 |
| 把 `tenant` 和 `tenant_membership` 合并 | 变成 n:m 关系就必然要中间表；合并只能表达"一个用户一个租户"，与 PRD FR-A6「用户查看所属租户，切换当前激活租户」矛盾 |
| 把 `networks.owner_id` 当作唯一的权限来源 | 无法表达多个 Owner，而 PRD FR-N4 明确要求「**最后一位** Owner 不可被移除」——复数 Owner 是设计前提 |
| 在资源上加 `is_public` 布尔字段代替 `publication` | ① 一个版本无法同时发布到两个网络；② 撤回一个网络的发布会影响另一个；③ 无法保留发布历史与撤回记录 |
| 建统一的 `resources` 表管理四类资源 | 资源本体在 JSON registry + 文件系统里，搬进数据库是被严重低估的工作量（ADR-08-08）；数据库只应存**关系**（会员、发布、审计） |

### 2.2 不拆分/不增表的理由

| 没有建的表 | 理由 |
|---|---|
| `email_verifications` | MVP 不做邮箱验证（ADR-08-01b），没有这个流程 |
| `password_resets` | 找回密码依赖可信邮箱，一并推迟 |
| `refresh_tokens` | opaque 会话令牌不需要刷新令牌机制 |
| `permissions` / `roles` | 角色是固定四值 + 权限码写在代码里（`contract` §5），引入数据驱动的 RBAC 属于过度设计 |
| `networks.settings` 独立表 | 策略项少，用 `networks.policy` JSON 列足够 |
| `resource_releases` | release 信息在 registry，数据库不重复存储 |

---

## 3. 逐表设计

> **通用约定**（ADR-08-02）：主键用 `sqlalchemy.Uuid`（跨方言）；JSON 用 `sqlalchemy.JSON`（**不用 JSONB**）；时间列用 `core.db.types.UTCDateTime`；禁用 PG 专有语法。
>
> **⚠️ 不使用外键**（ADR-08-11，阿里开发手册【强制】：「不得使用外键与级联」）：
> 下表中所有 `*_id` 列都是**逻辑引用** —— 数据库层**没有** `FOREIGN KEY`、**没有** `ON DELETE CASCADE`。
> 引用完整性由**应用层**保证（写入前校验父行存在、删除按依赖顺序显式执行）。
> ORM 关系必须显式写 `primaryjoin` + `foreign()`，因为无外键后 SQLAlchemy 无法推断连接条件。
>
> **仓库手册三字段与命名**（ADR-08-11）：
>
> - **`id` / `gmt_create` / `gmt_modified` 三列由 mixin 提供**（`UUIDPrimaryKeyMixin` + `TimestampMixin`，
>   见 `core/db/base.py`），**不是每张表手写**。下面各表的清单为完整物理结构，便于对照；
> - 表名一律**单数**，不使用复数名词；
> - 索引命名：主键 `pk_`、唯一索引 `uk_`、普通索引 `idx_`。
>
> **时间列为什么不用裸 `DateTime(timezone=True)`**：SQLite 没有时区类型，裸类型读出来是
> naive，与 `utcnow()` 比较会抛 `TypeError: can't compare offset-naive and offset-aware`
> —— 而且只在"从库里读出来的对象"上出现。`UTCDateTime` 在读写两端自动归一为 aware UTC，
> 让这类 bug 无法出现（DDL 完全相同）。

### 3.1 `user` — 用户主体

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | 不可猜测 |
| `email` | String(320) | NULL, UNIQUE | 规范化后存储（trim + 整体小写）。**可空**——GitHub 用户未公开邮箱时为空 |
| `display_name` | String(128) | NOT NULL | 为空时回退为 email 本地部分 |
| `avatar_url` | Text | NULL | Phase 2 从 GitHub 取 |
| `status` | String(16) | NOT NULL, DEFAULT 'active' | `active` / `disabled` |
| `gmt_create` | UTCDateTime | NOT NULL | UTC |
| `gmt_modified` | UTCDateTime | NOT NULL | UTC |

> ⚠️ **表名 `user` 是 PostgreSQL 保留字**（等价于 `CURRENT_USER`）。
> ORM 用法不受影响 —— SQLAlchemy 自动生成 `CREATE TABLE "user"`；
> 但**裸 SQL 必须写 `"user"`**：`SELECT * FROM user` 在 PostgreSQL 上报语法错误，
> **在 SQLite 上却能过**（典型的"开发没问题、切库才炸"）。
> 回归测试：`test_reserved_word_table_names_require_quoting`。
> 若将来觉得引号成本太高，可改名为 `user_account` / `sys_user`。

**为什么 `email` 可空**：PRD FR-A1 要求「用户未公开邮箱时留空并提示补充」。若设 NOT NULL，Phase 2 接入 GitHub 时会被迫造假数据。

**为什么没有 `email_verified`**：ADR-08-01b——MVP 不做验证，设一个永远为 `false` 的字段会误导使用者。

**唯一约束为什么重要**：并发注册同一邮箱时，靠**数据库唯一约束**保证只有一个成功。**不能靠"先查后插"**——两个请求可能都查到"不存在"然后都插入（TOCTOU）。这是 PRD FR-A1 验收标准「并发注册仅创建一个账号」的实现方式。

### 3.2 `user_identity` — 外部身份映射

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `user_id` | Uuid | 逻辑引用 `user.id`, NOT NULL | |
| `issuer` | String(32) | NOT NULL | `local`（自建）/ `github`（Phase 2） |
| `subject` | String(255) | NOT NULL | `local` 时=user_id；`github` 时=GitHub 数字 ID |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**约束**：`UNIQUE(issuer, subject)` —— PRD FR-A1 的原文要求。

**为什么用 `subject` 而不是 email 做外部主键**：PRD FR-A1 明确「不以邮箱作为外部身份主键」。GitHub 用户改邮箱后，账号不应变成两个人。

**Phase 2 的接入成本**：为已存在的 `user_id` 追加一行 `(issuer='github', subject=<数字ID>)`——**表结构不动，这正是这个设计的目的**。

### 3.3 `user_credential` — 密码凭证

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `user_id` | Uuid | 逻辑引用 `user.id`, NOT NULL, **UNIQUE** | 一人一条凭证由 `uk_user_credential_user_id` 保证 |
| `password_hash` | Text | NOT NULL | bcrypt `$2b$` 格式，约 60 字符 |
| `algorithm` | String(32) | NOT NULL | `'bcrypt'`；为将来升级到 Argon2id 预留 |
| `changed_at` | UTCDateTime | NOT NULL | **业务字段**：密码最后变更时间（改密后吊销全部会话的依据）。与 `gmt_modified` 通常同步，保留它是为了不让业务语义藏在技术字段里 |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**为什么 `user_id` 是唯一列而不是主键**：手册要求每个表都有独立的 `id` 主键，所以 `user_id` 退为普通列 + `UNIQUE`。**"一人一条凭证"的保证没有减弱** —— 唯一约束与主键在这一点上效力相同（`test_one_credential_per_user`）。

**⚠️ 两个实现要点**：

1. **bcrypt 72 字节上限**：`password_hash` 本身没问题，但**入口必须按 UTF-8 字节长度校验密码**（`len(pw.encode('utf-8')) <= 72`），否则 `bcrypt` 4.x 会直接抛 `ValueError`（ADR-08-01a）；
2. **不要用 `passlib`**：与 `bcrypt>=4.1` 不兼容。

### 3.4 `session` — 会话

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `user_id` | Uuid | 逻辑引用 `user.id`, NOT NULL | |
| `token_hash` | String(64) | NOT NULL, **UNIQUE** | SHA-256 十六进制（64 字符）。**明文永不落库** |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |
| `expires_at` | UTCDateTime | NOT NULL | 默认 gmt_create + 30 天 |
| `revoked_at` | UTCDateTime | NULL | 非空即已吊销 |
| `user_agent` | Text | NULL | 供用户识别设备 |
| `ip` | String(45) | NULL | 45 = IPv6 最大长度 |

**为什么 `token_hash` 用 SHA-256 而不是 bcrypt**：令牌本身是 256 bit 高熵随机串，不存在字典攻击空间，不需要慢哈希；而每次请求都要查会话，必须快。这与密码的处理逻辑不同，不要混为一谈。

**为什么 `id` 和 `token_hash` 分开**：`id` 是内部标识（用于"吊销指定会话"接口的 URL），`token_hash` 是认证凭据。分开可避免内部标识被当作凭据使用。

### 3.5 `audit_event` — 审计（批次 1）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `actor_id` | String(64) | NOT NULL | 用户 id 或工作负载 id |
| `actor_kind` | String(16) | NOT NULL | `user` / `workload` / `system` |
| `tenant_id` | Uuid | NULL | 可空——认证事件发生在租户确定之前 |
| `network_id` | Uuid | NULL | |
| `action` | String(64) | NOT NULL | 如 `auth.login`、`network.member.remove` |
| `target_type` | String(32) | NULL | |
| `target_id` | String(64) | NULL | |
| `result` | String(16) | NOT NULL | `success` / `failure` / `denied` |
| `request_id` | String(64) | NOT NULL | 贯穿链路 |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**`tenant_id` 为什么可空**：登录失败时还不知道（或不该记录）是哪个租户。

**只追加，不修改不删除**。保留期独立于成员删除（PRD §11）——移除成员不能删掉他做过的审计。

### 3.6 `tenant` — 租户（批次 2）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `name` | String(128) | NOT NULL | 显示名 |
| `slug` | String(64) | NOT NULL, **UNIQUE** | URL 友好标识 |
| `status` | String(16) | NOT NULL, DEFAULT 'active' | |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**`slug` 唯一而非 `name` 唯一**：租户显示名可能重名（"测试组"），slug 才是身份。

### 3.7 `tenant_membership` — 租户成员（批次 2）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `tenant_id` | Uuid | 逻辑引用, NOT NULL | |
| `user_id` | Uuid | 逻辑引用, NOT NULL | |
| `role` | String(16) | NOT NULL | `owner` / `admin` / `member` / `viewer` |
| `status` | String(16) | NOT NULL, DEFAULT 'active' | `active` / `invited` / `removed` |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**约束**：`UNIQUE(tenant_id, user_id)` —— 防重复加入（PRD FR-N3「并发接受只生成一条 membership」同源问题）。

**注册时自动写入一条 `role='owner'` 的记录**（个人租户，PRD FR-A1）。

### 3.8 `network` — 虚拟网络（批次 3）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `tenant_id` | Uuid | 逻辑引用, NOT NULL | 网络归属租户（PRD 开放问题 2 的答案：是） |
| `name` | String(128) | NOT NULL | |
| `owner_id` | Uuid | 逻辑引用 `user.id`, NOT NULL | **创建者记录，不参与授权判定**（见下方说明） |
| `status` | String(16) | NOT NULL, DEFAULT 'active' | `active` / `suspended` / `archived` |
| `policy` | JSON | NOT NULL, DEFAULT '{}' | `{allowMemberPublish, allowViewerInvoke}` |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**约束**：`UNIQUE(tenant_id, name)` —— PRD FR-N1「网络名称租户内唯一」。

**⚠️ `owner_id` 与 `network_memberships.role='owner'` 的重复问题（必须明确）**

这两处都表达了"所有者"，存在**双真值源漂移**的风险。裁定：

| 字段 | 角色 | 是否参与授权 |
|---|---|---|
| `networks.owner_id` | **创建者记录**（不可变，审计用"谁建的"） | ❌ **不参与** |
| `network_memberships.role='owner'` | **权限来源**（可变，可多人、可转移） | ✅ **唯一来源** |

**规则**：`authorize()` 只读 `network_memberships.role`，**永远不读 `networks.owner_id`**。创建网络时同时写入 `owner_id` 和一条 `role='owner'` 的 membership，此后二者可以不一致（例如转移所有权后 owner_id 仍是创建者）——这是**设计意图**，不是 bug。

**为什么保留 `owner_id`**：审计与展示需要知道"谁创建的"，而 membership 会被移除。

**为什么 `policy` 用 JSON 而不是独立列**：策略项少且会演进，独立列每加一项就要迁移。代价是无法用数据库约束校验内容——由应用层校验（有 Pydantic 模型）。

### 3.9 `network_membership` — 网络成员（批次 3）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `network_id` | Uuid | 逻辑引用, NOT NULL | |
| `user_id` | Uuid | 逻辑引用, NOT NULL | |
| `role` | String(16) | NOT NULL | `owner` / `admin` / `member` / `viewer` |
| `status` | String(16) | NOT NULL, DEFAULT 'active' | `active` / `removed` |
| `joined_at` | UTCDateTime | NOT NULL | |
| `version` | Integer | NOT NULL, DEFAULT 1 | **并发守卫**，见下 |

**约束**：`UNIQUE(network_id, user_id)` —— PRD FR-N3「并发接受邀请仅一条 membership」。

**⚠️ `version` 列是 SQLite 兼容性的关键**（ADR-08-02）：

PRD FR-N4 原文要求「最后一位 Owner 不可被移除（**事务内锁定检查**）」。但 **SQLite 没有行级锁，`SELECT ... FOR UPDATE` 是空操作**。因此改为 **compare-and-set**：

```sql
-- 调整角色：只有 version 匹配才更新，影响行数=0 即为并发冲突
UPDATE network_memberships
   SET role = :new_role, version = version + 1
 WHERE id = :id AND version = :expected_version;
-- 影响 0 行 → 409

-- 移除成员前的"最后 Owner"守卫：在同一事务内重新统计
SELECT COUNT(*) FROM network_memberships
 WHERE network_id = :nid AND role = 'owner' AND status = 'active';
```

**这条守卫在 SQLite 上是"串行写恰好掩盖了竞态"，测试通过不等于 PG 上正确**——并发用例必须在 PostgreSQL 上复验一次。

### 3.10 `network_invitation` — 邀请（批次 3）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `network_id` | Uuid | 逻辑引用, NOT NULL | |
| `email` | String(320) | NOT NULL | 规范化后存储 |
| `token_hash` | String(64) | NOT NULL, **UNIQUE** | 同会话令牌，只存 hash |
| `status` | String(16) | NOT NULL, DEFAULT 'pending' | `pending`/`accepted`/`rejected`/`expired`/`revoked` |
| `expires_at` | UTCDateTime | NOT NULL | 短期，建议 7 天 |
| `invited_by` | Uuid | 逻辑引用 `user.id`, NOT NULL | |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**为什么邀请令牌也只存 hash**：与密码重置令牌同理——数据库泄露不应让攻击者能直接接受邀请。PRD §10 原文要求「Session、重置 Token、邀请 Token 数据库只存 hash」。

**有效邀请去重**：PRD §7.2 提到「有效邀请去重」。数据库层无法用唯一约束表达"仅当 status=pending 时 (network_id, email) 唯一"（部分唯一索引 PG 支持、SQLite 也支持，但两库语法不同）。**MVP 在应用层做幂等**：同一 (network_id, email) 已有 pending 邀请则返回原邀请而不新建。

### 3.11 `publication` — 资源发布（批次 4）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | Uuid | PK | |
| `resource_type` | String(16) | NOT NULL | `agent` / `tool` / `data` |
| `release_id` | String(64) | NOT NULL | **逻辑引用**，非外键（见下） |
| `scope` | String(16) | NOT NULL | `private`/`tenant`/`network`/`public` |
| `scope_id` | Uuid | NULL | `scope='network'`→network_id；`'tenant'`→tenant_id；其余 NULL |
| `status` | String(16) | NOT NULL, DEFAULT 'active' | `active` / `revoked` |
| `published_by` | Uuid | 逻辑引用 `user.id`, NOT NULL | |
| `gmt_create` | UTCDateTime | NOT NULL | |
| `gmt_modified` | UTCDateTime | NOT NULL | |

**约束**：
- `UNIQUE(resource_type, release_id, scope, scope_id)` —— 同一版本对同一范围只发布一次（幂等）
- **CHECK**：`scope = 'network'` 时 `scope_id` 非空（PRD §7.2 原文要求）

**⚠️ `release_id` 是"跨系统"逻辑引用 —— 比表间逻辑引用更进一步**

本设计里所有 `*_id` 都是逻辑引用（ADR-08-11），但 `release_id` 还多一层：**它的目标根本不在数据库里**。资源本体（工具/Agent/数据集的版本）保存在 `resources/*/registry.json` 与文件系统中（ADR-08-08）。

后果：

- 数据库层无法保证它存在 —— 这一点**连外键也无能为力**，因为目标表根本不存在；
- **没有级联**：资源被删除后 publication 会变成悬空引用，必须由应用层清理；
- 查询时若 release 已消失，要**优雅降级**（展示为"已下架"或直接过滤），而不是抛 500。

登记在本文档 §7 已知限制。

**CHECK 约束的可移植性**：SQLAlchemy 的 `CheckConstraint` 在 SQLite 与 PG 上都可用，但 SQLite 加 CHECK 需要重建表（Alembic `render_as_batch=True` 会处理）。若迁移动手困难，可降级为应用层校验——**但要在代码里明确注释，不要静默省略**。

---

### 3.12 `installation` — 安装记录（批次 4，✅ 已落地）

> 用户 2026-10-26 拍板新增的第 12 张表。裁定理由见 `design-08-02-network.md` §8.1。

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | 会出现在「吊销这个安装」的接口里，不能可枚举 |
| `tenant_id` | UUID | NOT NULL | 逻辑引用 `tenant.id`，隔离边界 |
| `publication_id` | UUID | NOT NULL | 逻辑引用 `publication.id`（**批次 5 才落地**） |
| `installed_by` | UUID | NOT NULL | 逻辑引用 `user.id`，装进谁的工作空间 |
| `status` | VARCHAR(16) | NOT NULL, CHECK | `active` / `revoked` |
| `gmt_create` | DATETIME | NOT NULL | 阿里手册【强制】 |
| `gmt_modified` | DATETIME | NOT NULL | 阿里手册【强制】 |

**索引**：

- `uk_installation_tenant_id_installed_by_publication_id`（唯一）——
  一个用户对同一发布项只有一条记录；重复安装是**重新激活**同一行，不是新增行。
  否则审计里会出现十几条同一用户的安装记录，看不出哪次有效。
- `idx_installation_tenant_id_installed_by_status` ——「我的工作空间里有哪些已安装项」的主查询路径。
- `idx_installation_publication_id` ——「这个发布项被谁装了」，发布者下架/吊销时反查。

**为什么吊销是软删除**：审计要能回答「谁什么时候装过、什么时候被吊销」。物理删除
就把证据丢了，而这张表存在的唯一理由就是**审计可追溯**。

**已知的悬空引用**：`publication_id` 指向一张还没建的表现在不会报错 ——
无外键方案（ADR-08-11）本就不做数据库层校验，存在性由应用层保证。这不是疏漏，
是既定代价；Step 5 落地 `publication` 后由 service 层在安装前校验。

---

## 4. 索引清单

| 表 | 索引 | 用途 |
|---|---|---|
| `user` | `UNIQUE(email)` | 登录查询 + 防重复注册 |
| `user_identity` | `UNIQUE(issuer, subject)` | OIDC 登录映射 |
| `user_identity` | `INDEX(user_id)` | 查用户的所有身份 |
| `session` | `UNIQUE(token_hash)` | **认证热路径** |
| `session` | `INDEX(user_id, revoked_at)` | 「退出全部设备」「会话列表」 |
| `session` | `INDEX(expires_at)` | 过期清理任务 |
| `tenant` | `UNIQUE(slug)` | |
| `tenant_membership` | `UNIQUE(tenant_id, user_id)` | 防重复 + 权限判定 |
| `tenant_membership` | `INDEX(user_id)` | 「我的租户列表」（PRD FR-A6） |
| `network` | `UNIQUE(tenant_id, name)` | 防重名（PRD FR-N1） |
| `network` | `INDEX(tenant_id)` | 租户内网络列表 |
| `network_membership` | `UNIQUE(network_id, user_id)` | 防重复 + `resolve_network_role` 热路径 |
| `network_membership` | `INDEX(user_id)` | 「我的网络列表」 |
| `network_membership` | `INDEX(network_id, role, status)` | 统计 Owner 数量（最后 Owner 守卫） |
| `network_invitation` | `UNIQUE(token_hash)` | 接受邀请 |
| `network_invitation` | `INDEX(network_id, status)` | 邀请列表 |
| `network_invitation` | `INDEX(email)` | 「发给我的邀请」 |
| `publication` | `UNIQUE(resource_type, release_id, scope, scope_id)` | 幂等发布 |
| `publication` | `INDEX(scope, scope_id, status)` | **目录查询热路径** |
| `audit_event` | `INDEX(tenant_id, gmt_create)` | 审计查询 |
| `audit_event` | `INDEX(network_id, gmt_create)` | 网络审计 |
| `audit_event` | `INDEX(request_id)` | 链路追踪 |
| `audit_event` | `INDEX(actor_id, gmt_create)` | 「某人做过什么」 |

---

## 5. 数据量估算与容量结论

| 表 | 量级估算（团队规模） | 增长特征 |
|---|---|---|
| `user` | < 100 | 静态 |
| `user_identity` | < 200 | 静态 |
| `session` | < 1,000 | 可清理 |
| `tenant` | < 20 | 静态 |
| `tenant_membership` | < 200 | 静态 |
| `network` | < 50 | 慢 |
| `network_membership` | < 500 | 慢 |
| `network_invitation` | < 500 | 慢 |
| `publication` | < 1,000 | 随资源数增长 |
| `audit_event` | **增长最快**，< 100,000 / 年 | 只追加 |

**结论**：

1. **SQLite 完全够用**——最大的表在十万行量级，SQLite 处理百万行级无压力；
2. **不需要分区**（PRD 提到按月分区，那是百万级以上才考虑的优化）；
3. **不需要 Redis**（会话查询走唯一索引，微秒级）；
4. **唯一需要注意的是 `audit_event` 的写入频率**——若每次工具执行都写审计，量会显著上升。MVP 只对**认证、组网、共享、Agent 通信**四类关键事件写审计，不对每次工具调用写。

---

## 6. 迁移策略

### 6.1 工具与目录

```text
alembic.ini                       项目根
migrations/
  env.py                          开启 render_as_batch=True（SQLite 必需）
  script.py.mako                  迁移模板
  versions/
    0001_auth_core.py             批次 1：5 张表
    0002_tenants.py               批次 2：2 张表
    0003_networks.py              批次 3：3 张表
    0004_publications.py          批次 4：1 张表
core/db/
  __init__.py                     包导出
  base.py                         DeclarativeBase + 命名约定 + utcnow
                                  + UUIDPrimaryKeyMixin / TimestampMixin（手册三字段）
  types.py                        UTCDateTime（保证读写都是 aware UTC）
  session.py                      引擎、会话工厂、SQLite PRAGMA
  models/
    __init__.py                   必须在此 import 全部模型（否则 autogenerate 漏表）
    auth.py                       user / user_identity / user_credential / session
    audit.py                      audit_event
```

> **⚠️ `alembic.ini` 必须保持纯 ASCII（不能写中文注释）**
>
> Alembic 用 `encoding="locale"` 读这个 ini（见 `alembic/util/compat.py`）。中文
> Windows 下 locale 编码是 GBK，任何 UTF-8 中文注释都会让 Alembic 在启动前就抛
> `UnicodeDecodeError: 'gbk' codec can't decode byte ...`。
>
> **`PYTHONUTF8=1` 修不了这个** —— Python 的 UTF-8 模式改变的是
> `locale.getpreferredencoding()`，而 `locale.getencoding()`（3.11+）**无视 UTF-8
> 模式**，仍然返回真实区域编码。所以唯一的稳妥做法是让该文件不含非 ASCII 字符。
>
> 这与项目此前遇到的 GBK 编码问题同源。`migrations/*.py` 和 `script.py.mako`
> 不受影响（Python 源码与 Mako 模板按 UTF-8 读取）。

### 6.2 连接串

```bash
# 开发（默认，零配置）
DATABASE_URL=sqlite+aiosqlite:///./storage/sotaband.db

# PostgreSQL（一个变量切换）
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/sotaband
```

启用已存在但无人引用的 `config/settings.py:344` `StorageConfig.database_url`。

**`.gitignore` 已包含 `*.db` 规则**（既有预防性配置），数据库文件不会进版本库。

### 6.3 迁移与既有数据的关系

**本模块的迁移不触碰任何既有数据。** 理由（ADR-08-08）：

- 现有 48 个工具、30 个模型、数据集全部在 `resources/*/registry.json` + 文件系统；
- 新表只存**关系**（会员、发布、审计），不存资源本体；
- 因此**没有数据迁移任务**，只有建表——这是把风险降到最低的关键设计。

唯一的例外是**历史数据集条目的 owner 认领**（`POST /api/data/claim-local`）——这是**既有功能**，已经实现，本模块不改动它。

### 6.4 两种数据库的行为差异（需要在测试中覆盖）

| 差异点 | SQLite | PostgreSQL | 影响 |
|---|---|---|---|
| 行级锁 | ❌ 无（`FOR UPDATE` 空操作） | ✅ 有 | 并发守卫必须用 version 条件更新 |
| `ALTER TABLE` | 弱（需重建表） | 强 | Alembic 必须 `render_as_batch=True` |
| 字符串比较 | 默认区分大小写 | 默认区分大小写 | `email` 已在应用层小写，无影响 |
| 唯一约束冲突异常类型 | `sqlite3.IntegrityError` | `psycopg.errors.UniqueViolation` | **必须捕获 SQLAlchemy 的 `IntegrityError` 而非方言异常** |
| 布尔类型 | 存 0/1 | 原生 boolean | 用 `Boolean` 类型，勿用整数 |
| 时区 | 忽略 `timezone=True`，存 naive | 保留 tz | 应用层统一用 UTC naive 或统一加 tz——**选一种并全局一致** |
| `Uuid` 列存储格式 | `CHAR(32)`，`uuid.hex`（**无连字符**） | 原生 `uuid` 类型 | **裸 SQL 传 `str(uuid)` 会静默查不到**，必须传 `.hex` 或改用 ORM 条件 |
| 唯一约束冲突异常 | `sqlite3.IntegrityError` | `psycopg.errors.UniqueViolation` | 统一捕获 SQLAlchemy 的 `IntegrityError` |

**最后两行尤其容易出错**：建议**应用层统一产出 UTC 时间，读取时统一标注 UTC**，不要依赖数据库的时区行为；UUID 一律走 ORM 条件查询，避免裸 SQL。

两条都已有回归测试：
`tests/unit/test_db_foundation.py::test_uuid_storage_format_on_sqlite`、
`::test_user_session_is_active_semantics`。

---

## 7. 已知限制（明确记录，不隐瞒）

> **⚠️ 0. 最重要的一条：本表设计可能缺一张表。**
> PRD 的 11 张表里**没有「安装（Installation）」或「工作负载（Workload）」表**，但 PRD §3 术语表定义了 Installation，且 FR-N7（**P0**）要求 Agent 工作负载凭证「可吊销、可审计」。**没有持久化就无法可靠吊销。**
> 这是 PRD 内部的一处不一致，需要评审裁定。三种方案与建议见 `docs/design-08-02-network.md` §8.1。**若采纳新增方案，本表合计为 12 张。**

1. **全库无外键，孤儿行由数据库放行**（ADR-08-11，阿里开发手册【强制】）：凭证/会话/成员等子表可以引用不存在的父行，数据库不会拦。**必须由应用层写入前校验、删除时按依赖顺序显式清理。** 这不是缺陷，是规范选择；代价必须由应用层承担。
2. **`publications.release_id` 的目标不在数据库里**：资源被删除后 publication 会变成悬空引用。这一点连外键也无能为力（目标表不存在），只能靠应用层清理 + 查询时优雅降级。
3. **有效邀请去重在应用层**：数据库层无部分唯一索引的跨方言写法，并发下可能产生两条 pending 邀请（但不影响正确性——接受时仍靠 `UNIQUE(network_id, user_id)` 保证只产生一条 membership）。
4. **`audit_event` 无保留期策略**：MVP 不清理。需要在正式运营前确定保留期。
5. **`policy` JSON 无数据库级校验**：内容正确性由 Pydantic 模型保证。
6. **模型空间无隔离**：`model_registry.py` 当前完全没有 `owner`/`visibility`，30 个条目全部共享。不在本模块范围（见 `design-08-isolation.md` §3）。
7. **单 worker 假设**：进程内限流与可能的进程内缓存都假设单 worker 部署。

---

## 8. 待确认

1. **会话有效期**：30 天是否合适（也可 7 天）；
2. **邀请有效期**：建议 7 天，需确认；
3. **是否保留 `networks.owner_id`**：本设计保留为"创建者记录"，若评审认为双字段易混淆，可删除并改用 membership 查询；
4. **`audit_event` 的写入范围**：本设计只写四类关键事件，不含工具调用——需确认是否满足 PRD FR-N9 的审计要求；
5. **是否需要在批次 1 就建 `audit_event`**：本设计是（理由见 §1），若认为认证审计可后补，可移到批次 4。

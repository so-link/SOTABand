# 设计：8.2 用户组网

> 状态：待评审 ｜ 日期：2026-10-15
> 决策依据：`docs/adr-08-user-space-and-network.md`（ADR-08-06 / 08-07 / 08-08）
> 冻结契约：`docs/contract-08-auth-network.md` §3.2、§3.3、§5
> 数据表：`docs/design-08-schema.md`
> 上游需求：PRD FR-N1 ~ FR-N9、§9.2、§9.3

---

## 1. 需求解读：这不是物理组网

需求原文：

> 8.2 用户组网：**构成虚拟局部网络**，用户可在网络中共享工具、数据，**Agent 之间可相互通信**

最容易走偏的地方是把它理解成"做一个 VPN / 内网穿透"。PRD §2.4 已明确排除：

> 不实现 VPN、SDN、P2P 打洞、局域网广播等物理网络能力；
> 不实现 Agent 底层通信协议（RPC/消息总线传输层），仅定义网络内通信的**身份与授权**。

**正确的理解**：这是一个**平台内的虚拟协作网络**——本质是一张关系表 + 一套授权规则。

```text
"虚拟局部网络" =   成员关系（谁在里面）
              +   角色（谁有多大权力）
              +   共享资源（里面有什么）
              +   通信授权（谁能调谁）
```

**没有网络设备，没有 IP 层，没有数据传输通道。** 数据传输在 MVP 里根本不发生——所有资源都在同一台机器上。

---

## 2. 边界：8.2 做什么，不做什么

这是最容易和队友重复劳动的地方，必须说清楚。

**8.2 交付的是「网络」这个容器和授权机制**；把具体的工具/数据集/Agent 放进网络，是下游模块的任务。

| 能力 | 归属 | 说明 |
|---|---|---|
| 网络的创建、生命周期、成员、角色、邀请 | **8.2** | 本文档 |
| 网络资源的**授权判定入口** `authorize()` | **8.2** | 下游必须调用它 |
| `publication` 表与"发布/撤回"机制 | **8.2** | 提供机制 |
| 网络资源目录 `/api/v1/network-catalog` | **8.2** | 提供统一视图 |
| **工具**发布到网络的具体实现 | 7.1 工具共享 | 调用 8.2 的 publications 接口 |
| **数据**发布到网络（镜像/虚拟化） | 8.1 数据共享 | 同上 |
| **Agent** 发布到网络 | 10.1 智能体发布 | 同上 |
| 工具集市 / 数据集市 / 智能体集市 | 9.3–9.5 | scope=`public` 的发布，复用同一机制 |
| Agent 通信的物理传输 | 已在 3.2 任务编排中 | 8.2 只加授权层（ADR-08-07） |

> **给下游的一句话**：你们不需要自己写权限判断，也不需要自己建发布表。调用 `POST /api/v1/{type}-releases/{id}/publications` 发布、调用 `authorize()` 判定。

---

## 3. 核心概念

| 概念 | 定义 | 载体 |
|---|---|---|
| **租户（Tenant）** | 资源与成员的归属组织边界 | `tenant` |
| **网络（Network）** | 平台内虚拟协作网络，属且仅属一个租户 | `network` |
| **成员（Membership）** | 用户与网络的关系，带角色与状态 | `network_membership` |
| **邀请（Invitation）** | 短期、一次性的入网凭证 | `network_invitation` |
| **发布（Publication）** | 某资源版本对某范围的授权记录 | `publication` |
| **工作负载身份** | Agent 运行实例的独立短期凭证 | ⚠️ 见 §8.1（**当前表设计缺一张表**） |

### 3.1 为什么网络属于租户（PRD 开放问题 2 的答案）

PRD §15 问：「一个网络是否严格属于一个租户？」本设计答**是**。

理由：

1. **授权判定可以线性化**：先判租户（404），再判网络成员（404），再判角色（403）。若网络可跨租户，"谁能看到这个网络"就没有清晰边界，审计与合规也说不清数据的责任方；
2. **`(tenant_id, name)` 唯一约束**才能成立，PRD FR-N1「网络名称租户内唯一」才有意义；
3. 跨租户网络联盟在 PRD §2.4 里已经是 Non-Goal，留到 Phase 5。

---

## 4. 网络生命周期

### 4.1 状态机

```text
                创建（Owner = 创建者）
                        │
                        ▼
                   ┌─────────┐
      ┌───────────→│ active  │←──────────┐
      │            └────┬────┘           │
      │  恢复           │  暂停           │ 恢复
      │  (Owner)        ▼  (Owner/Admin) │ (Owner)
      │            ┌─────────┐           │
      └────────────│suspended│           │
                   └────┬────┘           │
                        │ 归档            │
                        ▼ (Owner)        │
                   ┌─────────┐           │
                   │archived │───────────┘
                   └─────────┘
```

### 4.2 各状态下的行为

| 状态 | 读（目录/详情/成员） | 写（发布/邀请/改成员） | 调用 |
|---|---|---|---|
| `active` | ✅ | ✅ 按角色 | ✅ |
| `suspended` | ✅（可读，便于排查） | ❌ **409** | ❌ **409** |
| `archived` | ❌ 仅 Owner/Admin + 审计 | ❌ **409** | ❌ **409** |

**为什么 `suspended` 仍可读**：暂停往往是为了排查问题，此时成员需要看到现状。而 `archived` 是终态（"这个网络不用了"），应表现为从目录消失。

**为什么都是 409 而不是 403**：网络状态是**暂时性**的冲突，不是权限不足。前端应提示"网络当前已暂停/归档"，而不是"权限不足"——后者会让人误以为要去改角色。

**历史审计在 `archived` 后仍保留**（PRD FR-N2）。

### 4.3 归档的不可逆性

MVP 中 `archived → active` 允许（PRD FR-N2「恢复后按策略继续」），但**不提供删除**。原因：删除网络会破坏审计追溯性。真需要清理时，管理员直接操作数据库。

---

## 5. 邀请与加入

### 5.1 状态机

```text
                     创建（Owner/Admin）
                            │
                            ▼
                     ┌────────────┐
                     │  pending   │
                     └──────┬─────┘
          ┌────────────┬────┴─────┬──────────────┐
          ▼            ▼          ▼              ▼
     accepted     rejected    expired       revoked
    （加入成功）   （拒绝）   （到期自动）  （Owner/Admin 撤销）
          │                                                   
          └─→ 写入 network_memberships（role=member）
```

**终态不可逆**：`accepted` / `rejected` / `expired` / `revoked` 都不再有出边。这是 PRD FR-N3 的要求（「邀请被撤销、网络停用、Token 过期后不可接受」）。

### 5.2 接受邀请的完整流程

```text
POST /api/v1/network-invitations/{token}/accept

1. 已认证？（401 否则）
2. hash = SHA-256(token)
3. 事务开始：
     a. SELECT ... WHERE token_hash = hash        （加锁语义由下方条件更新实现）
     b. 校验 status == 'pending'                  → 410
     c. 校验 expires_at > now()                   → 410（并置 expired）
     d. 校验 network.status == 'active'           → 409
     e. 校验 邀请 email == ctx 用户的 email        → 403
     f. UPDATE invitation SET status='accepted' WHERE id=? AND status='pending'
        ↑ 影响 0 行 → 说明并发已接受 → 409 或直接返回成功（幂等）
     g. INSERT network_memberships (role='member')
        ↑ UNIQUE(network_id, user_id) 冲突 → 已是成员 → 返回成功（幂等）
   COMMIT
4. 写审计 network.invitation.accept
5. 200 {network, role}
```

**三个并发要点**（PRD FR-N3 验收标准）：

| 要点 | 实现 |
|---|---|
| 同一 Token 二次使用 | 第 f 步的状态条件更新（compare-and-set），影响 0 行即拒绝 |
| 并发接受只生成一条 membership | 第 g 步的 `UNIQUE(network_id, user_id)` |
| 重复点击邀请幂等 | 第 f 步影响 0 行时**返回成功**而非报错（用户视角：我已经进去了） |

**为什么第 b/c 步要在事务内重新读**：如果先在事务外校验再进事务，两次请求可能都通过了校验。

### 5.3 邀请邮箱与账号匹配

PRD FR-N3：「邀请邮箱与登录账号匹配校验」。

规则：`invitation.email` 必须等于 `ctx` 用户的 `email`（规范化后比较）。

**边界情况**：GitHub 登录的用户 `email` 可能为空（PRD FR-A1）。此时无法匹配邮箱 → **拒绝接受邀请**，并提示用户先补充邮箱。这是 Phase 2 才需要处理的问题，MVP 自建路径下 `email` 必定存在。

### 5.4 幂等性

| 操作 | 幂等行为 |
|---|---|
| 重复邀请同一邮箱 | 已有 `pending` 邀请则**返回原邀请**，不新建（PRD FR-N3「重复邀请幂等」） |
| 已是成员再被邀请 | 返回一致结果，不报错 |
| 重复接受 | 返回成功 |
| 重复撤销 | 返回成功 |

---

## 6. 角色与权限

### 6.1 四角色

| 角色 | 定位 | 可由谁授予 |
|---|---|---|
| `owner` | 完全控制，包括归档与读审计 | 仅 Owner（转移所有权） |
| `admin` | 协助管理，**不可动 Owner、不可授予 Owner** | Owner / Admin |
| `member` | 使用网络资源 | Owner / Admin |
| `viewer` | 只读，可看可装，**默认不可调用** | Owner / Admin |

完整权限真值表见 `contract-08-auth-network.md` §5.2（**唯一真值源**，本文档不重复）。

### 6.2 三条必须用代码强制的规则

**规则 1：最后一位 Owner 不可被移除或退出**

```python
# 移除成员前，在同一事务内重新统计
owner_count = SELECT COUNT(*) FROM network_memberships
              WHERE network_id = ? AND role = 'owner' AND status = 'active'
if target.role == 'owner' and owner_count <= 1:
    raise HTTPException(409, "不能移除最后一位 Owner")
```

⚠️ **在 SQLite 上，这个检查靠串行写才成立**——SQLite 无行级锁，`SELECT FOR UPDATE` 是空操作。必须配合 `version` 条件更新，并且**并发用例要在 PostgreSQL 上复验**（ADR-08-02、`design-08-schema.md` §3.9）。

**规则 2：普通成员不能改角色、不能移除他人**

由 `authorize(ctx, permission="network.members.manage", network_id=...)` 统一拦截（真值表里 member/viewer 对该权限码为 ❌）。

**规则 3：Admin 不能对 Owner 行使管理动作**

这是真值表里的"受限"格：

```python
if ctx.role == 'admin' and target.role == 'owner':
    raise HTTPException(404)        # 对 Admin 来说，Owner 成员"不存在"
if ctx.role == 'admin' and new_role == 'owner':
    raise HTTPException(403)        # 不能授予 Owner
```

**为什么第一条返回 404 而不是 403**：避免向 Admin 暴露"这里有一个我动不了的 Owner"这一信息。虽然 Admin 能从成员列表看到，但**管理动作接口**不应泄露可操作性的差异。

### 6.3 主动退出

```text
POST /api/v1/networks/{network_id}/leave
```

- 任何成员都可以退出自己（不需要 `network.members.manage`）；
- 但若是**最后一位 Owner**，拒绝退出（409），提示"请先转移所有权或归档网络"；
- 退出后 `network_memberships.status = 'removed'`（软删除，保留审计线索）。

---

## 7. 资源共享模型

### 7.1 四类 scope

PRD FR-N5 明确要求用独立的 `publication` 表，**而不是资源上的 `is_public` 布尔字段**：

| scope | `scope_id` | 谁能看到 | 典型场景 |
|---|---|---|---|
| `private` | NULL | 仅 `owner` | 我刚做出来，还没想好要不要共享 |
| `tenant` | `tenant_id` | 同租户全部成员 | 团队内部资产 |
| `network` | `network_id` | 该网络成员 | PRD 8.2 的"虚拟局部网络" |
| `public` | NULL | 所有人 | 集市（9.3–9.5） |

### 7.2 为什么不能用一个布尔字段

| 需求 | 布尔字段能否满足 |
|---|---|
| 同一版本同时发布到 N1 和 N2 | ❌ |
| 只撤回 N1 的发布，N2 不受影响 | ❌ |
| 保留发布历史与撤回记录 | ❌ |
| 发布到 A 网络与 B 网络的**审核状态**不同 | ❌ |
| 同一版本既有网络发布又有公开集市发布 | ❌ |

这五条都来自 PRD FR-N5 与 §12.3 的验收用例。

### 7.3 发布与撤回

```text
POST   /api/v1/{type}-releases/{release_id}/publications   {scope, scope_id?}
   → 校验：发布者有该资源的发布权（资源 owner 或 Agent/Tool 的授权）
   → 校验：scope='network' 时，发布者是该网络成员且角色允许 network.resource.publish
   → UNIQUE(resource_type, release_id, scope, scope_id) 保证幂等
   → 201 {publication}

DELETE /api/v1/publications/{publication_id}
   → 校验 network.resource.publish
   → status='revoked'（软删除，保留审计）
   → 目录立即不可见；但**历史已安装记录不受影响**（PRD FR-N5）
```

### 7.4 与既有 `visibility` 字段的关系

**关键裁定**（ADR-08-08）：两者共存，各管一段，**不做迁移**。

| | `visibility`（既有） | `publication`（新增） |
|---|---|---|
| 位置 | `resources/*/registry.json` 的条目字段 | 数据库表 |
| 语义 | 资源条目的**默认**可见性 | 某版本对某范围的**授权发布** |
| 谁读 | 既有 `/api/tool/list` 等接口 | `/api/v1/network-catalog` |
| 现存行为 | 数据集 private、工具/Agent public | — |

**48 个工具、30 个模型、现有数据集的既有行为完全不变。** 网络目录是**另一条**查询路径，读 `publication`。

### 7.5 网络资源目录

```text
GET /api/v1/network-catalog?network_id=...
```

查询逻辑：

```sql
SELECT ... FROM publications p
 WHERE p.status = 'active'
   AND (
        (p.scope = 'network' AND p.scope_id = :network_id)      -- 本网络发布
     OR (p.scope = 'tenant'  AND p.scope_id = :ctx_tenant_id)   -- 租户级发布
     OR (p.scope = 'public')                                     -- 公开
   )
   AND :ctx_user_is_active_member_of_network(:network_id)
```

**⚠️ 必须先判定成员身份再返回**——非成员查询该网络目录返回 **404**，而不是空列表（空列表会泄露"这个网络存在"）。

**详情接口必须复用同一授权**（`design-08-isolation.md` §4）。

---

## 8. 网络内 Agent 相互通信

这是需求后半句「Agent 之间可相互通信」的落点，也是 PRD FR-N7 的全部内容。

### 8.1 ⚠️ 表设计缺口 —— ✅ **已拍板：新增第 12 张表 `installation`（2026-10-26）**

**PRD 的 11 张表里没有「安装（Installation）」或「工作负载（Workload）」表。**

但：

- PRD §3 术语表定义了「安装（Installation）—— 成员将网络共享的 Agent/Tool/Data 安装到自己的工作空间」；
- PRD §7.1 ER 概览里没有它的位置；
- `contract-08-auth-network.md` §3.3 却已经定义了 `/installations` 接口；
- FR-N7 要求工作负载凭证「可吊销、可审计、短期有效」——**没有持久化就无法可靠吊销**。

**这是 PRD 内部的一处不一致。三种方案与最终裁定：**

| 方案 | 做法 | 结果 |
|---|---|---|
| A. 不新增表 | "安装"复用 `publication`：`scope='private'`, `scope_id=用户id` 即表示"我把它装进自己空间" | ❌ 未采纳 |
| **B. 新增第 12 张表 `installation`** | 记录 `(tenant_id, publication_id, installed_by, status, ...)`，凭证与之关联 | ✅ **已采纳** |
| C. 推迟到 Phase 5 | MVP 只做"网络内 Agent 用成员身份通信" | ❌ 未采纳 |

**裁定理由（关键的一条）**：方案 A 并非不合规 —— **FR-N8 原文是「吊销**或**等待极短有效期结束」**，
所以"极短 TTL + 不持久化"在 PRD 自己的验收标准下是允许的。

但 **FR-N7 要求「每次通信记录调用主体、目标、权限与结果」**。没有安装记录就没有稳定的
**工作负载主体 id**，审计日志只能写匿名标识，追溯链断掉。**审计是 P0 且不可退让**，
因此新增第 12 张表。

**已落地**：`core/db/models/sharing.py::Installation` + 迁移 `0005_installation_table.py`
（`UNIQUE(tenant_id, installed_by, publication_id)`，软吊销保留行，0 外键）。
`publication` 表仍是 Step 5 的批次 5；`installation.publication_id` 是逻辑引用，
等该表落地后一起构成完整共享链路。

> **注意**：`design-08-schema.md` §1 的合计已同步为 12 张；`contract-08-auth-network.md`
> §2.2 需追加 `installation` 一行。

### 8.2 用户身份 ≠ Agent 运行身份

PRD FR-N7 的核心要求：

> 用户成员身份表示「某人可管理/使用网络资源」，Agent 运行身份表示「某运行实例可调用什么」，二者**不复用个人长期 Token**

| | 用户身份 | Agent 工作负载身份 |
|---|---|---|
| 载体 | 会话 Cookie | 短期工作负载凭证 |
| 生命周期 | 30 天 | 分钟级 |
| 撤销 | 登出 / 禁用 | 安装停用 / 成员退出 / 密钥轮换 |
| 权限 | 按角色（真值表） | 按**目标白名单**（最小 scope） |
| 能否调用 Agent | ✅ | ✅ |
| 能否管理成员 | ✅（按角色） | ❌ **禁止** |

**明确禁止**：Agent 用用户的会话 Cookie 发起 Agent 间调用 → **401**（`contract` §5.4）。

### 8.3 通信授权链

```text
Agent A  →  调用 Agent B
   │
   ├─ 1. 携带工作负载凭证（不是用户 Cookie）
   ├─ 2. 校验凭证：存在？未吊销？未过期？
   ├─ 3. 校验 A 与 B 的安装都属于同一网络 N，且 N 为 active
   ├─ 4. 校验 B 的调用白名单允许 A 调用
   ├─ 5. 速率限制
   ├─ 6. 记录审计：caller=workload_A, target=workload_B, network=N, result
   └─ 7. 放行 → 走既有 InProcessMessageBus（ADR-08-07，不换传输层）
```

#### ✅ 实际实现（2026-10-27，批次 7 已补齐全部遗留）

实现见 `core/sharing/service.py::authorize_agent_invocation` +
`core/agent/invoke_bus.py` + `core/auth/workload.py`，
验收测试见 `tests/integration/test_agent_invoke.py`（**39 项**）。

| 步骤 | 状态 | 说明 |
|---|---|---|
| 1 工作负载凭证 | ✅ | `POST /installations/{id}/credential` 签发，`Authorization: Bearer`；**用 Cookie 调用返回 401** |
| 2 校验凭证 | ✅ | HMAC 签名 + 过期；**但撤权不依赖这一步**（见下） |
| 3 同一网络 + active | ✅ | 且额外校验「来源 publication 属于本次请求的网络」，防跨网络越权 |
| 4 白名单 | ✅ **两级** | 网络级（步骤 7）+ **按目标粒度的 allowlist**（步骤 8） |
| 5 速率限制 | ✅ | 按**调用方安装实例**计数，默认 120 次/分钟；超限 429 + `Retry-After` |
| 6 审计 | ✅ | `actor_kind='workload'`、`actor_id=installation_id`；**拒绝也写**（`result='denied'`） |
| 7 走消息总线 | ✅ **已接线** | 授权通过后经 `InvocationBus` 投递并等待响应；离线/超时不算错误 |

**第 4 步：两级白名单**

批次 5 只有网络级白名单（"目标发布到本网络 → 任何有 invite 权限的成员都能调"），
当时如实记为简化。批次 7 补上按目标的那一半：

| `publication.invoke_policy` | 含义 |
|---|---|
| `network`（默认） | 网络内所有有 `network.agent.invoke` 权限的成员都能调 |
| `allowlist` | 只有 `invoke_allowlist`（成员 `user_id` 列表）里的人能调 |

- 发布者本人可改；网络 Owner/Admin 也可改（兜底 `network.update`）；
- 名单里的人必须**当前**就是该网络的 active 成员（防止先塞外部 id、等人加进来后自动获权）；
- 名单不能为空（空名单 = 谁都调不了，是配置错误）；
- 存储为 `publication.invoke_allowlist`（JSON 数组，迁移 `0007`）——
  白名单规模小且只整体读写，开新表不划算。

接口：`PATCH /api/v1/publications/{id}/invoke-policy`；前端在网络资源目录里
对**自己发布的**资源显示盾牌图标，可切换「网络内所有成员 / 仅指定成员」并勾选成员。

**第 5 步：速率限制**

白名单解决"能不能调"，限流解决"能调多快"。两者缺一不可 —— 白名单放行之后目标方
**没有任何拒绝手段**，一个写错的 Agent（`while true: call(...)`）会把目标打到过载，
典型的"吵闹邻居"。

维度选 **installation** 而不是 IP 或用户：滥用主体是某个跑飞的 Agent 进程，
正好对应一个安装实例。同一个人多个工作空间互不影响。
限流在**授权之前**执行 —— 超限请求连数据库查询都不做（它本质是 DoS 流量）。

**第 7 步：消息总线接线**

`core/agent/invoke_bus.py` 在已有的 `MessageBus` 抽象（ADR-08-07）之上做了一件事：
把授权通过的调用投递给订阅了该目标的目标 Agent，并等待响应。

```text
agent.invoke.<resource_type>.<release_id>    请求频道（目标订阅）
agent.invoke.reply.<correlation_id>          响应频道（调用方临时订阅）
```

`/invoke` 的响应因此多了三个字段：

| 字段 | 含义 |
|---|---|
| `delivered` | 是否真的送到了目标 Agent |
| `delivery_status` | `ok` / `offline` / `timeout` |
| `response` | 目标 Agent 的响应体 |

**离线与超时都返回 200，不是错误**：授权已经成立，只是对方没在线（`offline`）
或卡住了（`timeout`），调用方稍后重试即可。混成一个 4xx 会让调用方去排查错误方向。

> 关于 PRD §2.4 把「Agent 底层通信协议（RPC/消息总线传输层）」列为非目标：
> 本实现**没有引入任何新的传输层依赖**，只是把已有的 `MessageBus` 接到授权结果上；
> 传输实现可替换（换成跨机的 `MessageBus` 实现即可，`InvocationBus` 不用改）。

**关于「撤权不依赖第 2 步」**：凭证本身是短期的（默认 10 分钟），但**撤权不等它过期**
—— 判定链每一步都现查数据库。所以卸载安装 / 撤回发布 / 移除成员 / 降级角色 /
暂停网络之后，**下一次调用立刻失败**。这是 FR-N8 的核心，测试逐条覆盖了这 5 条路径。

**令牌形状**：`w1.<installation_id>.<exp>.<jti>.<HMAC-SHA256>`

为什么不违反 `core/auth/tokens.py` 里「不用 JWT，因为无法即时吊销」那条决策——
因为工作负载令牌**不是通行证而是工牌**：签名只防伪造 `installation_id`，
**不承载任何权限**，每次调用都重新从数据库推导授权。详见该模块文档的对照表。

### 8.4 传输层：复用，不重建

`core/agent/bus.py` 已有 `InProcessMessageBus`（Pub/Sub），`core/agent/process.py` 已实现 `agent.{id}.task` 订阅 + `reply_to` 回发。**链路已经通了。**

本模块**只在其上包一层授权**：

```text
授权层（新增）          传输层（既有，不动）
─────────────          ──────────────────
校验凭证、白名单   →    InProcessMessageBus.publish()
记录审计                AgentProcess._handle_task()
```

> 注意：`bus.py` 的模块注释声称「提供 Pub/Sub 和 RPC 两种通信模式」，但**实际只实现了 Pub/Sub**，RPC 并不存在。不要被注释误导。

---

## 9. 撤权规则

PRD FR-N8 的规则表，落到实现：

| 触发事件 | 目录/详情 | 新安装/调用 | 工作负载凭证 | SSE 长连接 | 排队任务 | 审计 |
|---|---|---|---|---|---|---|
| 成员退出 / 被移除 | 立即 404 | 立即 404 | 立即吊销 | 断流 | 执行前校验拒绝 | **保留** |
| 网络 suspended | 可读 | 409 | 不变 | 不变 | 执行前校验 | 保留 |
| 网络 archived | 404 | 409 | 吊销 | 断流 | 拒绝 | **保留** |
| publication 撤回 | 立即 404 | 404 | 吊销 | — | 拒绝 | 保留 |
| 用户被禁用 | — | 401 | 吊销 | 断流 | 拒绝 | 保留 |
| 安装被停用 | — | 403 | 吊销 | — | 拒绝 | 保留 |

### 9.1 为什么"立即"能做到

**因为不做权限缓存**（`design-08-isolation.md` §8）：每次请求都读事实源。这是用性能换正确性的主动取舍——在本项目规模下性能根本不是问题，而撤权延迟是 PRD 的硬指标。

### 9.2 SSE 长连接的复查

`chat_routes.py` 已有 SSE 流式输出。撤权后不能等连接自然结束。规则：

- SSE 流中**每个事件发送前**检查一次会话有效性（成本可忽略）；
- 网络相关的流还需检查 membership 状态；
- 失效时发送 `event: revoked` 并关闭连接，让前端明确知道原因而不是"连接莫名其妙断了"。

### 9.3 排队任务

**入队时校验不够**。任务在队列里等待期间，权限可能已经变化。因此：**执行前必须重新校验一次**（PRD FR-N8 明确要求）。

---

## 10. 验收标准

### 10.1 网络与成员（PRD §12.2）

- [ ] 创建者成为唯一 Owner，网络绑定正确租户
- [ ] 同一租户内重名网络被拒
- [ ] Alice 创建 N1 邀请 Bob → Bob 可访问网络资源
- [ ] 普通成员不能邀请、不能改角色、不能移除他人
- [ ] 移除最后一位 Owner 被拒（409）
- [ ] 最后一位 Owner 退出被拒（409）
- [ ] 并发改角色：仅一个成功，另一个 409
- [ ] 并发接受邀请：仅产生一条 membership
- [ ] 重复点击邀请/接受不产生重复关系（幂等）
- [ ] 网络 suspended 后邀请不可接受、发布被拒（409）
- [ ] 网络 archived 后所有写操作被拒，审计保留

### 10.2 Agent 通信（PRD FR-N7）

- [ ] Agent A 仅能调用白名单内的目标
- [ ] Agent 用用户会话 Cookie 发起调用 → 401
- [ ] 成员退出后，其 Agent 通信在 SLA 内被拒
- [ ] 审计可还原完整调用链（caller → target → result）

### 10.3 资源共享（PRD §12.3）

- [ ] 同一 release 发布到两个网络，互不影响
- [ ] 只撤回一个 publication，另一网络仍可见
- [ ] 用户退出 N1 但仍属 N2，N2 的权限不受影响
- [ ] Agent 调用期间成员被移除 → 拒绝
- [ ] 后台任务入队后权限变化 → 执行前拒绝

### 10.4 隔离（PRD §12.2，与 `design-08-isolation.md` §9 共用）

- [ ] Carol（同租户非成员）在列表/详情/安装/调用**全部失败**
- [ ] 跨租户 Dave 全部失败
- [ ] 猜 ID 访问失败
- [ ] 伪造 header/body 中的 `tenant_id` / `user_id` / `network_id` 全部失败

> ⚠️ **并发相关用例必须在 PostgreSQL 上复验**（ADR-08-02）。

---

## 11. 待确认

1. **⚠️ 最重要：是否新增第 12 张表 `installations`**（§8.1）——FR-N7 是 P0，而当前 11 表设计无法持久化工作负载凭证，也就无法可靠吊销；
2. **工作负载凭证的形式**：opaque 令牌查库 / 签名 JWT（自带过期，但难吊销）；建议前者，与用户会话一致；
3. **白名单的表达能力**：MVP 用"全部 / 指定列表"两档，还是需要更复杂的策略；
4. **网络是否允许删除**：本设计不提供删除（保审计），需确认；
5. **`suspended` 状态下是否该允许读目录**：本设计允许（便于排查），若评审认为应完全不可见则改为 404；
6. **邀请默认有效期**：建议 7 天；
7. **Admin 能否归档网络**：真值表里 `network.archive` 仅 Owner。若认为 Admin 也应可归档，需改真值表（会连带影响契约）。

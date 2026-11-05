# 设计：数据隔离与授权

> 状态：待评审 ｜ 日期：2026-10-15
> 决策依据：`docs/adr-08-user-space-and-network.md`（ADR-08-04 / 08-06 / 08-08）
> 冻结契约：`docs/contract-08-auth-network.md` §5、§6
> 上游需求：PRD FR-A6 / FR-N5 / FR-N6 / FR-N8、§9.4

---

## 1. 先厘清一个概念：隔离 ≠ 权限

这两件事经常被混为一谈，但它们的失败模式完全不同：

| | 隔离（Isolation） | 权限（Permission） |
|---|---|---|
| 回答的问题 | 「这个东西**存不存在**于我的世界」 | 「这个动作我**能不能做**」 |
| 边界 | 归属、租户、网络 | 角色、策略 |
| 失败响应 | **404**（不能泄露存在性） | **403**（可以知道自己无权） |
| 典型漏洞 | IDOR（猜 ID 直取详情） | 越权（普通成员改自己角色） |

**两者必须同时做，且判定顺序不能颠倒**：先判断「可见吗」，再判断「能做这个动作吗」。如果顺序反了，403 的响应本身就会泄露资源存在性。

PRD FR-N6 把这个区分写得很清楚：

> `can_install`、`can_invoke` 为额外权限，**不简单等同 can_view**

---

## 2. 四条隔离边界

SOTABand 的资源隔离由四条边界叠加而成，从外到内：

```text
┌─ L1 实例边界 ────────────────────────────────────────────┐
│  数据物理在本机 → 他人环境根本访问不到                     │
│  ┌─ L2 租户边界 ──────────────────────────────────────┐  │
│  │  tenant_id 不同 → 视为不存在（404）                 │  │
│  │  ┌─ L3 用户边界 ────────────────────────────────┐  │  │
│  │  │  owner 不同 + 资源为 private → 不可见         │  │  │
│  │  │  ┌─ L4 网络边界 ──────────────────────────┐  │  │  │
│  │  │  │  publication.scope = network            │  │  │  │
│  │  │  │  且调用者非该网络成员 → 不可见（404）    │  │  │  │
│  │  │  └────────────────────────────────────────┘  │  │  │
│  │  └──────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### L1 · 实例边界（已由物理事实保证）

数据集 `data_path` 指向本机路径。从他人环境复制项目时会带过来对方的注册记录，而那些路径在本机不存在——`core/user_context.py` 的模块文档把这类条目称为「**幽灵数据集**」（能选中、一运行就报路径不存在）。

**这层不需要新代码**，但解释了为什么数据集默认 `private` 是物理事实而非权限偏好。

### L2 · 租户边界（新增，Step 3）

- 所有租户资源显式携带 `tenant_id`；
- 访问不属于当前激活租户的资源 → **404**；
- 切换激活租户必须服务端校验 membership，客户端提交的 `tenant_id` 只是**意向**（PRD FR-A6）。

### L3 · 用户边界（部分已实现）

`owner` 字段决定「谁能改/删」，`visibility` 决定「谁能看」——这两个是**正交**的（`core/user_context.py` 明确写了这一点）。

### L4 · 网络边界（新增，Step 4–5）

网络共享通过 `publication` 表表达，而不是资源上的布尔字段。同一资源可以发布到多个网络，每个 publication 独立状态。

---

## 3. 现状盘点：四类资源的隔离程度**并不一致**

这是最容易被忽略、也最容易出漏洞的地方。经逐文件核实：

| 资源空间 | `owner` | `visibility` | 当前隔离行为 | 代码位置 |
|---|---|---|---|---|
| **数据集** | ✅ 有 | ✅ `private`（默认） | **按 owner 严格隔离**，含历史条目认领启发式 | `data_registry.py`（`is_visible` L169-197、`register` L246-247、`list_all` L283-294） |
| **工具** | ✅ 有 | ✅ `public`（默认） | **全局共享**，owner 仅用于标记 `is_user_generated` | `tool_registry.py:154-157`、`:213` |
| **Agent** | ✅ 有 | ✅ `public`（默认） | **全局共享** | `agent_registry.py:113-116`、`:156` |
| **模型** | ❌ **无** | ❌ **无** | ⚠️ **零隔离——模型空间没有任何归属或可见性概念** | `model_registry.py`（grep 无 `owner`/`visibility`） |

**模型空间的缺失是一个真实缺口**，且不在当前 PRD 的 11 张表讨论范围内。两种处置：

1. **本模块暂不处理**（推荐 MVP）：模型空间当前 30 个条目全部是共享资产，无私有需求；把隔离模型留给后续需求；
2. 与工具/Agent 对齐：加 `owner` + `visibility=public`。成本很低，但要改 `model_registry.py` 和 `model_routes.py`。

> **建议**：写进 ADR 待办，但**不纳入 8.1/8.2 的交付范围**——避免范围蔓延。若评审要求四类资源隔离一致，再单独评估。

### 数据集的特例：历史条目认领

`data_registry.py` 里有一段平滑迁移逻辑（L169-197、L199-220）：早期注册的数据集没有 `owner` 字段。规则是：

- 有 `owner` → 精确匹配；
- 无 `owner` 但**本机确实存在该数据目录** → 视为本人所有；
- 提供 `POST /api/data/claim-local` 一次性认领接口（`data_routes.py:399`）。

**这是必要的历史包袱**，改动这块要格外小心。属于 8.1/8.2 之外的既有逻辑，**不要顺手"重构"**。

---

## 4. 核心原则：四个检查点必须同源

PRD §8.3 的原话：

> ⚠️ 列表、详情、安装、调用必须复用同一授权策略。只在目录列表过滤、详情接口可猜 ID 访问，是典型 IDOR 漏洞。

这是本文档**最重要的一条**。展开为工程约束：

```text
                    ┌──────────────────────┐
                    │  authorize(ctx, ...)  │  ← 唯一授权入口
                    └──────────┬───────────┘
          ┌────────────┬───────┴────┬────────────┐
          ▼            ▼            ▼            ▼
       LIST         DETAIL       INSTALL       INVOKE
    目录列表        资源详情       安装          调用
```

**结构性保证**：所有四个检查点必须调用**同一个** `authorize()`（定义见 `contract-08-auth-network.md` §1.3），**禁止各路由自行写 if-else**。

为什么强调"结构性"：靠代码评审发现"某个路由忘了过滤"是不可靠的。正确做法是让**忘记过滤变得不可能**——路由层根本不接触 registry 的原始查询，而是调用统一的服务层函数，服务层内部强制注入 `ctx`。

### 反例（必须避免的写法）

```python
# ❌ 列表过滤了，详情没有 → IDOR
@router.get("/catalog")
async def list_catalog(ctx = Depends(get_auth_context)):
    return [r for r in all_releases() if visible(ctx, r)]

@router.get("/catalog/{release_id}")
async def get_detail(release_id: str):        # ← 缺 ctx！任何人猜到 ID 即可取详情
    return get_release(release_id)
```

```python
# ❌ 各路由自己实现判定 → 规则漂移，改一处漏一处
if membership.role in ("owner", "admin"):     # 与 authorize() 里的规则重复且不一致
    ...
```

```python
# ✅ 统一入口
@router.get("/catalog/{resource_type}/{release_id}")
async def get_detail(resource_type: str, release_id: str,
                     ctx: AuthContext = Depends(get_auth_context)):
    await authorize(ctx, permission="network.resource.install",
                    network_id=network_id, resource=(resource_type, release_id))
    ...
```

---

## 5. 隔离与授权矩阵

### 5.1 资源可见性（谁能"看到"）

| 资源 | `private` | `tenant` | `network` | `public` |
|---|---|---|---|---|
| 数据集 | 仅 `owner` | 同租户全部成员 | 该网络成员 | 所有人 |
| 工具 | 仅 `owner` | 同租户全部成员 | 该网络成员 | 所有人 |
| Agent | 仅 `owner` | 同租户全部成员 | 该网络成员 | 所有人 |
| 模型 | —（当前无隔离字段） | — | — | 当前实际为所有人 |

### 5.2 动作层次（"看到"之后的三个独立动作）

PRD FR-N6 要求三个动作**分别授权**：

| 动作 | 权限码 | owner | admin | member | viewer |
|---|---|:---:|:---:|:---:|:---:|
| 浏览目录 / 详情 | `network.read` | ✅ | ✅ | ✅ | ✅ |
| 安装到自己的工作空间 | `network.resource.install` | ✅ | ✅ | ✅ | ✅ |
| 调用（执行） | `network.agent.invoke` | ✅ | ✅ | ✅ | ⚠️ 策略¹ |

¹ `networks.policy.allowViewerInvoke`，默认 `false`。

**为什么 install 和 invoke 要分开**：安装是"把定义复制到我这儿"，调用是"消耗对方资源"。Viewer 可以看和装（了解能力），但不该消耗发布方的算力/配额。这是 PRD 开放问题 3 的答案。

### 5.3 网络资源 × 调用者身份

| 调用者 | 目录 | 详情 | 安装 | 调用 |
|---|---|---|---|---|
| 网络成员（有权限） | ✅ | ✅ | ✅ | ✅ |
| 网络成员（Viewer，策略关闭调用） | ✅ | ✅ | ✅ | ❌ `403` |
| **同租户但非网络成员** | ❌ `404` | ❌ `404` | ❌ `404` | ❌ `404` |
| **跨租户** | ❌ `404` | ❌ `404` | ❌ `404` | ❌ `404` |
| **未认证** | ❌ `401` | ❌ `401` | ❌ `401` | ❌ `401` |
| 猜 ID（任意未授权 ID） | — | ❌ `404` | ❌ `404` | ❌ `404` |

---

## 6. 404 还是 403：判定规则

这是隔离设计里最容易做错的一处。**规则只有一条**：

> **调用者是否有权知道"这个东西存在"？有 → 403；没有 → 404。**

具体到场景：

| 场景 | 响应 | 理由 |
|---|---|---|
| 未认证 | `401` | 认证状态本身不敏感 |
| 资源对调用者可见，但角色不允许该动作 | `403` | 调用者已经知道它存在（列表里见过），403 不泄露新信息 |
| 网络对调用者不可见（非成员） | `404` | 若返回 403，等于确认了「这个网络存在」 |
| 网络存在但属于其他租户 | `404` | 同上 |
| publication 已撤回 | `404` | 撤回后应表现为"从未发布过" |
| 资源 ID 格式合法但不存在 | `404` | — |
| 资源 ID 格式非法（如非 UUID） | `400` | 格式错误与不存在是两件事，但要小心：**别让 400 与 404 的差异成为存在性探针** |

**易错点**：同一资源的「不可见」与「不存在」必须返回**完全一致**的响应体、状态码，甚至**响应时间**。如果"无权限"路径多查了一次数据库，耗时差异就可能被用来探测存在性。

### 一个反直觉但必要的做法

对于**敏感**资源（如 private 数据集），PRD FR-N6 要求：

> 对敏感私有资源统一返回 404 避免泄露存在性

即使调用者**是同租户成员**，只要无权限，也返回 404 而非 403。判断标准是「泄露存在性本身是否构成信息泄露」——如果资源名/描述本身敏感，答案是肯定的。

---

## 7. IDOR 防护

### 7.1 攻击面

所有「用 ID 直取」的接口都是候选攻击面。本项目当前有：

| 接口族 | 示例 | 风险 |
|---|---|---|
| 数据集 | `GET /api/data/{dataset_id}`、`/api/data/{id}/files` | 已有 owner 过滤（`data_routes.py:370-379`） |
| 工具 | `GET /api/tool/{tool_id}` | 共享资源，风险低 |
| Agent | `GET /api/agent/{agent_id}` | 共享资源，风险低 |
| 模型 | `GET /api/model/{model_id}` | **无隔离** |
| 文件 | `GET /api/file/...` | ⚠️ **需重点审查**——文件接口常按路径直取 |
| 会话 | `DELETE /api/v1/me/sessions/{session_id}` | 新增，必须校验 session 属于 `ctx.user_id` |

### 7.2 防护清单

- [ ] **不可猜测的 ID**（UUID）——必要但**不充分**。UUID 只提高猜测成本，不能替代授权；
- [ ] 每个 detail 接口都调用 `authorize()`；
- [ ] 会话删除接口校验 `session.user_id == ctx.user_id`（否则可删别人会话）；
- [ ] 文件接口的路径必须限制在允许的根目录内（**路径穿越** `../` 是文件接口的 IDOR 变体）；
- [ ] 批量接口（列表）同样过滤——不能因为"反正是列表"就放松。

### 7.3 路径穿越（文件接口特有）

`app/api/routes/file_routes.py` 与 `data_routes.py` 的目录/文件接口接受用户提供的路径。防护要求：

```python
resolved = (allowed_root / user_path).resolve()
if not resolved.is_relative_to(allowed_root.resolve()):
    raise HTTPException(404)      # 不是 403——不确认外部路径的存在性
```

> 这条属于既有代码的加固项，是否需要纳入 8.1/8.2 范围待定——**建议单独提一个安全问题单**，不要混进本模块。

---

## 8. 撤权及其传播

PRD FR-N8 给出了完整规则表。提炼成工程要求：

| 对象 | 规则 | 实现要点 |
|---|---|---|
| 目录与详情 | **立即**不可见 | 每次查询都读事实源，不做权限缓存 |
| 新安装 / 调用 | **立即**拒绝 | 同上 |
| 已建立的长连接（SSE） | 重新认证或断开 | `chat_routes.py` 已有 SSE；需在流中定期复查 |
| 排队任务 | **执行前再次校验** | 入队时校验不够——权限可能在排队期间变化 |
| 运行中任务 | 按数据风险决定终止或完成当前原子步骤 | MVP：允许完成当前步骤，不启动下一步 |
| Agent 短期凭证 | 吊销或等待极短有效期结束 | 见 8.2 设计文档 |
| 历史安装与审计 | **保留** | 撤权不应破坏可追溯性 |

### 关键决策：MVP **不做权限缓存**

理由：

1. 撤权延迟是 PRD 的硬要求（"即时"），缓存必然引入延迟；
2. 本项目规模下（几十个资源、几个用户），每次请求查一次数据库的代价可忽略；
3. 缓存失效逻辑是撤权漏洞的常见来源——不做缓存就没有这个风险。

代价：Step 4/5 之后每次网络资源请求会多 2–3 次数据库查询。若压力测试显示瓶颈，再引入**主动失效 + 短 TTL** 的缓存（PRD 建议的方案）。

---

## 9. 越权测试用例矩阵

这是**验收的核心**，也是最终向评审证明隔离有效的方式。每个用例都必须能自动化执行。

### 9.1 测试环境

```text
租户 T1
  ├─ Alice   （N1 的 owner）
  ├─ Bob     （N1 的 member）
  └─ Carol   （不是 N1 成员，与 Alice/Bob 同租户）
租户 T2
  └─ Dave    （与 T1 完全无关）

网络 N1（属 T1，owner=Alice）
  ├─ Alice  owner
  ├─ Bob    member
  └─ Eva    viewer

资源：
  R_priv   Alice 的 private 数据集
  R_pub_N1 Alice 发布到 N1 的 Agent
  R_pub_T1 Alice 发布到 T1（租户级）的工具
  R_pub_pub Alice 发布到 public 的数据集
```

### 9.2 用例矩阵

| # | 调用者 | 目标 | 动作 | 期望 |
|---|---|---|---|---|
| 1 | Alice | R_priv | 详情 | ✅ 200 |
| 2 | Bob | R_priv | 详情 | ❌ 404 |
| 3 | Carol | R_pub_N1 | 目录 | ❌ 404（**同租户非成员**） |
| 4 | Carol | R_pub_N1 | 详情（猜 ID） | ❌ 404 |
| 5 | Carol | R_pub_N1 | 安装 | ❌ 404 |
| 6 | Carol | R_pub_N1 | 调用 | ❌ 404 |
| 7 | Dave | R_pub_N1 | 目录/详情/安装/调用 | ❌ 404（**跨租户**） |
| 8 | Dave | R_pub_T1 | 目录 | ❌ 404 |
| 9 | Bob | R_pub_N1 | 目录/详情/安装/调用 | ✅ 全部 200 |
| 10 | Eva | R_pub_N1 | 目录/详情/安装 | ✅ 200 |
| 11 | Eva | R_pub_N1 | 调用 | ❌ 403（默认策略关闭） |
| 12 | 匿名 | R_pub_pub | 详情 | ❌ 401（或 200，取决于是否公开接口） |
| 13 | Dave | R_pub_pub | 详情 | ✅ 200 |
| 14 | Bob | 不存在的 UUID | 详情 | ❌ 404 |
| 15 | Bob | 非 UUID 字符串 | 详情 | ❌ 400/404（**两者必须与用例 14 不可区分地一致**） |
| 16 | Bob | 伪造 tenant_id 请求头 | 任意 | ❌ 被忽略，按 `ctx.tenant_id` 判定 |
| 17 | Bob | 伪造 user_id 请求体 | 任意 | ❌ 被忽略 |
| 18 | 任意 | 他人 session_id | DELETE 会话 | ❌ 404 |

### 9.3 撤权时序用例

| # | 场景 | 期望 |
|---|---|---|
| 19 | Bob 退出 N1 后，立即重复用例 9 | ❌ 全部拒绝 |
| 20 | Bob 退出 N1 后，用退出前缓存的对话页面再请求 | ❌ 拒绝（无权限缓存） |
| 21 | 网络归档后，成员发起发布 | ❌ 409 |
| 22 | publication 撤回后，另一网络的同 release publication | ✅ 不受影响（PRD FR-N5 验收） |
| 23 | 任务入队后 Bob 被移除，任务开始执行 | ❌ 执行前校验拒绝 |
| 24 | Alice 移除 Bob 后，Bob 的 Agent 工作负载凭证 | ❌ 在 SLA 内失效 |

### 9.4 测试实施要求

- **必须在 PostgreSQL 上跑一遍**并发相关用例（SQLite 无行级锁会掩盖竞态，见 ADR-08-02）；
- 用例 14/15 要做**响应耗时对比**，确认不可区分；
- 用例 3–8 是**最重要的一组**——PRD §12.2 把「Carol 同租户非成员全部失败」列为独立验收项。

---

## 10. 与既有代码的边界（不要越界）

| 既有实现 | 处置 |
|---|---|
| `core/user_context.py` 的 `owner`/`visibility` | **保留**，作为资源默认可见性（ADR-08-08） |
| `data_registry.py` 的 owner 过滤与历史认领 | **保留不动**，这是既有平滑迁移逻辑 |
| `tool_registry.py` / `agent_registry.py` 的 `public` 默认 | **保留**，48 个工具/Agent 的行为不能变 |
| `model_registry.py` 无隔离 | **本模块不处理**，写进 ADR 待办 |
| `get_current_user_id()` | 保留为未登录回退，但接入 `AuthContext` 的接口不得再读它 |
| `data_routes.py` / `file_routes.py` 的路径处理 | 单独的加固项，不混入本模块 |

**核心原则**：8.1/8.2 是**增量叠加**，不是重写。任何要求"顺手把 registry 改成从数据库读"的冲动都要压制——那是被 PRD 严重低估的隐性工作量（ADR-08-08）。

---

## 11. 待确认

1. **模型空间是否纳入隔离范围**（当前零隔离，30 个条目全共享）；
2. `file_routes.py` / `data_routes.py` 的路径穿越加固是否纳入本模块；
3. 「敏感私有资源统一 404」的判定标准——哪些资源算"敏感"需要明确清单；
4. 无权限缓存的方案在数据量增长后是否需要调整（当前判断：不需要）。

---

## 12. v2 实现补充：多用户安全加固（2026-11-04）

> 本节记录评审后落地的实现，覆盖 §11「待确认」第 1–4 项的实际裁定。

### 12.1 身份统一（管理员评审意见 1）

- **资源 owner 一律为 `users.id`（UUID）**；机器身份 `local-xxxx` 仅作未登录兜底。
- 身份解析统一走 `core/user_context.py`：`current_owner_id()`
  = 显式上下文（ContextVar）> `SOTABAND_USER_ID`（**每次现取，不缓存**）> 机器身份。
  历史问题「同一 Agent 进程先后服务 A/B 时 B 的数据集写成 A 的 owner」由
  「env 层不缓存」根除。
- **执行链显式下传**：HTTP 边界 `resolve_owner_id(ctx)` →
  `ToolExecutor.execute(owner_id=)`（子进程按需注入/删除 `SOTABAND_USER_ID`）→
  常驻 Worker `call(owner_id=)`（每次调用重设环境并 `reset_cache()`）→
  工具内 `api_data.*` 通过 `current_owner_id()` 读取。
- **系统 API 与 HTTP 路由同源**：`core/api/implementations/api_data.py` 改用
  `current_owner_id()`，不再有第二套身份语义；`migrate` 导入的资源 owner
  也改为导入者 UUID。

### 12.2 文件与工作区边界（原 §7.2「文件接口需重点审查」的落地）

- 新增 `core/file_guard.py`：
  - **允许根**：项目根、`.runtime/refactor`、`resources-private`、上传根、
    全部已注册数据集的 `data_path`、`SOTABAND_FILE_ALLOWED_ROOTS` 扩展；
  - **敏感黑名单**：任何包含 `storage` / `.git` / `.env` / `venv` 的路径一律拒绝；
  - **规范化**：先 `resolve()` 再按 `Path.parents` 判断，避免 `..` 与符号链接绕过。
- `/api/file/*` 全部要求登录；下载/预览/图片/音频/删除/目录浏览均走上述校验；
  上传文件名净化（`Path(name).name`）并限制大小（`SOTABAND_UPLOAD_MAX_MB`）。
- **数据集注册路径校验**：`data_path` 必须在数据允许根内、不能是资源根本身、
  不能与其他已注册数据集目录重叠 —— 否则可借「注册任意目录 + `/files` 预览」
  绕过文件边界。
- **工作区状态按用户隔离**：`storage/workspace_tools/{user_id}.json`、
  `storage/workspace_tree/{user_id}.json`；旧全局文件只迁移给第一个读取者。
- **目录列举同样过滤敏感条目**：即使允许根包含项目根，`scan-directory` /
  `browse-directory` / 数据集扫描也不会列出 `storage/`、`.env`、`.git` 等条目
  （否则可借"扫根目录"旁路获得敏感路径清单）。
- **文件接口的第二层边界：归属**（v2 评审补）：数据集目录只把**当前用户自己的**
  （以及历史无主本机条目、public 可见性）加入允许根；上传目录按
  ``UPLOAD_ROOT/<user_id>/`` 命名空间隔离，跨用户不可读/不可删；
  ``registry.json`` / ``workspace_*.json`` / ``data/exports/**`` 等平台元数据
  一律禁止通过文件接口读/删（否则可清空注册表）。
- **迁移包**：目标环境 owner = 导入者 UUID；manifest 新增 `exported_by`
  （仅溯源与可见性）；包列表/下载默认只看自己导出的（旧包仅租户管理员可见）；
  `overwrite=true` 需对现有资源有写权限。

### 12.3 数据集归属与 404 语义

| 端点 | 规则 |
|---|---|
| `GET /api/data/{id}`、`/{id}/files`、`/{id}/preview`、`/{id}/thumbnail`、`DELETE /{id}`、`POST /match-tools` | 非本人一律 404（不泄露存在性）；历史无 owner 条目按「本机有数据」启发式 |
| `GET /api/data/repository` | 默认只返回本人；`include_others=true` 仅租户 Owner/Admin |
| `GET /api/data/search/find` | 结果按 owner 过滤 |
| `POST /api/data/claim-local` | 仅租户 Owner/Admin（避免"先到先得"认领全部历史数据），写审计 |

### 12.4 审计补全（FR-N9）

除既有认证/租户/网络/共享事件外，新增：`resource.code.change`、
`resource.spec.change`、`resource.delete`（工具/Agent）、`data.delete`、
`file.delete`、`config.llm.change`、`data.claim_local`、`migrate.export/import`；
发布被拒绝也写 `publication.create result=denied`。

### 12.5 §11 待确认项的裁定

1. **模型空间**：仍按共享资产（读/用登录即可），但**写/删**纳入 D2 口径
   （owner 或租户 Owner/Admin；`model_path` 受文件允许根约束）；
2. **路径穿越加固**：纳入本模块并已实现（§12.2）；
3. **敏感资源清单**：文件层用「目录/文件名黑名单 + 允许根」，资源层统一 404；
4. **无权限缓存**：维持不需要（当前无缓存）。

### 12.6 v3 终审补强（2026-11-04 晚）

- **Agent 子进程并发隔离**：同一 Agent 的 stdin/stdout 通道由工厂级 asyncio 锁
  串行化；超时/异常/消费者提前断开时排空或重建通道，禁止残留输出串给下一个请求；
  常驻 Worker 每工具一把调用锁 + call_id 回包校验，失败即停旧 Worker。
- **Web 服务式 Agent 身份**：引擎代理透传 HMAC 签名的发起者头
  （`x-sotaband-owner` + sig，密钥为引擎共享令牌）；Web 侧验签后用
  `use_owner()` 执行，工具/任务以发起用户归属落库；验签失败退回机器身份。
- **任务归属**：长任务注册表记录 owner，HTTP 端点按 owner 过滤（他人 404）。
- **数据集边界**：`validate_dataset_path` 收紧允许根（data/output/private-data/
  runtime/datasets/本人 upload），新增 `validate_dataset_deletion`；迁移包解压
  拒绝 Windows 盘符/绝对路径成员（写盘前再次 `relative_to` 校验）。
- **网络不变量**：SQLite 下成员变更临界区先取库级写锁（等效 BEGIN IMMEDIATE），
  保证"最后一位 Owner 不可移除/降级/退出"在默认部署上真实成立；
  过期邀请状态先提交再返回 410；成员移除/退出吊销该邮箱 pending 邀请；
  网络状态变更改条件更新（CAS）。
- **升级兼容（历史机器身份数据集）**：8.1 之前注册的数据集 owner 为机器身份（`local-xxxx`），升级后按「未认领的历史条目」处理——本机有数据即对登录用户可见，平台管理员可用 `POST /api/data/claim-local` 批量打上 UUID 归属；不会静默失联。

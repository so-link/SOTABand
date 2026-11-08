# 设计：8.1 用户注册与登录

> 状态：待评审 ｜ 日期：2026-10-15
> 决策依据：`docs/adr-08-user-space-and-network.md`（ADR-08-01 / 01a / 01b）
> 接口契约：`docs/contract-08-auth-network.md` §3.1、§3.4
> 数据表：`docs/design-08-schema.md`

---

## 1. 这个功能要解决什么问题

### 1.1 表面需求 vs 真实需求

需求原文只有六个字：「用户注册与登录」。字面理解就是加一个登录页。但**登录页本身几乎不是问题所在**——真正要解决的是：

> **把「你是谁」从「客户端自己说了算」变成「服务端签发并可验证的断言」。**

这是整个平台所有授权判断的地基。没有这一步，后面所有的「数据隔离」「网络共享」「权限矩阵」都只是在 UI 上做样子——任何人改一个字段就能绕过。

### 1.2 现状：三个具体的问题

经逐文件核实，当前代码在身份上有三个实打实的缺陷：

| # | 位置 | 现状 | 后果 |
|---|---|---|---|
| 1 | `app/api/routes/chat_routes.py:104` | `context={"user_id": request.user_id or "default"}` —— **身份由客户端在请求体里提交** | 任何人把 `user_id` 改成别人的 id，就能以该用户身份操作 |
| 2 | `core/user_context.py:140` | `get_current_user_id()` 从环境变量 → `storage/user.json` → `local` 取值 | 这是**单机实例身份**，不是「谁在操作」。同一台机器上所有浏览器共用一个身份 |
| 3 | `app/api/middleware/__init__.py` | 空文件，注释写着「认证、限流、日志等」，无任何实现 | 没有统一的认证入口，也就没有统一的可信上下文 |

**问题 1 是最严重的**：它让 `core/user_context.py` 里那套已经实现得很好的 `owner` / `visibility` 归属模型**形同虚设**——数据集的 owner 过滤是真的（`data_registry.py` 按 owner 隔离），但对话接口却能以任意 user_id 发起操作。

### 1.3 如果不做会怎样

下游五个模块（工具共享、数据共享、Agent 发布、三大集市）**全部无法安全落地**：

- 「网络内共享」无法定义「共享给谁」；
- 「成员退出后立即撤权」无从谈起；
- 「Agent 以独立身份通信」没有身份体系可依附。

所以 8.1 的交付物不是「一个登录页」，而是**三样东西**：

1. 一套**凭证签发与校验**机制（注册、登录、会话）；
2. 一个**服务端生成、客户端无法伪造**的请求上下文 `AuthContext`；
3. 一个**统一认证入口**（中间件 + FastAPI 依赖），让下游只需 `Depends(get_auth_context)`。

---

## 2. 威胁模型：要防什么

范围限定：**单团队、本地/内网部署、MVP**。不假设互联网规模的攻击者，但也不允许「改个字段就能越权」这种级别的漏洞。

| 编号 | 威胁 | 具体场景 | 造成的后果 | 对策 | 落点 |
|---|---|---|---|---|---|
| **T1** | 身份伪造 | 请求体里改 `user_id` / `tenant_id` / `role` | 完全越权 | 业务接口**只从 `AuthContext` 取主体**，忽略客户端同名字段 | 中间件 + `deps.py` |
| **T2** | 密码爆破 | 脚本高速尝试常见密码 | 账号被接管 | 登录限流（IP + 账号双维度）+ bcrypt 本身的慢哈希 | §5.2 |
| **T3** | 用户枚举 | 通过「邮箱已存在」/「密码错误」的差异响应或**响应耗时差异**筛选出有效账号 | 为 T2 提供精准目标 | 统一错误响应体、状态码，并做**恒定耗时**处理（邮箱不存在时也跑一次假哈希） | §5.4 |
| **T4** | 会话劫持 | 会话令牌被窃取后重放 | 账号被接管 | 令牌用 `secrets.token_urlsafe(32)`；**数据库只存 SHA-256 hash**；HttpOnly Cookie | §4.3 |
| **T5** | 会话固定 | 攻击者预先设定受害者的会话 ID | 账号被接管 | 登录成功后**必须重新签发**会话令牌，不复用登录前的任何令牌 | §4.2 |
| **T6** | CSRF | 第三方站点诱导浏览器发起带 Cookie 的写请求 | 以受害者身份执行操作 | `SameSite=Lax`；写操作要求 `Content-Type: application/json`（简单请求无法跨站携带）；Key 接口校验 `Origin` | §4.4 |
| **T7** | XSS 窃取令牌 | 前端脚本读取令牌 | 账号被接管 | 令牌放 **HttpOnly** Cookie，**不落 localStorage** | §4.3 |
| **T8** | 数据库泄露后的离线破解 | 数据库文件被拿走 | 明文密码（用户往往复用密码） | **bcrypt work factor 12**，每用户独立盐 | §3.3 |
| **T9** | 开放重定向 | 登录后 `?return_url=https://evil.com` | 钓鱼 | `return_url` **只允许站内相对路径**，拒绝绝对 URL 与 `//` 开头 | §5.5 |
| **T10** | 权限提升 | 普通成员改自己的 role | 越权 | 角色变更走独立接口 + 权限校验 + 版本号条件更新（见 8.2 设计） | 8.2 设计文档 |

**明确不在威胁模型内**（MVP）：国家级攻击者、供应链攻击、物理接触、内部人员作恶、DDoS。

---

## 3. 认证方案

### 3.1 总览

```text
                    ┌──────────────────────────────────────────┐
                    │  账号 + 密码（自建）                       │
   注册  ──────────→ │  users + user_credentials + user_identities│
                    └──────────────────────────────────────────┘
                                     │
   登录  ─────────────────────────→  校验密码 → 签发 opaque 会话令牌
                                     │
                    ┌────────────────▼─────────────────────────┐
                    │  sessions：只存 token 的 SHA-256 hash     │
                    └──────────────────────────────────────────┘
                                     │
                              Set-Cookie: HttpOnly; SameSite=Lax
                                     │
   后续请求 ──→ 认证中间件 ──→ 查 session → 组装 AuthContext ──→ 业务接口
```

**为什么用 opaque 令牌而不是 JWT**：

- JWT 无法即时吊销（成员退出、改密码、禁用用户后旧 token 仍然有效直到过期）——而 PRD FR-N8 的核心要求就是**即时撤权**；
- 每次请求查一次数据库的代价，在本项目规模下可以忽略；
- opaque 令牌不需要管理签名密钥，也就不存在密钥轮换问题。

> PRD §8.1 提到「CLI/服务调用使用 Bearer Token」，那是 Phase 5 的扩展；MVP 只做 Cookie 会话。

### 3.2 注册流程

```text
POST /api/auth/register  {email, password, displayName?}

1. 限流检查（IP 维度）                        → 429
2. 规范化 email（trim + 域名小写）
3. 校验 password：UTF-8 字节长度 8..72        → 400（含具体原因）
4. 校验 displayName 长度 ≤ 128
5. hash = bcrypt(password, rounds=12)        ← 慢操作，放在事务外
6. 事务开始：
     a. INSERT users
     b. INSERT user_credentials
     c. INSERT user_identities (issuer='local', subject=<user_id>)
     d. INSERT tenants            （个人租户，name = display_name 或 email 前缀）
     e. INSERT tenant_memberships (role='owner')
   COMMIT
   ↑ email 唯一约束在 (a) 处冲突 → 409（统一错误，不区分「邮箱已存在」）
7. 签发会话（同登录）
8. 写审计：auth.register / result=success
9. 201 {user, tenant} + Set-Cookie
```

**关键设计点**：

- **第 5 步在事务外**：bcrypt 耗时约 250ms，若放在事务内会长时间持有写锁（SQLite 下尤其致命——SQLite 是库级写锁）；
- **第 6 步必须是一个事务**：users / credentials / identities / tenants / memberships 五条记录要么全成要么全不成，否则会出现「有账号但无租户」的半成品，用户登录后无处可去；
- **个人租户在注册时自动创建**（PRD FR-A1「注册成功即创建个人租户并成为租户 Owner」）。这让每个用户天然有归属，后续「切换租户」才有意义。

### 3.3 密码存储（bcrypt）

> 本节是 ADR-08-01a 的展开。

**参数**：

| 项 | 取值 | 说明 |
|---|---|---|
| 算法 | bcrypt，`$2b$` | 跨语言通用格式 |
| work factor | **12** | 约 250ms/次；需在目标机器上实测标定，若 >500ms 降到 11，若 <100ms 升到 13 |
| 盐 | bcrypt 内部随机生成，随 hash 一起存储 | 不自管盐 |

**依赖**：官方 `bcrypt` 包。

**❌ 不要用 `passlib`**：`passlib` 1.7.4 与 `bcrypt >= 4.1` 不兼容——它会读取已被移除的 `bcrypt.__about__` 属性而报错（社区已多次报告，例如 [hexstrike-ai#43](https://github.com/0x4m4/hexstrike-ai/issues/43)、[StackOverflow 上的同类问题](https://stackoverflow.com/questions/79469355/error-attributeerror-problem-with-passlibbcrypt-module/79469411)）。直接用官方包，少一层中间件。

**⚠️ 72 字节陷阱（必须处理）**：

bcrypt 的输入上限是 **72 字节**。官方 `bcrypt` 4.x 的行为是**直接抛异常**：

```
ValueError: password cannot be longer than 72 bytes, truncate manually if necessary
```

（参见 [Debian Bug #1122147](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1122147)）

三种处置方式，**我们采用第 1 种**：

| 方案 | 评价 |
|---|---|
| ① **入口拒绝**：按 UTF-8 字节长度校验，>72 直接返回 400 | ✅ 采用。语义清晰，不会造成「同一密码不同后缀都能登录」的困惑 |
| ② 静默截断 | ❌ **危险**。用户设了 80 字节密码，实际只有前 72 字节生效，安全预期被违背 |
| ③ SHA-256 预哈希后再 bcrypt | ⚠️ 可行但要处理 base64 编码与 null 字节问题，复杂度不值得 |

**注意是字节不是字符**：中文在 UTF-8 下占 3 字节，所以 24 个汉字就会超限。校验必须用 `len(password.encode('utf-8'))`，不能用 `len(password)`。

**为什么不用 Argon2id**：ADR-08-01a 已记录——bcrypt 生态更成熟、跨语言可验证，OWASP 基线认可 work factor ≥ 10 的 bcrypt。代价是抗 GPU 能力弱一档，用 work factor 12 与登录限流补偿。`user_credentials.algorithm` 字段已预留，后期可平滑升级（登录成功后按需重哈希）。

### 3.4 登录流程

```text
POST /api/auth/login  {email, password}

1. 限流检查（IP + email 双维度）              → 429
2. 规范化 email
3. 查 users（by normalized email）
4. t0 = now()
   if 用户不存在 or 已禁用:
       bcrypt.hash(password, rounds=12)   ← 关键：跑一次假哈希，抹平耗时差异
       → 统一失败响应
   else:
       bcrypt.verify(password, stored_hash)
5. 失败 → 写审计 auth.login / result=failure → 统一失败响应（401）
6. 成功：
     a. 生成新 token（不复用登录前任何令牌 → 防 T5 会话固定）
     b. INSERT sessions (token_hash, expires_at=now+30d)
     c. 若有旧会话且策略要求单设备，可选择性吊销
     d. 登录成功后的重哈希升级：若 stored work factor < 当前配置 → 重新哈希并更新
7. 写审计 auth.login / result=success
8. 200 {user} + Set-Cookie
```

**第 4 步的假哈希是防 T3 用户枚举的关键**：如果「用户不存在」直接返回，而「密码错误」要跑 250ms 的 bcrypt，攻击者用响应耗时就能筛出有效账号。所以用户不存在时也必须消耗等量时间。

**统一失败响应**：无论「邮箱不存在」「密码错误」「账号被禁用」，对外一律返回同一个响应体、同一个状态码。差异化提示只在**审计日志**里留存。

### 3.5 会话管理

| 项 | 设计 |
|---|---|
| 令牌格式 | `secrets.token_urlsafe(32)`（32 字节随机，约 43 字符） |
| 存储 | `sessions.token_hash` = SHA-256(令牌)。**明文永不落库** |
| 为什么用 SHA-256 而不是 bcrypt | 令牌本身是高熵随机串（256 bit），不存在字典攻击空间，不需要慢哈希；且每次请求都要查会话，必须快 |
| 有效期 | 绝对过期 30 天（`expires_at`） |
| 吊销 | `revoked_at` 置值。登出 / 改密码 / 禁用用户 / 退出全部设备均触发 |
| 认证时校验顺序 | 查 hash → 存在？→ `revoked_at IS NULL`？→ `expires_at > now()`？→ 用户 `status='active'`？ |
| 清理 | 过期会话由后台任务定期删除（MVP 可先不清理，`expires_at` 判断已足够正确） |

**为什么认证时要查用户 status**：PRD FR-A5 要求「禁用用户后新请求立即被拒」。只查会话不够——会话还在有效期内。所以每次认证都要 join 到 `user` 确认 `status='active'`。

### 3.6 登出

```text
POST /api/auth/logout        → 当前会话 revoked_at = now()，清 Cookie，204
POST /api/auth/logout-all    → 该用户全部会话 revoked_at = now()，清 Cookie，204
```

**登出是幂等的**：重复调用返回 204，不报错（会话已删/已吊销都视为成功）。

---

## 4. 会话与 Cookie 的具体约定

### 4.1 Cookie 属性

```text
Set-Cookie: sotaband_session=<token>;
            HttpOnly;
            SameSite=Lax;
            Path=/;
            Max-Age=2592000;
            [Secure]      ← 仅当 SOTABAND_COOKIE_SECURE=true（生产 https）
```

**`Secure` 必须按环境条件开启**：开发环境是 `http://localhost`，硬编码 `Secure` 会导致浏览器在 http 下直接丢弃 Cookie（虽然现代浏览器把 localhost 视为 secure context，但显式条件开启更稳妥、也避免误配到非 localhost 的 http 部署）。

### 4.2 会话固定防护

**登录成功后必须签发全新令牌**，绝不复用登录请求中携带的任何会话标识。这是 T5 的标准对策。

### 4.3 为什么不放 localStorage

PRD §14 风险表明确：「localStorage Token —— 易受 XSS」。本项目前端渲染 LLM 生成的代码、Markdown、HTML 预览，XSS 面不小。HttpOnly Cookie 让脚本**读不到**令牌，从根上消除这条路径。

### 4.4 CSRF

`SameSite=Lax` 已挡住绝大多数跨站写请求（Lax 下跨站的 POST 不带 Cookie）。补充两道：

1. 所有写接口要求 `Content-Type: application/json` —— 跨站 `<form>` 只能发 `application/x-www-form-urlencoded`，无法伪造 JSON 请求（属于 CSRF 的"简单请求"限制）；
2. 高风险接口（改密码、登出全部设备、创建网络）校验 `Origin` / `Referer` 白名单。

**同源代理让这件事更简单**（见 ADR-08-05）：开发期前端通过 Vite 的 `/api` 代理访问后端，全程同源，配置错误的空间被压缩到最小。

---

## 5. 安全基线细则

### 5.1 密码强度策略

> **2026-10-27 变更（用户指定）**：长度改为 **8–20 位字符**，并**要求字母 + 数字组合**。
> 旧的「不做复杂度要求、长度优先」被取代 —— 见下方变更说明。

| 规则 | 取值 | 理由 |
|---|---|---|
| 最小长度 | **8 位字符** | 低于此无意义 |
| 最大长度 | **20 位字符** | 用户指定；同时远离 bcrypt 上限，不会撞到 72 字节 |
| **组合要求** | **必须同时含字母和数字** | 用户指定。比"长度优先"更符合本项目使用者的预期 |
| 拒绝纯数字 / 纯字母 | ✅（由上一条覆盖） | 缺哪条提示哪条 |
| bcrypt 硬上限 | **72 字节**（仍守） | 20 位以内塞满多字节字符仍可能超限，必须单独拒绝 |
| 拒绝与邮箱相同 | ✅ | 低成本、防明显弱口令 |
| 拒绝常见弱口令 Top 列表 | 可选 | MVP 先不做 |

**为什么从"字节"改成"字符"**：旧提示是「密码至少 8 个字节（中文按 3 字节计）」——
普通用户既看不懂也没法自己数，是纯粹的可用性损耗。长度按字符计后，提示可以直接写成
「8-20 位，需同时包含字母和数字」，并且前端能实时逐条打勾。

**为什么复杂度要求这跟现代共识不同**：现代共识确实倾向"长度优先、不加复杂度规则"，
但那条共识针对的是**面向公众的大规模注册**（需要极低的注册摩擦）。本项目是内部协作
工具，账号来自团队成员，`8-20 位 + 字母数字` 的摩擦可接受，且能挡掉最弱的几类口令。
**这是产品决策，不是安全最优解** —— 若将来面向公网开放，应重新评估并倾向"长度优先"。

**错误提示必须具体**（「密码长度需为 8-20 位，当前 5 位」而不是「注册失败」），
且**一次只提示一条**最该先修的问题，不得回显密码本身。

### 5.2 限流

| 接口 | 维度 | 建议阈值（可调） |
|---|---|---|
| `POST /api/auth/login` | IP | 20 次 / 5 分钟 |
| `POST /api/auth/login` | 账号（email） | 5 次 / 5 分钟，超过则临时锁定 15 分钟 |
| `POST /api/auth/register` | IP | 5 次 / 小时 |

**实现方式**：MVP 用进程内滑动窗口（`collections.deque` + 时间戳）即可，不需要 Redis（ADR-08-09 已排除）。注意：进程内限流在多 worker 下会失效——**MVP 单 worker 部署，需在文档与启动脚本中明确**，否则日后加 `--workers 4` 会静默削弱防护。

### 5.3 输入校验

| 字段 | 规则 |
|---|---|
| `email` | 长度 ≤ 320；格式校验用宽松正则（含 `@` 且域名有 `.`）；**规范化**：trim、整体小写（本平台不做区分大小写的本地部分） |
| `displayName` | 长度 ≤ 128；trim；允许为空（回退为 email 本地部分） |
| `password` | 见 §5.1 |

**邮箱规范化为什么整体小写**：RFC 5321 规定本地部分理论上区分大小写，但没有任何主流邮件服务商实际这样做。整体小写可避免 `Alice@x.com` 与 `alice@x.com` 被当成两个账号——这是实际运维中更常见的问题。

### 5.4 用户枚举防护清单

- [ ] 登录失败响应体与状态码完全一致
- [ ] 用户不存在时跑假哈希（恒定耗时）
- [ ] 注册重复邮箱返回统一错误（不返回「该邮箱已注册」以外的差异信息）
- [ ] 限流响应不区分账号是否存在
- [ ] 审计日志可以区分（内部排查需要），但**不返回给客户端**

### 5.5 开放重定向防护

```python
def safe_return_url(raw: str | None) -> str:
    """只接受站内相对路径。"""
    if not raw:
        return "/"
    if not raw.startswith("/") or raw.startswith("//"):
        return "/"          # 拒绝 //evil.com 与 https://evil.com
    return raw
```

### 5.6 审计事件

写入 `audit_event`（表结构见 `design-08-schema.md`）：

| action | 触发 |
|---|---|
| `auth.register` | 注册（含成功/失败） |
| `auth.login` | 登录（含成功/失败/限流） |
| `auth.logout` | 登出（含单设备/全部设备） |
| `auth.session.revoked` | 会话被吊销（含原因：登出/改密/禁用/管理员操作） |

**审计日志禁止记录**：密码、密码哈希、会话令牌、Cookie 全文。可复用 `core/security/secrets.py::scrub_text()` 做兜底脱敏。

---

## 6. 明确不做（MVP）

| 不做 | 原因 |
|---|---|
| 邮箱验证 | ADR-08-01b |
| 找回密码 / 修改密码 | 依赖可信邮箱；且 MVP 无邮件服务 |
| GitHub OAuth | 移至 Phase 2（ADR-08-01） |
| MFA / 二次验证 | PRD §2.4 Non-Goal |
| 记住我 / 滑动过期 | 绝对过期 30 天已够用 |
| 多设备会话管理界面 | 接口预留（`/api/v1/me/sessions`），UI 可后置 |
| 账号注销 / 数据导出 | 未在需求中 |
| JWT / Bearer Token | 无法即时吊销，与撤权要求冲突 |

---

## 7. 验收标准（可测）

### 7.1 功能

- [ ] 注册成功后能用同一密码登录
- [ ] 注册自动创建个人租户，用户在该租户中角色为 `owner`
- [ ] 重复邮箱注册被拒，且响应与「密码错误」在状态码/响应体上不可区分
- [ ] 并发注册同一邮箱：**只有一个成功**（靠 `users.email` 唯一约束，不能靠先查后插）
- [ ] 密码 **7 位 → 400**（提示「需为 8-20 位，当前 7 位」）；**8 位 → 成功**
- [ ] 密码 **21 位 → 400**；**20 位 → 成功**
- [ ] 纯字母（`abcdefgh`）→ 400，提示「需包含至少一个数字」
- [ ] 纯数字（`12345678`）→ 400，提示「需包含至少一个字母」
- [ ] 只有符号（`!!!!!!!!`）→ 400
- [ ] 20 个多字节字符但 > 72 字节 → 400（提示多字节字符过多），不得抛 500
- [ ] 两次密码不一致 → 前端拦住，不发请求
- [ ] 登出后同一 Cookie 再调用受保护接口 → 401
- [ ] 登出全部设备后，另一浏览器的会话 → 401
- [ ] 禁用用户后，其既有会话立即 → 401

### 7.2 安全

- [ ] 伪造 Cookie（随机串、篡改、空值）→ 401
- [ ] 过期会话 → 401
- [ ] 连续 6 次登录失败 → 第 6 次起 429，且临时锁定生效
- [ ] 「邮箱不存在」与「密码错误」的响应耗时差异 < 20%（实测）
- [ ] `return_url=https://evil.com` 与 `//evil.com` 均被拒绝
- [ ] 跨站 `Origin` 发起的写请求被拒
- [ ] Cookie 具备 `HttpOnly` + `SameSite=Lax`；生产环境含 `Secure`
- [ ] 数据库中 `session` 表无明文令牌（抽查）
- [ ] 日志中无密码/令牌明文（抽查；`scrub_text()` 兜底验证）

### 7.3 契约

- [ ] `AuthContext` 能被子进程外的下游模块 import 并使用
- [ ] 业务接口忽略客户端提交的 `user_id`（构造伪造请求验证）
- [ ] 401 / 403 / 404 语义与 `contract-08-auth-network.md` §4 一致

---

## 8. 待确认

1. **bcrypt work factor 12 需在本机实测标定**——目标 100–500ms，若超标需调整；
2. **部署是否严格单 worker**——影响进程内限流的有效性（§5.2）；
3. 会话有效期 30 天是否合适（也可 7 天）；
4. 是否需要「修改密码」接口（不依赖邮箱，只需要旧密码）——技术上可行，成本低，建议纳入。

---

## 9. v2 实现补充：身份传导与旧接口覆盖（2026-11-04）

### 9.1 身份传导（管理员评审意见 1 的落地）

原设计只定义了 `AuthContext`（HTTP 层），未定义「身份如何到达工具子进程」。
v2 补全为一条显式链路：

```
浏览器 Cookie → 路由 resolve_owner_id(ctx) = users.id (UUID)
  → Agent 执行 context={"user_id": uuid}
    → Agent 子进程 os.environ[SOTABAND_USER_ID] = uuid（每次 execute 前设置/清除）
      → ToolExecutor.execute(owner_id=uuid) 起子进程时显式注入 env
        → 常驻 Worker 每次 call 的 RPC 载荷带 owner_id，执行前重设 env + reset_cache()
          → 工具内 api_data.* 经 current_owner_id() 读取 UUID
```

要点：

- `current_owner_id()` 优先级：**ContextVar（进程内显式）> 环境变量（每次现取，不缓存）>
  机器身份**；env 层不缓存是「同一进程连续服务两个用户会串号」的根因修复；
- 未认证路径（CLI/后台任务/Web 服务式 Agent）回退机器身份 `local-xxxx`，
  仅能看见历史无主数据（已由审计与文档留痕）；
- `migrate` 导入的资源 owner = 导入者 UUID（原实现记机器身份，属遗留断层）。

### 9.2 旧接口纳入认证基线（D1）

原设计只约束 8.1 新增的 `/api/v1/*` 路由；v2 起**全部业务 API** 都要求登录，
公开白名单仅 `/api/auth/*` 与 `/api/health`。实现方式是 `app/main.py` 在
`include_router` 统一挂 `get_auth_context` 依赖，前端本就强制登录。

### 9.3 交互会话的归属（v2 评审补）

- 停止标志与归属记录改为按 ``(user_id, session_id)`` 复合键：同一安装下不同
  用户即使都用默认 session 也不会互相停止/覆盖；
- 前端对话 session id 改为浏览器级随机值（localStorage），不再固定 ``default``；
- **已知限制**：交互 Agent 进程内的对话历史仍按 session_id 存储；`(user, session)`
  隔离依赖前端生成唯一 session id。多用户同安装时若要强隔离，应在 Agent
  侧把 session 键改为 `(user_id, session_id)`（后续需求，已记录）。
- Agent 的启动/停止/重启属于运行控制，纳入 D2 写权限（owner 或租户管理员）。

### 9.4 会话安全复核

HttpOnly + SameSite=Lax + Secure（可配）、登录签发新会话、改密吊销全部会话、
会话删除校验归属 —— 实现与设计一致，v2 未改动，仅补充了回归测试。

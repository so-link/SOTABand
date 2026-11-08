# 验收手册：用户空间与用户网络（需求 8.1 + 8.2）

> 写给要**亲手验证**的人。每条都给了「怎么做」和「应该看到什么」。
> 自动化部分在最后，手工部分不需要跑任何命令。
>
> 最后更新：2026-11-03　对应迁移版本 `0007`

---

## 0. 先把服务跑起来

两种方式，**任选一种**。下面所有验证内容完全一样。

### 方式 A：Docker 一条命令（推荐，含 PostgreSQL）

```powershell
# 在项目根目录执行
docker compose up -d --build      # 首次约 3-5 分钟
docker compose ps                 # 三个服务都应是 healthy / running
```

然后浏览器开 **http://localhost:5173**。

这一套起的是 **PostgreSQL**（不是 SQLite），表结构由后端容器启动时自动跑 Alembic
迁移建好。常用命令：

| 命令 | 作用 |
|---|---|
| `docker compose logs -f backend` | 看后端日志（含迁移输出） |
| `docker compose exec backend python scripts/inspect_db.py` | 容器内查表结构 |
| `docker compose restart backend` | 重启后端（**会清零注册限流**） |
| `docker compose down` | 停止（保留数据库数据） |
| `docker compose down -v` | 停止并**删除数据库**（回到全新状态） |

> **镜像源**：本机直连 Docker Hub / PyPI 会超时，compose 默认用 DaoCloud + 阿里云
> 镜像源。网络能直连时用
> `$env:DOCKER_REGISTRY='docker.io'; $env:PIP_INDEX='https://pypi.org/simple'` 覆盖后重建。

### 方式 B：本机跑（SQLite，改代码即时热更新）

需要**两个**进程（前端会自动把 `/api` 代理到后端 8001，所以只需访问 5173）。

```powershell
# 终端 1 —— 后端（必须设 PYTHONUTF8，否则中文 Windows 下读文件会 GBK 报错）
$env:PYTHONUTF8=1
# 在项目根目录执行
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 终端 2 —— 前端
cd frontend
npm run dev
```

然后浏览器开 **http://localhost:5173**。

**自检**：访问 http://127.0.0.1:8001/api/health 应返回 `{"status":"healthy",...}`。
（Docker 模式下后端端口未对外暴露，改用 `docker compose ps` 看 healthy。）

> 两种方式的区别只在**数据库**（PostgreSQL vs SQLite）和**前端形态**（构建产物 vs
> Vite 开发服务器）。共用同一套 Alembic 迁移，所以 schema 不会漂移。

> ⚠️ **注册限流**：5 次/小时/IP，状态在**后端进程内存里**。手工验证时如果一直注册
> 失败（HTTP 429），重启后端（Docker 模式用 `docker compose restart backend`）即可清零。

---

## 1. 五分钟快速验收（单人也够）

### 1.1 注册登录（8.1）

**密码规则（2026-10-27 起）**：**8–20 位，必须同时包含字母和数字。**

| 做什么 | 应该看到 |
|---|---|
| 打开 5173 | 登录页，**不会先闪一下主界面**（PRD FR-A7） |
| 点「注册」 | 密码框下出现**三条规则清单**，初始都是灰色 ✗ |
| 随便输几个字符（如 `ab1`） | 「8-20 位」「包含字母」「包含数字」中的 ✗ 会随输入**实时变绿打勾**；长度不够时第一条仍是 ✗ |
| 输入 `abcdefgh`（纯字母） | 「包含数字」保持 ✗ —— **提交会被拦下并提示「密码需包含至少一个数字」** |
| 输入 `12345678`（纯数字） | 「包含字母」保持 ✗ |
| 输入 `Passw0rd2026`，确认密码也填一样 | 三条全绿；点右侧 👁 可切换明文/密文（用来核对有没有打错） |
| 确认密码填成不一样的 | 下方立刻出现红字「两次输入的密码不一致」 |
| 填好邮箱/昵称/密码，提交 | 直接进入主界面，右上角头像首字母 = 你的昵称 |
| 刷新页面（F5） | **仍是登录状态**（会话在 HttpOnly Cookie 里） |
| 点头像 | 下拉里显示昵称、邮箱、`所有者` 角色标签 |

> 前端的即时校验只是体验；**真正的裁决在服务端**（`core/auth/passwords.py`），
> 直接用 API 打也会被同样拒绝（见 3 节的用法）。

### 1.2 建网络（8.2）

| 做什么 | 应该看到 |
|---|---|
| 点头像 → **协作网络与空间** | 打开「协作网络」页签，左侧「新建网络」输入框 |
| 输入 `飞控算法组`，点 `+` | 左侧列表出现该网络；右侧详情：状态 `运行中`、`1 位成员`、`我的角色：所有者` |

### 1.3 邀请（8.2）

| 做什么 | 应该看到 |
|---|---|
| 在详情里「邀请加入」填一个邮箱，点「邀请」 | 出现蓝色框「**邀请链接（只显示这一次）**」，里面是 `http://localhost:5173/?invite=...` |
| 检查 | 下方「待接受」列表出现该邮箱；点「撤销」后消失 |
| 关掉蓝框 | 链接再也拿不回来 —— **这是设计**：库里只存 SHA-256，明文只在创建时返回一次 |

### 1.4 状态机（8.2）

| 做什么 | 应该看到 |
|---|---|
| 点「暂停」 | 状态变 `已暂停`，出现黄色提示条「只读可查看，写操作会被拒绝（409）」 |
| 点「恢复」 | 回到 `运行中`，提示条消失 |
| 点「归档」→ 确认 | 状态 `已归档`；**普通成员将看不到这个网络**（下面第 2 节验证） |

### 1.5 最后一位所有者守卫生效

| 做什么 | 应该看到 |
|---|---|
| 点「退出网络」→ 确认 | **红色错误条**：「你是最后一位 Owner，不能退出。请先转移所有权或归档网络」 |

这条很关键：它证明后端在算「还剩几个 Owner」，而不是前端拍的。

### 1.6 资源共享：发布 → 目录 → 安装（FR-N5 / FR-N6）

> 前置：完成 1.2 建了网络。

| 做什么 | 应该看到 |
|---|---|
| 在网络详情里看「**资源目录**」区块 | 空状态提示「该网络还没有共享资源…」；上方有发布表单 |
| 类型选 `Agent`，资源输入框填一个 id（或从下拉候选中选），点「发布」 | 目录出现一行：`Agent` 徽标 + 该 id + 「安装」按钮 |
| 点「安装」 | 按钮变成 **「已安装」**（灰、带勾），不可再点 |
| 点顶部第三个页签「**我的安装**」 | 出现刚安装的资源，右侧有「卸载」 |
| 回网络详情，点目录行右侧的垃圾桶图标 → 确认 | 目录条目消失（**已安装不会自动卸载** —— 撤发布只影响新安装） |
| 回「我的安装」→ 点「卸载」→ 确认 | 列表清空 |

**权限可见性**（用 2.5 的 Bob 视角看同一个网络）：

| Bob 是 member，应该 | 不应该 |
|---|---|
| 看到资源目录、看到「安装」按钮 | 看到发布表单 |
| | 看到垃圾桶图标（撤回发布） |

member 位置会显示一句「你没有发布权限（需要网络所有者/管理员；或由管理员开启
「允许成员发布」策略）」—— 这是刻意的：**告诉用户为什么没有**，而不是静默消失。

> 发布粒度说明：目录里显示的是 `release_id`（资源版本号），不是 UUID。
> 它属于"跨系统逻辑引用"，数据库无法校验其存在 —— 发布一个还没同步到本机
> `registry` 的版本是允许的，这是设计而非缺陷。

### 1.7 修改密码（FR-A5）

| 做什么 | 应该看到 |
|---|---|
| 点头像 → **修改密码** | 弹窗打开，三个字段：当前密码 / 新密码 / 确认新密码 |
| 新密码输 `abcdefgh` | 规则清单：「8-20 位」✓、「包含字母」✓、「**包含数字**」✗ |
| 当前密码**填错**，其余填对，提交 | 弹窗内红字「当前密码不正确」；**仍停在弹窗里，没有被登出** |
| 改成正确的当前密码，提交 | 被**踢到登录页**，并提示「密码已修改，所有设备需要重新登录（已吊销 N 个会话）」 |
| 用**旧密码**登录 | 失败：「邮箱或密码不正确」 |
| 用**新密码**登录 | 成功进入主界面 |

> 两个容易误判的点：
> - **当前密码错不该被登出** —— 后端刻意返回 400（而不是 401），因为前端对 401
>   注册了"会话过期→跳登录"的全局处理。
> - **成功后要重新登录是设计** —— PRD FR-A5 要求"改密码后既有会话全部吊销"。
>   这不是 bug，是"怀疑密码泄露所以改密"这个动机真正生效的前提。
>
> 连续输错 5 次当前密码会返回 429（防把改密接口当爆破信道），重启后端清零。

### 1.8 审计日志（FR-N9）

| 做什么 | 应该看到 |
|---|---|
| 在网络详情往下滚 | 有「**审计日志**」区块，列出最近 20 条 |
| 每行的内容 | `时间 · action 代码 · 主体徽标（用户/Agent）· 结果徽标（success/denied）` |
| 建网络、邀请、改角色后点刷新图标 | 出现对应记录（`network.create` / `network.create` 邀请 / `network.member.role.change`） |
| 触发一次 Agent 调用后刷新 | 出现 `network.agent.invoke`，主体徽标是 **Agent**（不是用户） |
| 改用 **member** 账号看同一网络 | **看不到「审计日志」区块** —— 权限码 `network.audit.read` 仅 Owner/Admin |

> 审计是**只追加**的，没有任何接口能修改或删除它 —— 这是刻意的（PRD §11
> 「审计保留期独立于成员删除」）。所以你在界面上只能"读"，没有编辑入口。
>
> 想看被**拒绝**的 Agent 调用？`GET /api/v1/networks/{id}/audit?action=network.agent.invoke`
> 会返回成功与被拒两种，被拒的 `result` 是 `denied`。

### 1.9 「谁能调用我的资源」（FR-N7 按目标粒度授权）

| 做什么 | 应该看到 |
|---|---|
| 以 Owner 身份在网络里发布一个 Agent | 目录行右侧出现**盾牌图标**（只有自己发布的资源才有） |
| 点盾牌图标 | 行**内联展开**「谁能调用这个资源」+ 下拉框（不是弹窗） |
| 下拉切到「仅指定成员」 | 出现成员复选框列表；行首不会显示策略徽标 |
| 不勾选任何成员直接点保存 | 保存**失败**，策略仍是 `network`（空名单等于谁都调不了，后端 400） |
| 勾选一位成员后保存 | 行上出现**「指定成员 (1)」**徽标 |
| 刷新页面 | 徽标仍在 —— 策略是数据库事实，不是前端状态 |
| 再改回「网络内所有有权限的成员」 | 徽标消失 |

> 这个设置的作用：把 Agent 发布到网络 = 让同事**看得到**；设成「仅指定成员」才等于
> 允许他们的 Agent **调用**它。默认是 `network`（网络内有 `network.agent.invoke`
> 权限的成员都能调），与旧行为一致。
>
> 终端可直接验证拒绝语义：把白名单设成只有自己，然后让另一个成员的 Agent 调用
> → **403**，`detail` 含「白名单」。

### 1.10 Agent 调用返回什么（FR-N7 接线后）

`POST /api/v1/installations/{id}/invoke`（**只认 Bearer 工作负载令牌，不认 Cookie**）
授权通过后**会真的投递**给目标 Agent，响应里多三个字段：

| 字段 | 含义 | 该怎么做 |
|---|---|---|
| `delivered` | 是否真的送到了目标 Agent | — |
| `delivery_status` | `ok` / `offline` / `timeout` | `offline`/`timeout` 稍后重试 |
| `response` | 目标 Agent 的响应体 | — |

> ⚠️ **`delivered=false` 仍然返回 200**。授权是成立的，只是目标没上线或卡住了。
> 把它当成 4xx 会让你去排查错误的方向。
>
> 超过速率上限（默认 120 次/分钟每安装实例）返回 **429 + `Retry-After`**。

---

## 2. 完整双人协作链路（验证 8.2 多用户真正可用）

> 这是本次新增功能里**最重要**的一条。用**两个独立浏览器上下文**模拟两个人：
> 一个普通窗口 + 一个隐身窗口（隐身窗口的 Cookie 是隔离的）。
> ⚠️ 用同一个浏览器的两个普通标签页**不行** —— 它们共享 Cookie，会互相顶掉登录。

### 2.1 准备两个账号

- 窗口 1（普通）：注册 `alice-<时间戳>@example.com`
- 窗口 2（隐身）：注册 `bob-<时间戳>@example.com`

> 注册时每人**自动获得自己的个人空间**（租户）。这是理解后面步骤的前提。

### 2.2 Alice 把 Bob 拉进自己的空间

在**窗口 1**：

1. 头像 → 协作网络与空间 → 切到 **「空间」** 页签
2. 在「邀请加入空间」填 Bob 的邮箱 → 点「邀请」
3. 复制蓝色框里的链接（形如 `?tenantInvite=...`）

在**窗口 2**（Bob 已登录）：把这个链接粘到地址栏回车。

| 应该看到 |
|---|
| 右上角提示「已加入空间「Alice 的空间」。到「协作网络 → 空间」可以切换过去」 |
| 打开 协作网络页 → **左侧网络列表是空的** ← **这是租户隔离的证据**：Bob 还在自己的个人空间里，看不到 Alice 的任何网络 |

### 2.3 Bob 切到 Alice 的空间

窗口 2 → 协作网络 → **「空间」** 页签 → 「我所属的空间」里有两个，点 Alice 那个的「切换」。

| 应该看到 |
|---|
| 上方标题变成 Alice 的空间名；原来那个空间的行变成「当前」徽标 |

### 2.4 Alice 建网络并邀请 Bob

窗口 1 → 协作网络页签 → 新建网络 `飞控算法组` → 邀请 Bob 的邮箱 → 复制 `?invite=...` 链接。

窗口 2（Bob）打开这个链接。

| 应该看到 |
|---|
| 提示「已加入网络「飞控算法组」」 |
| 网络列表出现该网络；点开：`2 位成员`、`我的角色：成员` |

### 2.5 验证权限真的收窄了（关键）

窗口 2（Bob 是 **member**）打开网络详情，逐项确认：

| 应该**看不到** |
|---|
| 「邀请加入」表单 |
| 成员行里的角色下拉框（只有只读徽标） |
| 「暂停」「归档」按钮 |
| 移除成员（垃圾桶）按钮 |

> 这些不是"藏起来"而已 —— 第 3 节会用 API 直接打，确认后端也拦。

### 2.6 Alice 把 Bob 升成管理员

窗口 1 → 网络详情 → 成员列表里 Bob 那一行 → 角色下拉改成「管理员」。

切回窗口 2 刷新 → Bob 现在**能看到**「邀请加入」和角色下拉，但**仍然看不到**「归档」——
因为归档是 **Owner 独占**（契约 §5.2 真值表里 owner 与 admin 的唯一差别）。

---

## 3. 用 API 直接验证「后端拦得住」（最有说服力）

前面第 2.5 节只证明了**前端没渲染按钮**。要证明后端真的拦，用 Bob 的浏览器
（窗口 2，隐身窗口，Bob 是 member）按 `F12` 打开控制台，粘贴：

```js
// 1) 拿到 Bob 能看到的第一网络
const [n] = await (await fetch('/api/v1/networks', {credentials:'include'})).json();
console.log('网络:', n.name, '我的角色:', n.role, 'id:', n.id);
```

然后逐条试（每行都会打印 HTTP 状态码）：

```js
// 2) member 尝试归档网络 → 期望 403（归档是 Owner 独占）
await fetch(`/api/v1/networks/${n.id}`, {
  method:'PATCH', credentials:'include',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({status:'archived'})
}).then(r => `归档: ${r.status}`);

// 3) member 尝试邀请人 → 期望 403
await fetch(`/api/v1/networks/${n.id}/invitations`, {
  method:'POST', credentials:'include',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({email:'x@example.com'})
}).then(r => `邀请: ${r.status}`);

// 4) member 尝试改别人的角色 → 期望 403
const members = await (await fetch(`/api/v1/networks/${n.id}/members`, {credentials:'include'})).json();
await fetch(`/api/v1/networks/${n.id}/members/${members[0].user_id}`, {
  method:'PATCH', credentials:'include',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({role:'viewer'})
}).then(r => `改角色: ${r.status}`);

// 5) 猜一个不存在的网络 id → 期望 404（且与"存在但无权看"同形，不泄露存在性）
await fetch('/api/v1/networks/00000000-0000-0000-0000-000000000000', {credentials:'include'})
  .then(r => `猜ID: ${r.status}`);
```

**期望全部：403 / 403 / 403 / 404。**

再验证「最后一位所有者」这条业务规则（用 Alice 的窗口，她唯一 Owner）：

```js
// 在 Alice 的窗口里：退出自己的网络 → 期望 409
const [n] = await (await fetch('/api/v1/networks', {credentials:'include'})).json();
await fetch(`/api/v1/networks/${n.id}/leave`, {
  method:'POST', credentials:'include'
}).then(r => `退出: ${r.status}`);   // 409
```

### 3.6 Agent 相互通信（FR-N7 / FR-N8）—— 一条命令搞定

这是"Agent 之间可相互通信"的落点。**最快的验证方式**：

```powershell
$env:PYTHONUTF8=1
# 在项目根目录执行
.\venv\Scripts\python.exe .planning\project-status\verify_agent_invoke.py
```

**期望**：`结果：16/16 通过`。它走的是**真实 HTTP**（打 Docker 部署），覆盖：

| 断言 | 说明 |
|---|---|
| 签发工作负载凭证 | 凭证形如 `w1.<installation_id>.<exp>.<jti>.<sig>` |
| **用用户 Cookie 调用 → 401** | **FR-N7 的核心**：Agent 不能拿用户会话冒充 |
| 带工作负载凭证调用 → 200 | 正常放行，响应带 `audit_event_id` |
| 调用白名单外目标 → 404 | 目标必须已发布到该网络 |
| **卸载后用同一个旧凭证 → 404** | **FR-N8 的核心**：撤权不等凭证过期 |

> 脚本会消耗 1 次注册限流。用尽时 `docker compose restart backend`。

**为什么"撤权即时生效"值得单独验**：凭证本身有 10 分钟有效期，但判定链每一步都
**现查数据库**。所以卸载安装 / 撤回发布 / 移除成员 / 降级角色 / 暂停网络之后，
**下一次调用就失败**，不需要等凭证过期 —— 上面最后一条断言就是在验证这一点：
它复用卸载前签发的 token，不重新签发。

想手工看的话（Alice 的窗口控制台）：

```js
// 1) 拿自己的安装列表
const mine = await (await fetch('/api/v1/me/installations', {credentials:'include'})).json();
const inst = mine[0].id;

// 2) 签发凭证（只显示一次，抄下来）
const cred = await (await fetch(`/api/v1/installations/${inst}/credential`, {
  method:'POST', credentials:'include'
})).json();
console.log('token:', cred.token, '剩余秒:', cred.expires_in);

// 3) 不带凭证调用 → 401
await fetch(`/api/v1/installations/${inst}/invoke`, {
  method:'POST', credentials:'include',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({networkId:'<网络id>', targetResourceType:'agent', targetReleaseId:'<目标版本>'})
}).then(r => `无凭证: ${r.status}`);

// 4) 带凭证调用 → 200
await fetch(`/api/v1/installations/${inst}/invoke`, {
  method:'POST',
  headers:{'Content-Type':'application/json', 'Authorization': `Bearer ${cred.token}`},
  body: JSON.stringify({networkId:'<网络id>', targetResourceType:'agent', targetReleaseId:'<目标版本>'})
}).then(r => `带凭证: ${r.status}`);
```

---

## 4. 自动化验证

### 4.1 后端单元 + 集成测试（最快，10 秒）

```powershell
$env:PYTHONUTF8=1
# 在项目根目录执行
.\venv\Scripts\python.exe -m pytest -q
```

**期望**：`176 passed, 3 skipped`

那 3 个 skipped 是**并发用例**，在 SQLite 上被刻意跳过（理由见 4.2）—— 不是失败。

按主题单跑：

```powershell
# 权限真值表（契约 §5.2 的可执行副本）
.\venv\Scripts\python.exe -m pytest tests/unit/test_network_permissions.py -v
# 网络 API：隔离、邀请、角色、状态机
.\venv\Scripts\python.exe -m pytest tests/integration/test_network_api.py -v
# 租户邀请 / 切换 / 端到端多用户链路
.\venv\Scripts\python.exe -m pytest tests/integration/test_tenant_api.py -v
# installation 表结构与约束
.\venv\Scripts\python.exe -m pytest tests/unit/test_db_foundation.py -k Installation -v
```

### 4.2 PostgreSQL 并发验证（可选，但**推荐看一眼**）

SQLite 是单写者，会把并发写串行化 —— 所以并发正确性**只能在 PG 上验证**。

```powershell
# 起容器（已有就跳过）
docker run -d --name sotaband-pg -e POSTGRES_USER=sotaband -e POSTGRES_PASSWORD=sotaband `
  -e POSTGRES_DB=sotaband -p 55432:5432 postgres:16-alpine
docker exec sotaband-pg psql -U sotaband -d sotaband -c "CREATE DATABASE sotaband_test;"

# 跑并发用例
$env:PYTHONUTF8=1
$env:SOTABAND_TEST_DATABASE_URL='postgresql+asyncpg://sotaband:sotaband@localhost:55432/sotaband_test'
# 在项目根目录执行
.\venv\Scripts\python.exe -m pytest tests/integration/test_network_concurrency.py -v
```

**期望**：`3 passed`。这三条覆盖：并发改角色只有一个成功、并发接受邀请只产生一条
成员关系、**并发互相移除 Owner 不会把网络变成 0 位 Owner 的孤儿**。

> 第三条是真实抓到的 bug：不加行锁时在 PG 上会输出网络 0 位 Owner。
> 想看它失败长什么样，把 `core/auth/authorize.py::lock_network` 里的
> `.with_for_update()` 注释掉再跑。

顺带验证**全量测试跨库通过**：

```powershell
.\venv\Scripts\python.exe -m pytest -q     # 期望 178 passed, 1 skipped
```

（PG 上跳过的 1 条是 SQLite 特有的 Uuid 存储格式用例。）

### 4.3 端到端 UI 测试（29 项断言）

```powershell
$env:PYTHONUTF8=1
# 在项目根目录执行
# 需要后端 8001 + 前端 5173 都在跑
.\venv\Scripts\python.exe .planning\project-status\verify_network.py
```

**期望**：`结果：29/29 通过`，截图在 `.planning/project-status/screens/30-*.png ~ 36-*.png`。

这条脚本会真实开两个隔离的浏览器上下文，把第 2 节的整条链路自动走一遍，并断言
普通成员看不到管理入口。

登录回归（14 项）：

```powershell
.\venv\Scripts\python.exe .planning\project-status\verify_login.py
```

### 4.4 迁移与静态检查

```powershell
$env:PYTHONUTF8=1
# 在项目根目录执行
.\venv\Scripts\python.exe -m alembic current     # 期望 0007 (head)
.\venv\Scripts\python.exe -m alembic check       # 期望 No new upgrade operations detected
.\venv\Scripts\python.exe -m ruff check core app migrations tests

cd frontend
node .\node_modules\typescript\bin\tsc -b        # 期望 exit 0
node .\node_modules\oxlint\bin\oxlint src        # 期望 0 errors
```

---

## 5. 数据库里到底长了什么

一条命令搞定（脚本已固化，两个库都支持）：

```powershell
$env:PYTHONUTF8=1
# 在项目根目录执行
.\venv\Scripts\python.exe scripts\inspect_db.py

# 或看 PostgreSQL
$env:DATABASE_URL='postgresql+asyncpg://sotaband:sotaband@localhost:55432/sotaband'
.\venv\Scripts\python.exe scripts\inspect_db.py
```

**期望输出**（关键三行）：

```text
迁移版本：0007

外键数量：0   （期望 0 —— 阿里开发手册【强制】不得使用外键与级联）
✅ 13 张业务表齐全
✅ 外键数量为 0，符合阿里开发手册（无外键、无级联）
```

完整的 13 张业务表（+`alembic_version`）：

```text
user  user_identity  user_credential  session  audit_event
tenant  tenant_membership  tenant_invitation
network  network_membership  network_invitation
installation  publication
```

> `publication` 已在迁移 `0006` 建好（批次 5）；`installation.publication_id` 是逻辑引用
> （无外键，ADR-08-11），所以不会报错。


---

## 6. 想推倒重来

```powershell
# 只清数据（表结构保留）：删掉 SQLite 文件，下次启动会自动建
# 在项目根目录执行
Remove-Item storage\sotaband.db* -Force

# 或者跑迁移重建
.\venv\Scripts\python.exe -m alembic upgrade head
```

再用新邮箱注册即可（旧账号随库一起没了）。

---

## 7. 对照表：每条需求落在哪

| 需求 | 怎么验 |
|---|---|
| FR-A1 注册即建个人空间 | 1.1 + `scripts/inspect_db.py` 看 `tenant` / `tenant_membership` 各多一行 |
| FR-A2 登录 | 1.1 |
| FR-A3 会话管理与当前用户 | 1.1 刷新保持登录；`GET /api/v1/me` |
| FR-A4 退出登录 | 1.1 头像菜单「退出登录 / 退出全部设备」 |
| FR-A5 账号安全与审计 | 限流 429、登录审计、**改密码：1.7** |
| FR-A6 租户上下文与切换 | 2.2–2.3 |
| FR-A7 无未登录闪现 | 1.1 第一行 |
| FR-N1 网络创建、创建者即 Owner | 1.2 |
| FR-N2 网络生命周期（暂停/归档/恢复） | 1.4 + 3 节 API 直打 |
| FR-N3 邀请与加入 | 2.2；邮箱不匹配 → 403 |
| FR-N4 成员与角色管理 | 2.5 + 2.6（member 看不到管理入口；admin 看不到归档） |
| FR-N5 网络资源共享（发布/安装） | 3.6 + 4.1 的 `test_sharing_api.py` |
| FR-N6 网络资源目录 | 3.6 + 4.1 的 `test_sharing_api.py::TestCatalog` |
| **FR-N7 网络内 Agent 通信** | **3.6（凭证 + 调用 + Cookie 冒充被拒）+ 1.9（按目标白名单）+ 1.10（投递结果与限流）** |
| **FR-N8 撤权** | **3.6 最后一条断言（卸载后旧凭证立刻失效）** |
| FR-N9 网络策略与审计 | 策略：1.4 的 `allowMemberPublish` / `allowViewerInvoke`；**审计查询：1.8** |
| 契约 §5.2 权限真值表 | 4.1 的 `test_network_permissions.py`（表驱动，19 条参数化断言） |
| 数据隔离（404 vs 403） | 3 节第 5 条 + `test_network_api.py::TestIsolation` |
| PostgreSQL 并发正确性 | 4.2 |

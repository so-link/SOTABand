# 需求 8.1 / 8.2 验收手册（提交版）

> 适用对象：代码评审人 / 验收人 / 你自己提交前的最后一遍自检。
> 深度的功能说明见 [`verify-08-guide.md`](./verify-08-guide.md)；
> 设计取舍见 `design-08-*.md` 与 `adr-08-*.md`。
> 本文只回答三件事：**做完了吗、怎么验、边界在哪。**

---

## 0. 一句话结论

| 需求 | 覆盖 | 状态 |
|---|---|---|
| **8.1 用户空间与用户网络 / 用户注册与登录** | FR-A1 ~ FR-A7 | ✅ 全部实现 |
| **8.2 用户组网（虚拟局部网络、共享工具/数据、Agent 互通信）** | FR-N1 ~ FR-N9 | ✅ 全部实现 |

未越界实现 PRD §2.4 声明的非目标（Agent 底层 RPC/传输层协议）——
但**授权之后的投递已接线**，只是复用仓库既有的 `MessageBus` 抽象，没有引入新传输层依赖。

---

## 1. 环境准备（3 条命令）

```powershell
# 1) 起服务（首次或代码有改动时用 --build）
docker compose up -d --build

# 2) 等三个容器都 healthy / Up
docker compose ps

# 3) 浏览器打开
#    http://localhost:5173
```

期望：

```text
sotaband-backend    | Up (healthy)
sotaband-db         | Up (healthy)
sotaband-frontend   | Up
```

> 端口只暴露 `5173`（前端 Nginx）。后端 `8001` 只在 compose 内部网络可达，
> 这是刻意的：认证 Cookie 的 `SameSite=Lax` + 同源反代是 CSRF 防线的一部分。

---

## 2. 自动化验收（照抄即可）

### 2.1 后端测试

```powershell
$env:PYTHONUTF8=1
.\venv\Scripts\python.exe -m pytest -q
```

**期望：`272 passed, 3 skipped`**

> 3 个 skip 是 PostgreSQL 专属用例，没设 `SOTABAND_TEST_DATABASE_URL` 时跳过（下面 2.4 会跑）。

### 2.2 静态检查

```powershell
# 后端：本模块范围内的文件，期望 All checks passed
.\venv\Scripts\python.exe -m ruff check `
  core/auth core/sharing core/network core/tenant core/db `
  core/agent/bus.py core/agent/invoke_bus.py `
  app/api/deps.py app/main.py core/errors.py config/settings.py `
  app/api/routes/auth_routes.py app/api/routes/me_routes.py `
  app/api/routes/network_routes.py app/api/routes/tenant_routes.py `
  app/api/routes/sharing_routes.py `
  app/api/schemas/auth_schemas.py app/api/schemas/network_schemas.py `
  app/api/schemas/tenant_schemas.py app/api/schemas/sharing_schemas.py `
  migrations tests

# 数据库无漂移，期望 No new upgrade operations detected.
.\venv\Scripts\python.exe -m alembic check

# 前端类型检查，期望无输出
cd frontend
node .\node_modules\typescript\bin\tsc -b
node .\node_modules\oxlint\bin\oxlint src
```

**期望：ruff 输出 `All checks passed!`；alembic 无漂移；tsc 无输出；
oxlint `0 error`（13 条 warning 是历史 `exhaustive-deps` 等，非本次引入）。**

> ⚠️ **不要用 `ruff check core app migrations tests` 这条全仓命令** ——
> 仓库既定的 lint 口径是它，但它当前有 **339** 条问题，全部落在
> tool / agent / data 资源生成器等历史模块。本模块 22 个路径是 **0** 条。
> 详见 §5 第 2 项。

### 2.3 数据库结构

```powershell
docker compose exec -T backend python scripts/inspect_db.py
```

**期望：**

```text
迁移版本：0007
✅ 13 张业务表齐全
✅ 外键数量为 0，符合阿里开发手册（无外键、无级联）
```

### 2.4 PostgreSQL（可选，验证并发正确性）

```powershell
$env:SOTABAND_TEST_DATABASE_URL='postgresql+asyncpg://sotaband:sotaband@localhost:55432/sotaband_test'
.\venv\Scripts\python.exe -m pytest tests/integration/test_network_concurrency.py tests/integration/test_db_session.py -q
```

验证「两个 owner 互相移除」不会把网络变成 0 个 owner —— 靠 `SELECT … FOR UPDATE` 行锁。

### 2.5 端到端（浏览器真实操作）

```powershell
$env:PYTHONUTF8=1
# ⚠️ 每个脚本前重启后端：注册限流是「进程级内存」，5 次/小时后会返回 429
foreach ($f in 'verify_login','verify_password_toggles','verify_network',
               'verify_sharing_ui','verify_account_audit_ui',
               'verify_agent_invoke','verify_invoke_policy_ui','smoke_file_preview') {
  docker compose restart backend | Out-Null
  Start-Sleep -Seconds 10
  .\venv\Scripts\python.exe ".planning\project-status\$f.py" 2>&1 | Select-String '结果：'
}
```

**期望（8 个脚本、共 141 项断言全通过）：**

| 脚本 | 断言 | 覆盖 |
|---|---|---|
| `verify_login.py` | **17/17** | 注册、登录、会话、退出、密码策略提示 |
| `verify_password_toggles.py` | **15/15** | 两个密码框的显示开关互不影响 + 布局 |
| `verify_network.py` | **29/29** | 建网、邀请、成员角色、状态机、隔离 |
| `verify_sharing_ui.py` | **20/20** | 发布、目录、安装、卸载 + 布局重叠检测 |
| `verify_account_audit_ui.py` | **19/19** | 改密码、审计日志、member 权限边界 |
| `verify_agent_invoke.py` | **16/16** | 工作负载凭证、Agent 调用、撤权 |
| `verify_invoke_policy_ui.py` | **15/15** | 按目标白名单 UI + 布局重叠检测 |
| `smoke_file_preview.py` | **10/10** | 上传/文件树/预览（统一 client 迁移的运行时验证） |

脚本会自动截图到 `.planning/project-status/screens/`。

---

## 3. 需求对照表（评审可直接查）

| 需求 | 自动化证据 | 手工验 |
|---|---|---|
| FR-A1 注册 | `test_auth_api.py` + `verify_login.py` | 1.1 |
| FR-A2 登录/会话 | `test_auth_api.py`、`test_auth_core.py` | 1.1 |
| FR-A3 退出/退出全部设备 | `test_auth_api.py` | 1.2 |
| FR-A4 密码策略 | `test_password_hashing.py`、`test_auth_core.py` | 1.1 |
| **FR-A5 改密码 / 吊销全部会话** | `test_account_and_audit.py` → `TestChangePassword` | **1.7** |
| FR-A6 租户上下文与切换 | `test_tenant_api.py` | 2.1 |
| FR-A7 前端认证体验 | `verify_login.py`、`verify_password_toggles.py` | 1.1/1.2 |
| FR-N1 建网 | `test_network_api.py` | 1.3 |
| FR-N2 网络生命周期 | `test_network_api.py`、`test_network_concurrency.py` | 1.3 |
| FR-N3 邀请与加入 | `test_network_api.py`、`verify_network.py` | 2.2 |
| FR-N4 成员与角色 | `test_network_permissions.py`（表驱动，23 个用例） | 1.4 |
| FR-N5 资源共享（发布/安装） | `test_sharing_api.py`、`verify_sharing_ui.py` | 1.5 |
| FR-N6 网络资源目录 | `test_sharing_api.py::TestCatalog` | 1.6 |
| **FR-N7 Agent 相互通信** | `test_agent_invoke.py`（39 条）+ `verify_agent_invoke.py` | **1.9 / 1.10 / 3.6** |
| FR-N8 撤权 | `test_agent_invoke.py` → 5 条撤权路径 | 3.6 最后一条 |
| **FR-N9 网络策略与审计** | `test_account_and_audit.py::TestNetworkAudit` | **1.8** |
| 契约 §5.2 权限真值表 | `test_network_permissions.py` | — |
| 数据隔离（404 vs 403） | `test_network_api.py::TestIsolation` | 3 节第 5 条 |
| PostgreSQL 并发正确性 | `test_network_concurrency.py` | 2.4 |

---

## 4. 手工验收（10 分钟，挑关键路径）

> 详细步骤见 `verify-08-guide.md` 的 1.1–1.10。这里只列**必看**的 6 条。

### 4.1 注册 → 登录 → 改密码（8.1 主线）

1. 注册一个账号，密码输入 `abcdefgh` → 规则清单第三个「包含数字」应显示 ✗
2. 改成 `Passw0rd2026` → 三条全绿 → 创建账号
3. 右上角头像 → **修改密码**
4. 当前密码填错 → 弹窗内红字「当前密码不正确」，**仍停在弹窗**（不能被登出）
5. 填对 → 被踢到登录页，提示「密码已修改，所有设备需要重新登录」
6. 旧密码登录失败，新密码登录成功

### 4.2 注册页两个密码框的眼睛各管各的

在注册模式：点「确认密码」右边的眼睛 → **只有确认密码变明文**，上面的密码仍是掩码。

### 4.3 建网 → 邀请 → 角色

Owner 建网 → 邀请另一个账号（对方需先在同一租户） → 对方接受 →
成员列表里能看到他，角色可改为 admin/member/viewer。

### 4.4 发布 → 目录 → 安装

在网络详情「资源目录」发布一个 Agent → 目录出现该行 → 点「安装」→ 变「已安装」→
切到「我的安装」能看到它。

### 4.5 谁能调用我的资源（按目标白名单）

自己发布的资源行上有**盾牌图标** → 点开 → 选「仅指定成员」→ 勾选一位成员 → 保存 →
行上出现「指定成员 (1)」徽标 → 刷新页面徽标仍在（**数据库事实**）。

> 不勾选任何人直接保存 → 失败（空名单等于谁都调不了，后端 400）。

### 4.6 审计日志

网络详情底部有「审计日志」，列出最近 20 条：
`时间 · action · 主体徽标（用户/Agent）· 结果徽标`。
触发一次 Agent 调用后，能看到 `network.agent.invoke` 且主体是 **Agent**。
用 **member** 账号看同一网络 → **看不到这块**（`network.audit.read` 仅 Owner/Admin）。

---

## 5. 已知限制与非目标（**评审重点，不藏着**）

| # | 项 | 说明 | 影响 |
|---|---|---|---|
| 1 | **Agent 底层传输层不做** | PRD §2.4 明确列非目标。本模块做的是**授权 + 投递接线**，传输复用既有 `MessageBus` 抽象（可替换成跨机实现） | 符合 PRD，非缺陷 |
| 2 | **仓库级 ruff 未全绿** | 既定口径 `ruff check core app migrations tests` 有 **339** 条，**全部**在 tool/agent/data 资源生成器、chat、file 等历史模块；**本模块 22 个路径 0 条** | 未触碰他人代码；§2.2 给的是精确清单 |
| 3 | **注册限流是进程级内存** | `5 次/小时/IP`，重启后端清零；多 worker 部署时各进程独立计数 | 测试时需重启后端；生产上多 worker 会放宽实际阈值 |
| 4 | **工作负载密钥未配时随机生成** | 未设 `SOTABAND_WORKLOAD_SECRET` 时进程启动随机生成，重启后所有 Agent 凭证失效，且多 worker 互不认 | **生产必须显式配置** |
| 5 | **邀请令牌只返回一次** | 库里只存 hash，创建后页面展示那一次是唯一机会 | 前端已提示「立即复制」 |
| 6 | **网络属且仅属一个租户** | PRD 开放问题 2 的决策；跨租户共享不在本需求内 | 设计约束 |
| 7 | **前端 13 条 oxlint warning** | 主要是 `exhaustive-deps`、`no-useless-escape`，历史代码 | 非 error，不影响功能 |
| 8 | **3 个 PostgreSQL 用例默认 skip** | 未设 `SOTABAND_TEST_DATABASE_URL` 时跳过 | 按 2.4 单独跑 |
| 9 | **密码策略是产品决策** | 8–20 位 + 必须含字母和数字。这与现代「长度优于复杂度」的共识不同，是**明确的产品选择**（便于向用户解释），不是安全最优 | 已在设计文档记录 |

---

## 6. 提交前检查清单

```text
[ ] docker compose ps 三个容器都 healthy/Up
[ ] 后端 pytest → 272 passed, 3 skipped
[ ] ruff（本模块路径）→ All checks passed
[ ] alembic check → No new upgrade operations detected
[ ] 前端 tsc -b → 无输出
[ ] 前端 oxlint src → 0 error
[ ] inspect_db.py → 13 表 / 0 外键 / 0007
[ ] 8 个 E2E 脚本 → 141 项全通过
[ ] 浏览器手工走过 4.1 ~ 4.6 六条关键路径
[ ] 截图确认无元素重叠（screens/ 目录）
[ ] 通读 §5 已知限制，确认可以接受
```

> ⚠️ **本次开发全程未执行任何 git 操作**（按你的要求）。
> 工作区有未提交改动，请自己 review 后再提交。新增文件清单见 §7。

---

## 7. 本次新增（便于 code review 定位）

**后端**

```text
core/auth/workload.py                                   工作负载身份（HMAC 短期令牌）
core/agent/invoke_bus.py                                Agent 调用投递（FR-N7 接线）
migrations/versions/0007_publication_invoke_allowlist.py 按目标白名单两列
tests/integration/test_account_and_audit.py              改密码 + 审计查询（14 条）
```

**前端**

```text
frontend/src/services/api/client.ts                     统一 API 客户端（分层 + apiUrl + assertOk）
frontend/src/services/api/sharing.ts / network.ts / …    全部 service 走统一 client
frontend/src/lib/password.ts                            密码规则前端唯一副本
frontend/src/components/account/ChangePasswordDialog.tsx 改密码弹窗（三个独立显示开关）
```

**验收脚本**

```text
.planning/project-status/verify_account_audit_ui.py      19 项
.planning/project-status/verify_invoke_policy_ui.py      15 项
.planning/project-status/verify_password_toggles.py      15 项
.planning/project-status/smoke_file_preview.py           10 项
```

---

## 8. 关键架构事实（给评审人）

| 项 | 事实 |
|---|---|
| 技术栈 | FastAPI + SQLAlchemy 2.0 async + Alembic；React 19 + TS 6 + Vite 8 + Tailwind 4 + Zustand 5 |
| 数据库 | Docker/生产 PostgreSQL 16（asyncpg）；本地开发默认 SQLite（aiosqlite） |
| 接口总数 | **109** |
| 业务表 | **13**，**0 外键**（阿里开发手册：不得使用外键与级联） |
| 迁移 | 0001 ~ **0007**，`alembic check` 无漂移 |
| 表命名 | 单数（`user` / `network` / `publication` …），统一 `id` / `gmt_create` / `gmt_modified` |
| 密码 | `bcrypt`（官方包，work factor 12；测试用 4） |
| 会话 | 不透明令牌 `secrets.token_urlsafe(32)`，库存 SHA-256，HttpOnly + SameSite=Lax Cookie |
| Agent 身份 | HMAC 短期令牌 `w1.<installation_id>.<exp>.<jti>.<sig>`；**是工牌不是通行证** —— 每次调用现查 DB 推授权 |
| 前端请求 | **裸 `fetch` 归零**，全部经统一 client（`credentials: 'include'` + 统一 401 处理） |
| 级联删除 | **无**。所有 `*_id` 是逻辑引用，存在性由应用层保证 |

## 7. 安全加固验收（v2，2026-11-04 增补）

| # | 用例 | 预期 |
|---|---|---|
| 1 | 未登录访问 `/api/data/list`、`/api/file/list`、`/api/tool/list`、`/api/model/list`、`/api/task/list`、`/api/config/llm`、`/api/migrate/bundles` | 401；`/api/auth/session`、`/api/health` 仍 200 |
| 2 | 工具详情页执行会注册数据集的工具 | 数据集 owner = 登录用户 UUID（不是 `local-xxxx`） |
| 3 | 同一 Agent/工具进程先后被 A、B 使用 | 两次注册的数据集 owner 分别为 A、B（不串号） |
| 4 | B 发布 A 的私有数据集/私有工具 | 404（不泄露存在性）；发布自己的 → 201 |
| 5 | B 读取/删除 A 的数据集 | 404；仓库列表默认不出现他人条目；`include_others` 非管理员 403 |
| 6 | `/api/file/download?path=../.env`、`/api/file/delete` 越界路径 | 403；允许根内文件正常 |
| 7 | `/api/tool/upload-test-file` 文件名带 `..\..\` | 400/落盘仍在允许目录 |
| 8 | 登记 data_path 指向 `storage/` 或项目根 | 400 |
| 9 | 非管理员改全局 LLM 配置 / 测试 LLM 连接 | 403 |
| 10 | B 覆盖 A 的私有工具（migrate `overwrite=true`） | 404/400；A 自己覆盖 → 200 |
| 11 | A、B 的工具空间/工作区文件树 | 互相不可见、不互相覆盖 |
| 12 | 删除工具/数据集/文件、保存代码、改 LLM 配置 | 审计表有对应 `action`，`actor_id` 正确 |
| 13 | Agent Web 服务端口无令牌直连 | 401；`/_health` 免令牌；仅监听 127.0.0.1 |
| 14 | 普通成员 `GET /api/data/list?include_others=true` | 403 |
| 15 | 普通成员用 register/注册接口覆盖他人工具/Agent/数据集/模型 | 404（不能接管） |
| 16 | A 的上传附件被 B list/download/delete；删除 `resources-private/**/registry.json` | 均被拒绝（按归属/敏感黑名单） |
| 17 | 同一 Agent 两用户并发执行 / 一用户两标签页 | 事件不串号（工厂锁+失步恢复）；断流后下一请求不继承残留输出 |
| 18 | Web 服务式 Agent 调用的数据归属 | 数据集 owner = 发起调用的用户 UUID（签名身份头透传）；验签失败退回机器身份 |
| 19 | 迁移包 zip 成员含 Windows 盘符绝对路径（`E:/...`） | 导入拒绝；不覆盖任意文件 |
| 20 | 默认 SQLite 下两个并发请求移除/降级/退出最后一位 Owner | 至少保留 1 位 Owner（败者 409/404）；过期邀请点击后状态落库为 expired |

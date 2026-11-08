# 系统使用手册：用户空间与用户网络（8.1 / 8.2）

> 适用版本：本 PR（`feat/8.1-8.2-user-space-network`，含 v2/v3 安全加固）
> 面向读者：普通使用者、租户管理员、平台管理员、运维
> 相关文档：[需求索引](index-08-user-space-and-network.md) ·
> [认证契约](contract-08-auth-network.md) · [隔离设计](design-08-isolation.md) ·
> [验收清单](acceptance-08-submission.md)

---

## 1. 部署与首次访问

### 1.1 启动方式

**Docker（推荐）**

```powershell
docker compose up -d --build     # 后端容器启动时自动 alembic upgrade head
docker compose ps                # db / backend / frontend 均应为 running/healthy
```

打开 <http://localhost:5173>（前端）；后端 API 默认 `http://127.0.0.1:8001`。

**本机开发模式**

```powershell
python -m alembic upgrade head   # 首次/升级后必须执行
uvicorn app.main:app --port 8001 # 后端
cd frontend; npm run dev         # 前端（Vite）
```

### 1.2 上线前必做

1. 配置 `SOTABAND_WORKLOAD_SECRET`（≥32 字节随机串）——不配则重启后 Agent 工作负载
   凭证失效、多 worker 互不认；
2. 生产 https 环境设置 `SOTABAND_COOKIE_SECURE=1`；
3. 配置 `SOTABAND_CORS_ORIGINS`（显式来源列表，不能用 `*`）；
4. 按需配置 `SOTABAND_PLATFORM_ADMINS`（见 §3.2）；
5. 执行 `python -m alembic upgrade head`（容器入口脚本会自动执行）。

完整环境变量见仓库根目录 `.env.example`。

---

## 2. 账号体系总览

| 概念 | 说明 |
|---|---|
| **用户（User）** | 一个可登录账号（邮箱 + 密码）；内部 ID 为 UUID |
| **个人租户（Personal Tenant）** | 每个用户注册时自动创建，**该用户是 Owner**。个人租户里的资源默认只有自己可见 |
| **共享租户（Tenant）** | 多人协作空间；由 Owner 创建并邀请成员，角色为 owner / admin / member |
| **用户网络（Network）** | 8.2 的组网单位：成员可在网络内发布/安装/调用资源；角色为 owner / admin / member / viewer |
| **平台管理员（Platform Admin）** | **部署级**管理员（不是租户角色），由运维通过环境变量指定，管理安装级全局配置 |

### 2.1 角色能力速查

**租户角色（Tenant Role）**

| 能力 | owner | admin | member |
|---|---|---|---|
| 创建共享租户 / 修改租户信息 | ✅ | ✅ | ❌ |
| 邀请/移除成员、改成员角色 | ✅ | ✅（不可动 Owner） | ❌ |
| 查看成员列表 | ✅ | ✅ | ✅ |

**网络角色（Network Role）**

| 能力 | owner | admin | member | viewer |
|---|---|---|---|---|
| 查看网络/成员/目录 | ✅ | ✅ | ✅ | ✅ |
| 修改网络信息、邀请成员 | ✅ | ✅ | ❌ | ❌ |
| 管理成员角色 | ✅ | ✅（不可动 Owner） | ❌ | ❌ |
| 归档/恢复/暂停网络 | ✅ | ❌ | ❌ | ❌ |
| 发布资源到网络 | ✅ | ✅ | 取决于 `allowMemberPublish` | ❌ |
| 安装/使用网络资源 | ✅ | ✅ | ✅ | 取决于 `allowViewerInvoke` |

**资源写权限（工具 / Agent / 模型）**

| 操作 | 资源 owner（创建者） | 租户 Owner/Admin | 其他登录用户 |
|---|---|---|---|
| 查看 / 运行 / 调用 | ✅ | ✅ | ✅（共享资产） |
| 改代码 / 改规范 / 标签 / 装依赖 / 删除 | ✅ | ✅ | ❌ |
| 内置资产（无 owner） | — | 仅租户 Owner/Admin | ❌ |

> 数据集是**私有资源**：只有创建者本人可读/改/删；平台管理员可用于排查/迁移。
> "共享"体现在工具/Agent/模型可被使用，"改"仍受 owner 限制。

---

## 3. 注册、登录与管理员账号

### 3.1 普通账号注册与登录

**界面操作**：打开前端 → 登录页选择「注册」→ 填写邮箱、密码、显示名（可选）→
注册成功后自动登录。

**API**（供脚本/联调）：

```http
POST /api/auth/register
Content-Type: application/json

{"email": "alice@example.com", "password": "Passw0rd2026", "displayName": "Alice"}
```

- 密码策略：**8–20 位**，必须同时包含**字母和数字**；bcrypt 存储；
- 注册成功即创建个人租户，该用户为 Owner；
- 登录：`POST /api/auth/login`，成功后写入 HttpOnly 会话 Cookie；
- 会话探测：`GET /api/auth/session`；登出：`POST /api/auth/logout`；
  全端登出：`POST /api/auth/logout-all`；改密：`POST /api/v1/me/password`
  （改密会吊销全部会话，需重新登录）。

### 3.2 如何获得"管理员账号"（重点）

本系统有**两种**管理员，来源不同，不要混淆：

#### A. 租户管理员（空间管理员）——注册即拥有个人空间的 Owner

1. 正常注册账号（§3.1）；
2. 该账号**自动成为自己个人租户的 Owner**，可以管理个人空间内的一切资源；
3. 如需多人协作空间：在「网络/空间」页面创建共享租户 → 邀请成员（邮箱）→
   被邀请者接受邀请后成为成员；Owner 可在成员管理中把成员设为 `admin` 或 `member`；
4. 租户 Owner 可移除成员、修改角色；Admin 可管理普通成员但**不可动 Owner**。

> 注意：个人租户的 Owner 身份是"空间内"的，不是安装级管理员；
> 不能借此修改全局 LLM 配置或查看他人私有数据（见 §3.2 B）。

#### B. 平台管理员（安装级管理员）——由运维通过环境变量指定

平台管理员能修改**安装级全局配置**（全局 LLM Key/模型）、查看他人数据集（排查/迁移）、
认领历史数据、测试 LLM 连接。**没有"注册入口"**，必须由部署者配置：

1. **先注册一个普通账号**（或用已有账号）；
2. 取得该账号的 `user_id`（UUID），两种方式：
   - 登录后访问 `GET /api/v1/me`，响应中的 `user_id` 字段；
   - 或直接查数据库（开发默认 SQLite）：
     `sqlite3 storage/sotaband.db "select id,email from users;"`；
3. 编辑运行环境变量（`.env` 或 docker-compose 环境）：
   ```dotenv
   # 逗号分隔的 user UUID；大小写不敏感；留空 = 没有平台管理员
   SOTABAND_PLATFORM_ADMINS=5f0c…-…,9a1b…-…
   ```
4. **重启后端**（环境变量在启动时读取）；
5. 重新登录/刷新前端：
   - `GET /api/v1/me` 返回 `is_platform_admin: true`；
   - 「设置」页出现全局大模型配置（非平台管理员看不到、也不发请求）；
   - 数据集页面出现"查看他人数据集/认领历史数据"等管理入口。

> 安全口径：`SOTABAND_PLATFORM_ADMINS` 为空时**没有任何平台管理员**（fail-closed）。
> 不要用"租户 Owner"充当安装级管理员——个人租户下每个注册用户都是自己的 Owner。

### 3.3 切换空间与多空间

- `GET /api/v1/me/tenants`：我加入的所有租户；
- `POST /api/v1/me/switch-tenant`：切换激活租户（只能切到自己 active 成员的空间）；
- 前端右下角用户菜单可切换空间、改密码、管理会话、登出。

---

## 4. 界面导览（登录后）

| 区域 | 功能 |
|---|---|
| **对话（Chat）** | 与交互式 Agent 对话；工具卡片内联展示（表格/图片/文件） |
| **工具空间** | 已加载工具；「工具仓库」可浏览/添加/删除（删除仅 owner/管理员可见按钮） |
| **Agent** | Agent 仓库与详情：规范、代码、运行/重启、Web 服务 API 测试 |
| **数据集** | 数据集仓库、导入（上传/登记目录）、预览、文件列表、删除；他人数据不可见 |
| **模型** | 模型仓库、分组（分组管理仅租户管理员）、模型详情 |
| **网络** | 创建/加入用户网络；成员、邀请、发布、目录、安装、审计 |
| **设置** | 全局大模型配置（**仅平台管理员可见**） |
| **任务监控** | 长任务列表/取消（**只显示自己的任务**） |
| **用户菜单** | 当前空间/角色、切换空间、修改密码、会话管理、登出 |

只读态：打开他人或内置的工具/Agent 时，编辑/保存/删除按钮隐藏或禁用——
这是**体验层**提示，后端仍有权限校验（返回 404/403）。

---

## 5. 用户网络使用流程（8.2）

### 5.1 创建与邀请

1. 登录后进入「网络」→ 创建网络（名称/描述）；
2. 创建者是网络 Owner；
3. 邀请成员：Owner/Admin 输入对方**注册邮箱**生成邀请（有效期 7 天）；
4. 被邀请者登录后接受邀请：`POST /api/v1/network-invitations/{token}/accept`；
5. 成员角色可被 Owner/Admin 调整（Admin 不能动 Owner、不能把自己升 Owner）。

### 5.2 发布资源到网络

1. 网络 Owner/Admin 先在网络设置里决定策略：
   - `allowMemberPublish`：是否允许普通成员发布资源（默认关）；
   - `allowViewerInvoke`：是否允许 viewer 调用已安装 Agent；
2. （可选）网络详情页把状态置为 `active`；
3. 在资源详情/发布入口选择资源（数据集/工具/Agent）与目标网络；
4. 规则：
   - **数据集**：只有资源创建者本人能发布；
   - **工具/Agent**：创建者本人，或租户 Owner/Admin（代发布）；
   - 发布前会校验资源归属，无权/不存在统一 404（不泄露存在性）；
   - 同一资源重复发布：命中他人条目会 409（网络）/404（私有）。
5. 发布后可在「网络目录」看到条目；撤回：发布者本人或网络 Owner/Admin。

### 5.3 安装与调用

1. 网络成员在目录中选择资源安装到自己的空间
   （安装唯一键：租户 + 安装者 + 发布记录）；
2. 安装后可申请调用凭证（`POST /api/v1/installations/{id}/credential`，
   短 TTL），再用凭证调用网络 Agent（`POST /api/v1/installations/{id}/invoke`，
   Bearer Token）；
3. 调用授权链会校验：网络成员身份、安装归属、发布状态、网络策略、
   发起者租户成员/用户状态、资源白名单等；
4. 卸载：仅本人（或对应权限）；资源被删除后其发布自动撤回，已装项将不可再调用。

### 5.4 审计

网络 Owner/Admin 可在网络详情查看审计列表（`GET /api/v1/networks/{id}/audit`），
包含创建/改角色/邀请/发布/安装/调用等事件。资源删除、代码变更等审计写
租户维度（当前仅数据库可查，见 §10 已知限制）。

---

## 6. 数据集、文件与迁移包

### 6.1 数据集

- 注册方式：上传文件、或登记服务器上已存在的目录；
- 登记路径必须在允许根内（`data/`、`output/`、`resources-private/data/`、
  `runtime/datasets/`、本人上传目录或 `SOTABAND_FILE_ALLOWED_ROOTS`），
  不能是资源根本身、不能与其他数据集目录重叠；
- 升级兼容：8.1 之前注册的数据集（owner 为机器身份 `local-xxxx`）会按
  "未认领历史条目"处理——本机有数据即可见；平台管理员可调用
  `POST /api/data/claim-local` 批量打上 UUID 归属；
- 删除：只允许删除位于安全数据集根内的本人数据集；对"允许根之外/无路径"的
  旧数据返回 **409**（防误删），需运维手工清理。

### 6.2 文件/工作区

- 「工作区间」可浏览/预览/下载允许根内的文件；上传按用户隔离
  （`UPLOAD_ROOT/<user_id>/`），单文件大小上限 `SOTABAND_UPLOAD_MAX_MB`（默认 512MB）；
- `storage/`、`.git`、`.env`、注册表/工作区元数据、迁移包导出目录等
  通过文件接口不可读、不可删；
- 上传文件名会自动净化（防路径穿越）。

### 6.3 迁移包（`.sotatool.zip` / `.sotaagent.zip`）

- 导出：资源详情页「导出」，导出者为包所有者（`exported_by`）；
- 导入：上传包导入，**导入后的资源 owner = 导入者**（不是原环境 owner）；
- 可见性：默认只能看到/下载自己导出的包；租户管理员可看全部；旧包（无
  `exported_by`）仅管理员可见；
- 覆盖导入：只有对同名资源有写权限时才允许 `overwrite`；
- 安全：包内路径已做 zip-slip 校验（拒绝绝对路径/盘符/`..`）。

---

## 7. 任务与长任务

- 提交训练/长任务的用户可在「任务监控」查看/取消**自己的**任务；
- 他人任务不可见、不可取消（404）；
- Agent 调用链产生的任务会记录发起用户（Web 服务式 Agent 通过签名身份头透传）。

---

## 8. 常见错误码与处理

| 状态码 | 典型原因 | 处理 |
|---|---|---|
| 400 | 参数/密码/路径格式不合法；数据集登记路径在允许根外；`.env` 值含非法字符 | 按提示修正输入 |
| 401 | 未登录或会话过期 | 重新登录 |
| 403 | 已登录但无权限（如非平台管理员改全局 LLM、越界文件路径） | 联系空间/平台管理员 |
| 404 | 资源不存在，**或存在但无权访问**（不泄露存在性） | 确认资源 id/归属，不要反复重试 |
| 409 | 冲突：最后一位 Owner 不可移除/降级/退出；暂停网络写操作；重复发布他人条目；删除路径不安全的数据集 | 换操作/先转移所有权/先恢复网络 |
| 410 | 邀请过期/已撤销/已接受 | 让管理员重新邀请 |
| 413 | 上传文件超过大小上限 | 调大 `SOTABAND_UPLOAD_MAX_MB` 或压缩 |
| 429 | 登录/注册/调用限流 | 等待后重试，或调大对应 env |
| 500 | 服务端异常（含审计/迁移失败信息） | 查看后端日志；若为迁移包问题核对格式 |

---

## 9. 运维配置速查（常用）

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | SQLite `storage/sotaband.db` | 生产可换 PostgreSQL（需 `pip install -e ".[postgres]"`） |
| `SOTABAND_PLATFORM_ADMINS` | 空 | 平台管理员 user UUID 列表；空=无 |
| `SOTABAND_WORKLOAD_SECRET` | 进程随机 | **生产必填**（≥32 字节）；否则重启后凭证失效 |
| `SOTABAND_COOKIE_SECURE` | 0 | https 生产必须 1 |
| `SOTABAND_CORS_ORIGINS` | 本地来源 | 显式来源列表 |
| `SOTABAND_FILE_ALLOWED_ROOTS` | 空 | 额外允许访问的目录（`os.pathsep` 分隔） |
| `SOTABAND_UPLOAD_MAX_MB` | 512 | 单文件上传上限 |
| `SOTABAND_LOGIN_*` / `SOTABAND_REGISTER_*` | 见 `.env.example` | 登录/注册限流 |
| `SOTABAND_SESSION_TTL_DAYS` | 30 | 会话有效期 |
| `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` | — | 安装级全局 LLM；仅平台管理员可在界面修改 |
| `SOTABAND_LLM_ALLOW_LOCAL` | 0 | 允许 LLM 连接测试访问本机（如 Ollama）时置 1 |
| `FORWARDED_ALLOW_IPS` | 127.0.0.1 | 反向代理部署时设为代理地址/子网，否则限流会把所有用户当同一 IP |

> 部署建议：默认单 worker（限流/网络不变量按单进程设计）；如需多 worker，
> 请改用 PostgreSQL，并评估限流与网络并发保护。

---

## 10. 已知限制与安全须知

1. **工具代码以服务器进程权限执行**：工具/Agent 生成并运行的代码拥有后端进程的
   文件与网络权限。多用户生产环境请隔离部署（独立 OS 用户/容器）、开启代码
   安全审查 enforce 模式，并限制资源与网络。
2. **交互对话历史仍按 session_id 存储**：已用浏览器级随机 session + (user, session)
   停止标志缓解，但强隔离依赖前端 session 唯一性；多用户共用一个安装时若需
   更强隔离，应在 Agent 侧把历史键改为 `(user_id, session_id)`（后续需求）。
3. **LLM 配置是安装级全局配置**：同一安装下所有租户共用；多租户共用安装时
   通过独立安装/独立 OS 用户隔离。
4. **文件读根包含项目根/runtime**：工作区间可浏览项目内文件（敏感目录已黑名单），
   多人部署如需更严格，可配置独立部署目录或后续收窄读根。
5. **登录限流是进程内实现**：单 worker 假设；多 worker 时限流阈值会按进程数放大。
6. **审计**：资源删除/代码变更等事件写租户维度，目前无租户级审计查询 API，
   需直接查库（`audit_event` 表）；网络审计有界面入口。

---

## 11. 常用 API 速查

| 分组 | 端点 |
|---|---|
| 认证 | `POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/session`、`POST /api/auth/logout`、`POST /api/auth/logout-all` |
| 我的 | `GET /api/v1/me`、`GET /api/v1/me/tenants`、`POST /api/v1/me/switch-tenant`、`POST /api/v1/me/password`、`GET/DELETE /api/v1/me/sessions…` |
| 租户 | `POST /api/v1/tenants`、`GET /api/v1/tenants/{id}/members`、`POST/GET/DELETE /api/v1/tenants/{id}/invitations…`、`POST /api/v1/tenant-invitations/{token}/accept` |
| 网络 | `POST/GET /api/v1/networks`、`GET/PATCH /api/v1/networks/{id}`、`GET /api/v1/networks/{id}/members`、`PATCH/DELETE /api/v1/networks/{id}/members/{uid}`、`POST /api/v1/networks/{id}/leave`、`POST/GET/DELETE /api/v1/networks/{id}/invitations…`、`POST /api/v1/network-invitations/{token}/accept`、`GET /api/v1/networks/{id}/audit` |
| 共享 | `POST /api/v1/{type}-releases/{id}/publications`、`DELETE /api/v1/publications/{id}`、`PATCH /api/v1/publications/{id}/invoke-policy`、`GET /api/v1/network-catalog`、`POST /api/v1/{type}-releases/{id}/installations`、`GET /api/v1/me/installations`、`DELETE /api/v1/installations/{id}`、`POST /api/v1/installations/{id}/credential`、`POST /api/v1/installations/{id}/invoke` |
| 资源 | `/api/tool/*`、`/api/agent/*`、`/api/data/*`、`/api/model/*`、`/api/file/*`（均需登录） |
| 迁移 | `/api/migrate/tool|agent/export`、`/import`、`/import-upload`、`/bundles` |
| 系统 | `GET /api/health`（公开）、`GET/POST /api/config/llm`（仅平台管理员）、`GET /api/llm/test-connection`（仅平台管理员） |

---

## 12. 管理员日常操作清单

**平台管理员**
- [ ] 修改全局 LLM（设置页）后，用「测试连接」验证；
- [ ] 新成员需要安装级权限时，把其 UUID 加入 `SOTABAND_PLATFORM_ADMINS` 并重启；
- [ ] 升级后如历史数据集不可见：确认「未认领」启发式生效，必要时 `claim-local` 归属；
- [ ] 定期检查 `audit_event` 表与登录异常（429/401 高频）。

**租户 Owner/Admin**
- [ ] 创建共享租户、邀请成员、按需设置 admin/member；
- [ ] 网络创建后按需开启 `allowMemberPublish` / `allowViewerInvoke`；
- [ ] 关注网络审计、及时撤销过期邀请、转移所有权后再移除旧 Owner；
- [ ] 复查资源 owner（只读态是否合理），避免把内置资产改坏（仅管理员可改）。

**普通成员**
- [ ] 个人资源在个人租户下默认私有；共享资产可读/用，改需 owner/管理员；
- [ ] 网络内按策略发布/安装/调用；凭证短期有效，勿写入日志；
- [ ] 退出网络前确认自己不是最后一位 Owner。

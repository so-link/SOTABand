# wxbit 全量 E2E 测试

> 目标：真实浏览器 + 真实后端 + 真实前端 + 确定性假 LLM，验证「接口返回的数据真的被页面消费」，并用 OpenAPI 覆盖台账防止新功能漏测。
>
> 当前基线：**2026-11-13，integration/wxbit**。

## 0. 当前基线

| 项 | 结果 |
|---|---|
| 浏览器用例 | **23 通过 / 0 失败 / 0 跳过** |
| 路由覆盖 | **95 / 95 应覆盖（100%）**；49 条显式排除（均有理由），未匹配请求 0 |
| 工具静态冒烟 | **53 / 53 通过** |
| 工具执行回归（fast） | **4 通过 / 0 失败**（按需，不阻断） |
| 生产锚点改动 | 22 处，全部为 `data-testid` |
| 耗时 | 浏览器约 15 秒；含播种/启动/门禁约 1 分钟 |

## 1. 快速开始

```powershell
cd C:\Users\squal\codebuddy-work\wxbit-integration

# 首次：创建 venv + 安装 dev/e2e 依赖 + Chromium
python -m venv venv
.\venv\Scripts\python.exe -m pip install -e ".[dev,e2e]"
.\venv\Scripts\python.exe -m playwright install chromium

# 全量（临时 SB-USER、假 LLM、后端、前端、pytest、覆盖门禁、工具冒烟）
.\venv\Scripts\python.exe scripts\e2e\run_e2e.py

# 按需工具执行回归
.\venv\Scripts\python.exe scripts\e2e\run_e2e.py --only tools --tool-profile fast
.\venv\Scripts\python.exe scripts\e2e\run_e2e.py --only tools --tools calculate-circle-area,calc-square

# 只跑匹配的浏览器用例（ASCII 关键词，避免 cmd 编码问题）
.\venv\Scripts\python.exe scripts\e2e\run_e2e.py --grep preview

# 失败现场保留
.\venv\Scripts\python.exe scripts\e2e\run_e2e.py --keep
```

`make test-e2e-full` 等价于第一条命令。

默认端口：后端 **8211**、前端 **5183**、假 LLM **8111**；被占用会直接报错，不会复用开发服务。

## 2. 隔离机制

每次运行创建 `.runtime/e2e/<run-id>/`：

- `SB-USER/`：临时 `SOTABAND_HOME`；先执行 `scripts/seed_system_resources.py`，把出厂资源播种到 `SB-USER/100/`（53 工具 / 3 Agent / 3 数据集）。
- `SB-USER/<uid>/uploads/e2e-fixture/`：确定性数据集（PNG / CSV / Markdown + spec_md），由 `prepare_fixtures.py` 注册为 `e2e-dataset`。
- `SB-USER/_platform/auth/users.json`：临时用户表。
- `logs/`：fake LLM、后端、前端、pytest、播种日志。

环境变量（覆盖 `.env`）：

```text
SOTABAND_HOME=<run>/SB-USER
SOTABAND_ROOT_USERNAME=root
SOTABAND_ROOT_PASSWORD=<run 随机>
SOTABAND_AUTH_SECRET=<run 随机>
SOTABAND_API_PORT=8211
SOTABAND_WEB_PORT=5183
LLM_PROVIDER=openai
LLM_API_KEY=e2e
LLM_BASE_URL=http://127.0.0.1:8111/v1
LLM_MODEL=e2e-model
```

认证流程：

1. root 登录；
2. `POST /api/auth/users` 创建普通用户 `e2e_<run-id>`；
3. 普通用户登录，大部分用例使用其 Bearer Token；
4. 浏览器把 token 写入 `localStorage["sotaband-token"]`；
5. 结束后 root 删除该用户，整个临时 `SB-USER` 随 run-dir 清理。

普通用户只写自己的 home，系统资源（uid=100）保持只读；管理页使用 root 身份。

## 3. 架构与复用

```text
run_e2e.py
├─ 临时 SB-USER + seed_system_resources.py
├─ fake_llm_server.py (8111, OpenAI 兼容, SSE/非流式)
├─ uvicorn app.main:app (8211)
├─ vite dev (5183)
├─ prepare_fixtures.py (root 建普通用户 + e2e-dataset)
├─ pytest tests/e2e -m "e2e and not e2e_live" --run-e2e
├─ route_inventory / classify_manifest / coverage_report
├─ tool_regression (53 静态 + 按需执行)
└─ summary.md / junit.xml / coverage.* / tools.* / screenshots
```

与旧 main 分支 E2E 的关系：

| 旧资产 | 处理 |
|---|---|
| `fake_llm_server.py` | 直接复用；规则改到 `tests/e2e/fixtures/llm-script.json` |
| 路由清单/覆盖门禁/分类脚本 | 复用算法，默认路径改到 `tests/e2e/`，环境改为 `SOTABAND_HOME` |
| 工具静态冒烟/执行骨架 | 复用；改为 Bearer Token + `/api/tool/list` |
| 编排/进程管理/报告骨架 | 复用结构，重写启动、播种、用户与 pytest 调用 |
| `consumption` / `guards` / `coverage` | 从 TypeScript 移植为 Python 辅助模块 |
| 旧多租户/协作网络/集市 specs | **不迁移**（能力已放弃） |
| 旧 seed/auth/global-setup | **不适用**（cookie/SQLAlchemy/邮箱注册 → users.json + Bearer + localStorage） |

## 4. 核心机制

### 4.1 接口 → DOM 对账

`tests/e2e/helpers/consumption.py`：

```python
expect_api_consumed(
    page,
    ConsumptionRule(
        api="/api/data/e2e-dataset/preview",
        fields=["files[].name"],
        scope='[data-testid="data-preview-files"]',
    ),
    action=row.click,
)
```

- 动作前挂 `page.expect_response`；
- 读取真实 JSON，按 `files[].name` 提取值；
- 在指定 scope 内查文本、`alt`、`title`、`src`；
- 未命中直接失败，并打印接口、字段、缺失值、页面文本摘要；
- 图片额外断言 `complete && naturalWidth > 0`。

### 4.2 静默失败守卫

`tests/e2e/helpers/guards.py` 自动挂载：

- `pageerror`；
- `console.error`（React DevTools/favicon/401 等显式 allow）；
- `/api/**` 4xx/5xx（未登录首屏的 401 显式 allow）；
- 图片 `requestfailed`（导航导致的 `ERR_ABORTED` 忽略）。

### 4.3 路由台账

- 唯一事实源：`app.openapi()` → `tests/e2e/route-inventory.json`（144 操作）。
- 台账：`tests/e2e/coverage-manifest.json`；模式 `ui-consume` / `ui-action` / `api` / `excluded`。
- 运行期记录：浏览器 `page.on("request")` + API 客户端 + 播种/夹具脚本。
- 门禁：存在 `uncovered` 或未匹配 `/api/**` 请求即失败。
- 新增路由：跑一次 → 门禁列出 → 补用例或加入 `scripts/e2e/classify_manifest.py` 的 `EXCLUDED_REASONS`（必须写理由）。

## 5. 用例清单

| 文件 | 覆盖 |
|---|---|
| `01_auth.py` | 登录框、错误密码、普通用户登录/退出、token 持久化、管理员入口不可见 |
| `02_resource_browser.py` | 数据/工具/模型/Agent 计数与条目来自真实接口；点击数据集打开预览 |
| `03_data_preview.py` | **核心回归**：文件清单/图片/CSV 表格/Markdown/spec 逐项对账 |
| `04_chat.py` | 假 LLM 流式标记渲染、新会话、停止按钮 |
| `05_tool_agent_detail.py` | 工具/Agent 名称、规范、代码、状态渲染 |
| `06_repository.py` | 工具仓库计数与搜索 |
| `07_settings_users_cron.py` | 管理员设置/用户列表、定时任务视图 |
| `08_files_tasks.py` | 上传 Markdown → 文件树 → 双击预览内容 |
| `09_routes_api_read.py` | 只读/分支路由：health、apis、migrate、llm config、task/task-jobs、agent 读/导出、data 读、file、tool 读、chat 平台分支、auth prefs/superuser/act-as-system |
| `10_routes_api_write.py` | 私有工具注册/保存/标签/表格/workspace/fork/删除；临时数据集注册/删除；文件上传/删除；临时用户管理；临时 cron |
| `test_chat_new_session_agent_start.py` | **live** 用例（真实 LLM/天气 Agent），默认排除 |

## 6. 工具回归

- 静态冒烟：全部 53 个工具，`/api/tool/list` + `/api/tool/{id}` 检查字段、code、spec_md。
- 执行回归：默认不跑；`--tools` / `--tool-profile fast` 触发，失败只进 `tools.md`，不改变主套件退出码。
- 当前 fast：`calculate-circle-area`、`calc-square`、`test-greeting`、`helloword`（4/4 通过）。
- 重工具（训练/下载/GPU/外网/模型权重）在清单中标 `excluded` 并写原因。

## 7. 生产代码锚点改动

结构化清单：`tests/e2e/production-changes.json`（22 处）。

| 文件 | 锚点/改动 |
|---|---|
| `layouts/MainLayout.tsx` | `main-layout` |
| `topbar/LoginDialog.tsx` | `login-dialog/username/password/submit/error` |
| `topbar/TopBar.tsx` | `topbar-login`、`user-menu`、`logout-button`、`open-settings`、`open-users` |
| `left-panel/LeftPanel.tsx` | `left-tab-files/resources` |
| `left-panel/ResourceBrowser.tsx` | `resource-browser`、分区/计数/资源行、`cron-jobs-entry` |
| `left-panel/WorkspaceFileTree.tsx` | `file-tree`、`file-node-{name}` |
| `center-panel/ChatView.tsx` | `chat-view`、`chat-new-session` |
| `center-panel/MessageList.tsx` | `chat-message-list` |
| `center-panel/MessageBubble.tsx` | `chat-message`（保留 data-role/data-msg） |
| `center-panel/ChatInput.tsx` | `chat-input/send/stop/confirm-policy` |
| `center-panel/DataPreviewView.tsx` | `data-preview/title/image/spec` |
| `right-panel/DatasetFilesPanel.tsx` | `data-preview-files/file-row` |
| `center-panel/TableView.tsx` | `data-table-preview` |
| `center-panel/ToolDetailView.tsx` | `tool-detail`、name、md/code toggle |
| `center-panel/AgentDetailView.tsx` | `agent-detail`、name、md/code toggle |
| `center-panel/RepositoryView.tsx` | `tool-repository`、count、search、card |
| `center-panel/DatasetRepositoryView.tsx` | `dataset-repository` |
| `center-panel/SettingsView.tsx` | `settings-view`（两种权限分支） |
| `center-panel/UsersView.tsx` | `users-view`（两种权限分支） |
| `center-panel/CronJobsView.tsx` | `cron-jobs-view` |
| `center-panel/TaskMonitorView.tsx` | `task-monitor-view`（外层无语义 div） |
| `center-panel/FilePreviewView.tsx` | `file-preview` |

约束：只新增 `data-testid`；唯一结构变化是 `TaskMonitorView` 多一层无语义包裹 div，不影响布局。未修改任何事件、请求、接口结构。

## 8. 故障注入自证

2026-11-13：

1. 临时把 `DataPreviewView` 的 `setPreviewFiles(dataset.id, d.files)` 改为空数组；
2. 运行 `run_e2e.py --grep preview_files`；
3. 用例按预期失败，输出：

```text
接口有数据但页面没有消费：GET http://127.0.0.1:5183/api/data/e2e-dataset/preview
字段：files[].name
未出现在页面上的值（3/3）：README.md、sample.csv、sample.png
页面可见文本摘要：E2E 数据集 正在读取文件列表…
```

4. 恢复代码后全量通过（23/23）。

## 9. 已知限制

- `tsc -b` 仍有 21 个既有类型错误，`npm run build` 会失败；E2E 默认使用 Vite dev，不修这些错误。
- `pytest -q -m unit` 在本机为 218 通过 / 1 失败：`test_symlink_escape_rejected` 需要 Windows 符号链接特权（`WinError 1314`），属于环境限制，与 E2E 改造无关。
- `test_chat_new_session_agent_start.py` 依赖真实 LLM/外网，默认排除；需真实环境时单独运行：
  `.\venv\Scripts\python.exe -m pytest tests/e2e/test_chat_new_session_agent_start.py -q --run-e2e -m e2e_live`。
- `TaskMonitorView` 是占位组件（16 行），只验证占位渲染，不验证 DAG。
- **数据预览请求失败时当前实现会静默显示空态**（`fetch(...).catch(() => setLoading(false))`），没有错误提示；这是产品侧可改进点，E2E 目前只验证成功路径，API 层可补 404 分支。
- 工具执行回归只覆盖 4 个低依赖 fast 工具；重工具需要独立 venv/数据/外网，登记为 excluded。
- 49 条路由显式排除，主要类别：Agent 生成/运行时控制、工具 LLM 生成/自动调试/装依赖、迁移包导入、模型注册、数据目录扫描/归属改写、全局 LLM 配置写、cron 真实触发、工具沙箱执行。每条原因见 `coverage-manifest.json`。
- 未验证 UI 视觉回归、跨浏览器、移动端。

## 10. 报告与产物

| 产物 | 位置 |
|---|---|
| 中文摘要 | `dist/e2e-report/summary.md` |
| pytest JUnit | `dist/e2e-report/junit.xml` |
| 路由覆盖 | `dist/e2e-report/coverage.md/json` |
| 工具报告 | `dist/e2e-report/tools.md/json` |
| 失败截图 | `dist/e2e-report/screenshots/` |
| 运行日志/假 LLM 流水 | `.runtime/e2e/<run-id>/logs/` |

成功运行自动删除 run-dir；失败或 `--keep` 保留现场。报告不写入密码、Token 或完整凭据。

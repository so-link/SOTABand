# SOTABand 定时任务模块设计方案

- 状态：已实现（`core/cron/` + `/api/cron` + 前端 `CronJobsView`）
- 日期：2026-11-02
- 触发场景：每日收盘后自动调起「ETF行情获取与数据集扩充」工具（`etf-quote-fetcher`，`mode=append`）增量写入行情数据集；推广到任意工具 / Agent 的周期执行

## 1. 目标与非目标

**目标**
1. 平台内建「定时任务」能力：按 cron 周期自动执行一个已注册的工具或 Agent，参数可配置。
2. 任务定义持久化，后端重启后自动恢复调度；执行历史可查询（成功/失败/输出摘要）。
3. 与四空间打通：任务绑定对象直接引用 `registry.json` 中的工具 ID / Agent ID，资源删除时给出失效提示。
4. 提供前端管理界面（列表、新建、暂停/恢复、立即触发、执行历史）。

**非目标**
- 不做分布式调度与多实例选主（单进程单调度器）
- 不做任务间依赖编排 / DAG
- 不做秒级精度（5 字段 cron，最小粒度 1 分钟）

## 2. 背景（立项时现状）

| 项 | 立项时现状 |
|---|---|
| 依赖 | `pyproject.toml` 无 apscheduler / croniter / celery，且部署环境 PyPI 不可达 |
| `core/scheduler/` | 仅 `__init__.py` 文档占位，无实现（本模块实现于新建的 `core/cron/`，不占用该包） |
| `SchedulerConfig`（`config/settings.py`） | 是算力/GPU 调度配置，与时间调度无关，**不可复用其语义**（建议新配置类改名避免混淆） |
| `docs/agent-types-design.md` | 已明确记录「任务式 Agent 定时触发（cron）—— 当前仅手动」，本方案即补齐该项 |
| 生命周期 | `app/main.py` 已用 `lifespan` asynccontextmanager，startup/shutdown 钩子现成 |
| 持久化惯例 | `core/observability/task_history.py` 与 `chat_store.py` 各持一份 `_db()`，同指向 `storage/platform.db`，WAL + `CREATE TABLE IF NOT EXISTS` + `check_same_thread=False` |

## 3. 总体架构

```
前端 CronJobsView ──HTTP──> app/api/routes/cron_routes.py  (/api/cron)
                                    │  CRUD
                                    ▼
                          core/cron/store.py   ── SQLite storage/platform.db
                                    │           表: cron_jobs / cron_runs
                                    ▼
                          core/cron/scheduler.py  (CronScheduler，零依赖)
                                    │ 到点触发 runner.fire(job, trigger)
                    ┌───────────────┴────────────────┐
                    ▼  kind=tool                     ▼  kind=agent
        ToolExecutor.execute(tool_id, params)   agent_factory.execute_stream(...)
        （子进程沙箱，异步）                     （需常驻进程；未运行则先 start）
                    └───────────────┬────────────────┘
                                    ▼
                          cron_store.run_start / run_finish
```

新增目录与文件：

| 文件 | 职责 |
|---|---|
| `core/cron/__init__.py` | 导出 `cron_scheduler` 单例 |
| `core/cron/cron_parse.py` | 零依赖自研五段 cron 解析（`CronSpec`：`next_after` 下次触发时间计算） |
| `core/cron/store.py` | `cron_jobs` / `cron_runs` 读写，沿用 task_history 的 `_db()` 写法 |
| `core/cron/scheduler.py` | `CronScheduler`：每个启用任务一个 asyncio 循环（算下次 → sleep → 防重叠 → 触发 → 重算）；start / shutdown / upsert / remove / trigger_now |
| `core/cron/runner.py` | `fire()`：身份注入、超时、结果落库、异常兜底；`check_target` / `validate_job_payload` 供路由校验 |
| `app/api/routes/cron_routes.py` | REST + 立即触发 + 历史查询 |
| `frontend/src/components/center-panel/CronJobsView.tsx` | 管理页 |
| `frontend/src/services/api/cron.ts` | 参照 `services/api/task.ts` 裸 fetch 写法 |

## 4. 数据模型

沿用 `storage/platform.db`（不新建库文件，与 task_history/chat_store 同库不同表）。

```sql
CREATE TABLE IF NOT EXISTS cron_jobs (
    id            TEXT PRIMARY KEY,        -- cron_<ts>_<rand>
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- tool | agent
    target_id     TEXT NOT NULL,           -- registry.json 中的 tool_id / agent_id
    params_json   TEXT NOT NULL DEFAULT '{}',
    cron          TEXT NOT NULL,           -- 5 字段 unix cron
    timezone      TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    enabled       INTEGER NOT NULL DEFAULT 1,
    owner         TEXT NOT NULL,
    timeout       INTEGER NOT NULL DEFAULT 600,   -- 秒
    misfire       TEXT NOT NULL DEFAULT 'skip',   -- skip | fire_once
    next_run_at   REAL,                           -- epoch 秒（时间戳统一用 REAL）
    last_run_at   REAL,
    last_status   TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cron_jobs_owner ON cron_jobs(owner, enabled);

CREATE TABLE IF NOT EXISTS cron_runs (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    started_at   REAL NOT NULL,
    finished_at  REAL,
    status       TEXT NOT NULL DEFAULT 'running',  -- running|success|failed|timeout|skipped
    trigger      TEXT NOT NULL DEFAULT 'schedule', -- schedule|manual
    message      TEXT DEFAULT '',          -- 工具返回的 message / Agent 末条文本
    output_json  TEXT DEFAULT '',          -- 截断后的 data（≤20000 字符），避免库膨胀
    error        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cron_runs_job ON cron_runs(job_id, started_at DESC);
```

保留策略：`cron_runs` 每任务最多留 200 条，写入后 `DELETE` 超出部分。

## 5. API 设计（`/api/cron`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/cron/jobs` | 当前用户任务列表（含 `next_run_at`、`last_status` 及本地时间串） |
| POST | `/api/cron/jobs` | 新建。校验：cron 合法、kind 对应资源存在且 `status=active` |
| GET | `/api/cron/jobs/{id}` | 单任务详情 |
| PUT | `/api/cron/jobs/{id}` | 修改（含 enabled）。改后 `cron_scheduler.upsert` 重新调度 |
| DELETE | `/api/cron/jobs/{id}` | 删除任务，保留历史 |
| POST | `/api/cron/jobs/{id}/toggle` | 启用/禁用切换（启用时重算 `next_run_at`） |
| POST | `/api/cron/jobs/{id}/trigger` | 立即执行一次，`trigger=manual`，与调度触发共用 `runner.fire` |
| GET | `/api/cron/jobs/{id}/runs?limit=20` | 执行历史 |
| GET | `/api/cron/runs/recent?limit=30` | 全局最近动态 |
| GET | `/api/cron/options` | 前端表单候选项（active 工具 / Agent 下拉 + 常用时区） |

响应沿用平台风格（`{"status": "...", ...}`），不引入新包裹层。

## 6. 关键设计决策

### 6.1 cron 解析依赖：零第三方依赖，自研解析器
- **方案**：`core/cron/cron_parse.py` 自研五段 cron 解析（支持 `*`、步进、范围、列表），配合 `CronScheduler` 的每任务 asyncio 循环。
- 理由：部署环境 PyPI 不可达，任何新增运行时依赖（apscheduler 及其传递依赖 tzlocal、croniter 等）都无法安装。所需语义均在百行内可自持：
  - `max_instances=1`：触发前检查该任务上一轮循环是否仍在执行，是则本轮记 `skipped`；
  - `coalesce`：sleep 醒来若已错过多个周期，只补一次；
  - misfire：启动恢复时 `next_run_at` 已过期，`skip` 直接重算未来、`fire_once` 先补跑一次；
  - 时区：用标准库 `zoneinfo`。
- 代价：解析器需自带测试覆盖边界（`0 17 * * 1-5`、步进、跨月等）。

### 6.2 后台任务的身份（最容易踩的坑）
`auth_identity_middleware`（`app/main.py:149-192`）通过 contextvar `set_current_user` 设置当前用户；而 `ToolRegistry` / `DataRegistry.register` / `ToolExecutor` 都依赖 `get_current_user_id()` 做归属与可见性判断（如 `data_registry.py` 中 `owner` 强制取当前用户）。定时触发发生在后台任务中，**无 HTTP 请求上下文，contextvar 为空**。

处理：`cron/runner.py` 触发前显式
```python
from core.auth.session import AuthUser, set_current_user, reset_current_user
token = set_current_user(AuthUser(id=owner, username="", role="user"))
try:
    ...  # 执行
finally:
    reset_current_user(token)
```
`fire()` 内为每次执行单独设置，避免污染其他协程。

### 6.3 Agent 触发必须先启动进程
`agent_factory.execute_stream` 在 `self._processes` 无进程时直接 yield `Agent 'x' 未运行`，**无冷启动**。因此 `kind=agent` 的 `runner._run_agent` 顺序为：

1. `agent_factory._processes` 无该 id → `await agent_factory.start(agent_id)` 并置 `started_here = True`
2. `async for ev in agent_factory.execute_stream(agent_id, params=..., timeout=...)` 消费到 `done`/`error`（该方法是 AsyncGenerator，事件形如 `{"event": "...", "data": {...}}`）
3. 若该 Agent 是本任务临时拉起的（`started_here`），执行完 `await agent_factory.stop(agent_id)` 释放资源；否则保持运行
4. 摘要取末条 `text`/`card` 文本写入 `cron_runs.message`

**建议**：优先绑定工具。工具走 `ToolExecutor.execute`（类方法、async、独立子进程沙箱、返回 `{"status": "success"|"failed", ...}`），无常驻进程与锁问题，最适合无人值守。

### 6.4 重叠与错过
- `max_instances=1` + `coalesce=True`：上一轮未跑完则本轮标记 `cron_runs.status='skipped'`（写记录，便于发现任务超时）。
- 服务停止期间错过的触发：`misfire='skip'`（默认）跳过；`'fire_once'` 在启动后补跑一次。ETF 采集建议 `skip`（每日任务补跑一次即可，`mode=append` 幂等，重复执行无副作用）。
- 单次执行 `timeout`（默认 600s）用 `asyncio.wait_for` 包裹；工具子进程本身有 `ToolExecutor` 超时。

### 6.5 启动恢复与失败隔离
`lifespan` startup 中：`store.init()` → `await cron_scheduler.start()`（从 store 载入 `enabled=1` 的任务，各起一个 asyncio 调度循环）。单任务 cron 表达式非法或载入异常时**只标记该任务并跳过**，不得让整个后端启动失败。shutdown 中 `await cron_scheduler.shutdown()`（取消各任务循环），放在 Agent 进程回收之前。

### 6.6 资源删除联动
不做强外键。新建/修改时经 `runner.check_target` 校验目标存在且 active；列表接口不批量回查目标存活状态，目标被删除后前端按 `target_id` 原样显示（触发时执行失败并记入 `cron_runs.error`）。

## 7. 前端改动清单（项目无 react-router，全靠 zustand `activeView`）

| 文件 | 改动 |
|---|---|
| `frontend/src/stores/ui-store.ts` | `ActiveView` 联合类型增加 `'cron-jobs'` |
| `frontend/src/components/center-panel/CenterPanel.tsx` | `TAB_CONFIG` 增加 label「定时任务」+ icon；`ViewRouter` 增加 `case 'cron-jobs'` |
| `frontend/src/components/left-panel/ResourceBrowser.tsx` | 增加导航项 + 点击 `setActiveView('cron-jobs')` |
| 新建 `center-panel/CronJobsView.tsx` | 列表卡片（名称/目标/cron/下次执行/末次状态/开关）+ 新建弹层 + 历史抽屉 |
| 新建 `services/api/cron.ts` | 参照 `services/api/task.ts` |

新建表单的目标选择：kind 下拉（tool/agent）→ 二级下拉列出 `/api/cron/options` 返回的 active 资源 → 选定目标后拉取其 spec，按「输入规范」表自动生成参数表单字段（spec 拉不到时退化为无参数表单）。

## 8. 分期实施

| 阶段 | 范围 | 交付判据 |
|---|---|---|
| P1 | `cron_jobs`/`cron_runs` store + `core/cron/scheduler.py` + `runner.py`（仅 kind=tool）+ `/api/cron` CRUD/trigger/runs + lifespan 接入 | 用 curl 建任务，到点自动执行 `etf-quote-fetcher`，`cron_runs` 出现 success 记录；重启后端任务自动恢复 |
| P2 | 前端 `CronJobsView` + 导航接入 | 浏览器可新建/暂停/立即触发/看历史 |
| P3 | kind=agent（start→execute→可选 stop）+ 按工具 spec 生成参数表单 + 目标失效标红 | Agent 定时任务可跑通并在历史中看到输出摘要 |

## 9. 风险与开放问题

1. **`SchedulerConfig` 命名冲突**：建议新配置类命名为 `CronJobConfig`（`enabled` 总开关、默认时区、默认 timeout、历史保留条数），避免与现有算力调度语义混淆。当前默认值直接内联在 `store.py`/路由中，尚未抽出配置类。
2. **多进程部署**：若后端改为多 worker（当前 uvicorn 单进程），需改为文件锁或独立调度进程，否则每个 worker 都会触发一次。当前单进程无此问题，`scheduler.py` 头部注释已显式记录该前提。
3. **输出体积**：工具 `data.rows` 可能很大，`cron_runs.output_json` 必须截断（现实现为 20000 字符）并只存摘要，否则历史表快速膨胀。
4. **交易日历**：cron 只能过滤周末，无法跳过节假日。非交易日执行 `mode=append` 会返回「无新数据」，幂等无副作用，可接受；如需精确，后续加交易日历判断（工具侧或 runner 前置条件）。

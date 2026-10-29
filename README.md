 <p align="center">
  <img src="frontend/public/image.png" alt="SOTABand Logo" width="120" />
</p>

# SOTABand （优智联邦）

> 多智能体驱动的多模态智能处理引擎
>
> 探索即扩展，描述即执行，协同即智能。


## 核心理念--

构建一个多智能体驱动的多模态智能处理引擎，实现对开放世界多源异构数据的智能处理流程的自动化构建与动态扩展，支持在分布式异构算力集群上完成多智能体的协同工作。

## 系统分层

```
应用层 (app/)         ← 探索式交互界面、工作区间、AI助手、任务编排、可视化
  ↕
核心层 (core/)        ← 唯一具有执行能力的层次：工作流引擎、资源管理、调度、协作、安全、可观测
  ↕
资源层 (resources/)   ← 六空间：数据/工具/模型/智能体/用户/任务
  ↕
存储层 (storage/)     ← 持久化、缓存、文件存储
```

## 项目结构

```
sotaband-engine/
├── app/                    应用层 — 用户交互界面
│   ├── api/                REST/GraphQL API 接口
│   ├── ui/                 Web 前端页面 & 组件
│   └── workspace/          工作区间管理（数据选择、探索历史）
│
├── core/                   核心层 — 系统大脑与执行引擎
│   ├── engine/             工作流引擎子系统
│   ├── resource/           资源生命周期管理子系统
│   ├── scheduler/          调度与算力管理子系统
│   ├── agent/              多智能体协作子系统
│   ├── security/           安全与治理子系统
│   └── observability/      可观测性子系统
│
├── resources/              资源层 — 六空间统一建模
│   ├── data/               数据空间
│   ├── tools/              工具空间
│   ├── models/             模型空间
│   ├── agents/             智能体空间
│   ├── users/              用户空间
│   └── tasks/              任务空间
│
├── storage/                存储层 — 持久化 & 缓存
├── config/                 全局配置
├── docs/                   设计文档
└── tests/                  测试
```

## 核心设计决策

1. **探索式交互** — 用户在工作区间选数据、发需求，交互Agent引导全流程
2. **工具自动生成** — 无工具时自动编写代码，用户核验后注册入库
3. **简化/复杂双模式** — 简单需求直接调用工具；复杂需求自动编排多Agent协同
4. **AI模型作为特殊工具** — 模型空间 + 工具空间双重建模，双向关联
5. **资源全生命周期管理** — 所有资源标准化描述、注册登记、版本管理

## 快速开始

### 环境要求

- **Python >= 3.11**（推荐 3.12）
- **Node.js >= 18**
- **macOS / Linux / Windows** 全平台支持（Windows 用 `python` 命令代替 `python3`）

### 一键启动

```bash
# 1. 创建 .env 并填入 LLM 配置（首次）
cp .env.example .env      # Windows: copy .env.example .env
# 编辑 .env，填写 LLM_PROVIDER 及对应 API Key

# 2. 一条命令完成安装并启动（自动建 venv、装全部依赖、拉起前后端）
python scripts/serve.py dev        # 开发模式：前端热更新
python scripts/serve.py start      # 部署模式：前端静态构建，暴露面更小，适合长期运行
```

`serve.py` 是幂等的：环境已装好的部分自动跳过，重复执行无副作用；Ctrl+C 一次停止前后端。Windows / macOS / Linux 命令相同。

常用变体：

```bash
python scripts/serve.py setup                  # 只装环境，不启动
python scripts/serve.py start --skip-tools     # 跳过工具大包依赖（torch 等），加快启动
python scripts/serve.py start --rebuild-frontend  # 改了前端代码后强制重建
python scripts/serve.py stop                   # 停止 serve.py 启动的服务
```

<details>
<summary>国内网络访问 PyPI 慢或超时？</summary>

给 venv 配置 pip 镜像源（只影响本项目 venv，不动全局配置）：

```bash
python scripts/serve.py dev --mirror https://pypi.tuna.tsinghua.edu.cn/simple
```

</details>

### 端口与监听地址配置

全部在 `.env` 中配置，前后端共同读取（无需改任何代码/配置文件）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SOTABAND_API_HOST` | `0.0.0.0` | 后端监听地址。配反向代理/隧道时建议 `127.0.0.1` |
| `SOTABAND_API_PORT` | `8001` | 后端端口 |
| `SOTABAND_WEB_PORT` | `5173` | 前端端口（dev 与 preview 共用） |

前端 `/api` 请求自动代理到 `127.0.0.1:<SOTABAND_API_PORT>`，改端口后重启即生效。端口被占用时 `serve.py` 会启动前预检并给出明确提示。

### 通过反向代理对外提供服务

后端只监听 `127.0.0.1` 时，用 nginx 等反代 + 域名访问前端。若前端使用 `serve.py dev`（Vite dev server），需确保 Vite 允许该域名：当前 `vite.config.ts` 已设 `allowedHosts: true`；生产建议改为白名单数组（如 `['sotaband.example.com']`）或改用 `serve.py start`。nginx 需为 `/api` 和 WebSocket 透传 `Upgrade`/`Connection` 头。

### 进阶（可选）

- **工具依赖单独检查**：`serve.py` 启动时已自动检查并安装工具依赖（扫描 `resources/tools/implementations/` 下各工具的外部依赖）。如需单独排查，运行 `python scripts/check_tool_env.py --dry-run`。
- **工具独立环境（uv）**：安装了 [uv](https://docs.astral.sh/uv/)（`pip install uv`）后，工具装依赖时会自动获得各自独立的 `.venv`（依赖清单固化工具目录下的 `requirements.txt`，uv 全局缓存硬链接共享，几乎不占额外磁盘），互不冲突；未安装 uv 时回退为装入共享项目 venv 的旧行为。换机器或误删 `.venv` 后，启动检查会按 `requirements.txt` 秒级重建。
- **绕过 serve.py 手动启动**：`venv/bin/python -m uvicorn app.main:app --host <API_HOST> --port <API_PORT>` + `cd frontend && npm run dev|preview`（需自行保证端口与 `.env` 一致）。

### 常见问题

| 症状 | 原因与处理 |
|---|---|
| 安装阶段报 `Network is unreachable` / 超时 | 直连 PyPI 不通，加 `--mirror https://pypi.tuna.tsinghua.edu.cn/simple` 重跑 |
| `Blocked request. This host (...) is not allowed` | dev 模式下 Vite Host 校验拦截，见上文「反向代理」一节 |
| 改了 `.env` 端口不生效 | 确认改的是仓库根目录 `.env`，并重启 `serve.py`（手动启动则不受 `.env` 控制） |
| 改了前端代码页面不变（`start` 模式） | preview 伺服的是构建产物，重跑 `serve.py start --rebuild-frontend`（日常开发用 `dev` 模式自带热更新） |
| macOS 提示 Python 版本过低 | `brew install python@3.12` 后用 `python3.12 scripts/serve.py dev` |



## 开发状态

🚧 初具规模

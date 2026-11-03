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

- **Python >= 3.12**（推荐 3.12）
- **Node.js >= 18**
- **macOS / Linux / Windows**（Windows 推荐使用 WSL2）

### 安装与启动

```bash
# 1. 创建 .env 并填入 API Key
cp .env.example .env
# 编辑 .env，填写 DEEPSEEK_API_KEY

# 2. 创建虚拟环境并安装 Python 依赖
python3.12 -m venv venv

# macOS / Linux
source venv/bin/activate
# Windows
venv\Scripts\activate

pip install -e ".[dev]"

# 3. 检查并修复工具运行环境（自动安装所有工具所需的依赖）
python scripts/check_tool_env.py --auto

# 4. 初始化 / 升级数据库（创建 13 张业务表；命令幂等，可反复执行）
#    中文 Windows 需先执行 $env:PYTHONUTF8=1，否则读文件会按 GBK 报错
python -m alembic upgrade head

# 5. 启动后端（端口 8001）
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 6. 新终端，激活 venv 后安装前端依赖并启动
cd frontend && npm install && npm run dev
```

> **注意**：macOS 用户如果系统自带 Python 3.6，需使用 Homebrew 安装 Python 3.12：
> ```bash
> brew install python@3.12
> ```
> 然后用 `/opt/homebrew/bin/python3.12` 替代 `python3.12`。

### 工具环境检查脚本

`scripts/check_tool_env.py` 用于自动检查所有工具的运行环境，确保依赖库完整。

```bash
# 仅检查，不安装
python scripts/check_tool_env.py --dry-run

# 交互模式，确认后安装缺失的包
python scripts/check_tool_env.py

# 自动安装所有缺失包，无需确认
python scripts/check_tool_env.py --auto
```

脚本会扫描 `resources/tools/implementations/` 下所有工具的 `tool.py`，提取外部依赖，检查安装状态，并自动安装缺失的 Python 包。

### 数据库迁移与部署

后端使用 Alembic 管理数据库结构（SQLite / PostgreSQL 通用）。连接串来自
`config.settings.storage.database_url`，可用环境变量 `DATABASE_URL` 覆盖
（`migrations/env.py` 里注入，不在 `alembic.ini` 里配置，避免「应用连 A 库、
迁移改 B 库」）。

| 命令 | 作用 |
|---|---|
| `python -m alembic upgrade head` | 创建 / 升级到最新版本（13 张业务表，幂等） |
| `python -m alembic current` | 查看当前迁移版本 |
| `python -m alembic check` | 检查模型与迁移是否一致（无漂移） |

> ⚠️ **首次部署或每次升级后必须执行 `upgrade head`**，否则调用接口会报「表不存在」。
> 后端进程本身**不会**自动建表（仅测试环境用 `create_all` 代建）。

#### Docker 部署（含 PostgreSQL，推荐）

```powershell
cd E:\Learn\SOTABand
docker compose up -d --build      # 后端容器启动时自动执行 alembic upgrade head
docker compose ps                 # db / backend / frontend 均应为 running/healthy
```

打开 http://localhost:5173。Docker 模式下迁移由容器入口脚本自动完成，无需手动执行；
上面的本机 `uvicorn`（SQLite）方式则必须手动 `python -m alembic upgrade head`。

## 开发状态

🚧 初具规模

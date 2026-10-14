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

python -m pip install -e ".[dev]"

# 3. 可选：检查已有工具的 Python 依赖（首次拉取没有工具时会正常跳过）
python scripts/check_tool_env.py --auto

# 4. 启动后端（端口 8001）
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 5. 新终端，激活 venv 后安装前端依赖并启动
cd frontend && npm install && npm run dev
```

> **注意**：macOS 用户如果系统自带 Python 3.6，需使用 Homebrew 安装 Python 3.12：
> ```bash
> brew install python@3.12
> ```
> 然后用 `/opt/homebrew/bin/python3.12` 替代 `python3.12`。

### 工具环境检查脚本

`scripts/check_tool_env.py` 检查本机已有工具的 Python 依赖。平台自身的依赖由上面的 `python -m pip install -e ".[dev]"` 安装；首次拉取没有工具时，这一步可跳过，执行也会正常结束。

```bash
# 仅检查，不安装
python scripts/check_tool_env.py --dry-run

# 交互模式，确认后安装缺失的包
python scripts/check_tool_env.py

# 自动安装所有缺失包，无需确认
python scripts/check_tool_env.py --auto
```

脚本与后端共用资源目录配置：读取项目内 `resources/tools/implementations/` 和私有目录下的 `tools/implementations/`。私有目录默认是项目父目录中的 `resources-private/`，可在 `.env` 或环境变量中设置 `SOTABAND_PRIVATE_RESOURCES`。同 ID 工具采用私有实现，新增工具仍写入私有目录。检查不会为了消除提示而创建空资源目录。

每个工具使用与实际执行相同的解释器：工具自己的 `.venv` → 项目的 `venv` / `.venv` → 当前 Python。Windows 识别 `Scripts/python.exe`，macOS/Linux 识别 `bin/python`。脚本显示工具路径和解释器，按环境检查依赖，使用该解释器的 `-m pip` 安装，并在安装后再次验证导入。它不会扫描整台电脑的 Python 环境。

没有工具、依赖已就绪或修复成功时退出码为 `0`；语法错误、导入错误、安装失败、仍有未安装依赖时为 `1`（包括 `--dry-run` 发现缺失依赖）。脚本根据静态 `import` 检查 Python 包；动态导入、外部程序、模型权重、数据文件和 API Key 仍需按对应工具说明配置。

### 系统默认对话入口

默认交互 Agent 的代码随核心模块分发，位于 `core/agent/implementations/interactive_agent/`，由 `AgentFactory` 启动独立进程。它不依赖个人 `resources/agents/` 文件，也不通过大模型临时生成自身实现。对话仍需先配置有效的 LLM API Key；用户工具与自定义 Agent 仍通过资源注册、构建流程保存到私有目录。

后端只有收到 Agent 加载完成的确认才显示“已就绪”；启动失败时，日志和对话接口会提供具体原因。更新代码后需重启后端以加载新实现。



## 开发状态

🚧 初具规模

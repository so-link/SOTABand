# 运行环境与安装

首次使用的完整安装、API 配置、前后端启动及三个界面示例见[从零操作指南](hypoxemia-operation-guide.md)。本页补充依赖和交付边界，实际验证记录统一见 [pr-readiness.md](pr-readiness.md)。

低氧计算源码和模型实现随仓库提供。安装不需要外部研究项目、历史任务目录或预训练权重。需要低氧功能时，平台与计算进程使用两个受管理的虚拟环境；两个环境均固定 Python 3.12.11。

## 只安装平台，不使用低氧预测

下面沿用上游 SOTABand 的“创建虚拟环境 → 安装项目 → 配置 `.env` → 启动后端与前端”顺序，命令针对本审查分支的 Windows PowerShell 和端口配置调整。只使用平台基础功能，无须安装 PyTorch、CUDA、`.venv-hypoxemia` 或运行 `scripts/install_hypoxemia.ps1`。

电脑需预先安装 Git、Python 3.12、Node.js 22.12+（附带 npm）及浏览器。在仓库根目录打开 PowerShell，确认当前目录包含 `pyproject.toml` 和 `frontend`，然后执行：

```powershell
py -3.12 --version
node --version
npm --version
py -3.12 -m venv .venv-platform
.\.venv-platform\Scripts\python.exe -m pip install -e ".[dev]"
```

这与上游 README 的 `python3.12 -m venv venv`、`pip install -e ".[dev]"` 用途相同：安装平台及开发测试依赖。这里直接调用环境内的 Python，不要求激活环境；环境名改为 `.venv-platform`，便于日后按下文添加低氧计算环境。`pip install -e ".[dev]"` 根据 `pyproject.toml` 解析允许的版本。

若电脑尚未安装 Python 3.12，或需要本次审查所用的确定版本，可跳过上面的 `py -3.12 -m venv` 与 `pip install`，改在新检出目录用任一可运行 `py` 的 Python 执行以下命令。它只安装平台依赖锁文件，不安装低氧计算环境：

```powershell
py -m pip install uv==0.8.22
$uvProgram = Join-Path (py -c "import sysconfig; print(sysconfig.get_path('scripts'))") 'uv.exe'
& $uvProgram python install 3.12.11
& $uvProgram venv --python 3.12.11 .venv-platform
& $uvProgram pip sync --python .venv-platform/Scripts/python.exe requirements/platform.lock
.\.venv-platform\Scripts\python.exe --version
```

两条安装路径择一使用；锁文件路径固定 Python 3.12.11 和平台依赖版本，且不包含上游 `.[dev]` 的测试工具。需要运行单元测试时再安装开发或验收依赖，不影响正常启动平台。

上游 README 的 `python scripts/check_tool_env.py --auto` 会扫描并尝试安装**所有工具**的额外依赖；只使用平台时不执行它。需要了解某个工具缺少什么，可先运行 `.\.venv-platform\Scripts\python.exe scripts/check_tool_env.py --dry-run`，再按该工具说明单独安装。低氧功能需按下一节安装独立计算环境。

在本机配置对话服务的 API Key，已有 `.env` 时不要覆盖：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
```

参照 `.env.example` 设置 `LLM_PROVIDER` 和自己的 `LLM_API_KEY`，必要时设置 `LLM_BASE_URL`、`LLM_MODEL`。不要提交 `.env`，也不要把 Key 输入聊天框。后端启动前，在仓库根目录安装前端依赖：

```powershell
Push-Location frontend
npm ci
Pop-Location
```

分别在两个 PowerShell 终端启动服务，并保持终端打开：

```powershell
# 终端 A：仓库根目录
.\.venv-platform\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

```powershell
# 终端 B：仓库根目录
Set-Location frontend
$env:SOTABAND_API_PORT = '8002'
npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

浏览器打开 `http://127.0.0.1:5174`。访问 `http://127.0.0.1:8002/api/health` 和 `http://127.0.0.1:5174/api/health` 应均返回 `{"status":"healthy"}`。后端使用 `8002`、前端使用 `5174`，因为本分支的前端代理默认连接 `8002`；上游 README 的 `8001` 命令不能直接与这里的前端代理混用。正常关闭时在两个终端分别按 `Ctrl+C`。平台独立安装后，低氧数据处理、训练和评测仍需要 `.venv-hypoxemia`；仅运行平台时不用准备它。

## Windows CPU 安装

需要 Git 和可联网的 Python（仅用于安装 uv）；仓库不包含虚拟环境。取得仓库后在仓库根目录执行：

```powershell
py -m pip install uv==0.8.22
powershell -ExecutionPolicy Bypass -File scripts/install_hypoxemia.ps1
```

若 `uv` 不在 PATH，使用 `-Uv` 指定安装得到的 `uv.exe`。安装器准备 Python 3.12.11、`.venv-platform`、`.venv-hypoxemia`，根据锁文件安装依赖。缓存和安装临时文件默认使用仓库 `.runtime`。已有同名环境不会自动删除；应在新的检出目录安装以获得干净环境。

启动后端：

```powershell
.venv-platform/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

平台基础依赖在 `pyproject.toml` 声明，经过本次安装解析的确定版本在 `requirements/platform.lock`。使用验收脚本时给安装器加 `-WithVerification`，它使用 `platform-verification.lock`，额外安装 pytest、HTTP 测试客户端、合成数据及 Excel 检查依赖。生产平台不需要因此引入 PyTorch。

## 计算依赖

`requirements/hypoxemia-cpu.in` 是直接依赖声明，`hypoxemia-cpu.lock` 固定全部传递依赖。核心版本为：

| 依赖 | 版本 |
| --- | --- |
| Python | 3.12.11 |
| PyTorch CPU | 2.5.1+cpu |
| NumPy | 2.1.3 |
| pandas | 2.2.3 |
| scikit-learn | 1.5.2 |
| SciPy | 1.14.1 |
| einops | 0.8.1 |
| reformer-pytorch | 1.4.4 |
| PyWavelets | 1.8.0 |
| matplotlib | 3.9.2 |

不能用外部研究项目的旧 requirements 代替上述清单。PyTorch CPU wheel 从 `https://download.pytorch.org/whl/cpu` 获取，其余包使用 PyPI；锁文件明确索引与版本。

## GPU 可选配置

CPU 是默认交付及验收目标。普通 PyTorch 模型可另建 CUDA 环境；不要覆盖已验证的 CPU 环境。可使用 PyTorch 2.5.1 的 CUDA 12.1 wheel（`torch==2.5.1+cu121`，索引 `https://download.pytorch.org/whl/cu121`），保留公共计算依赖版本，再显式配置 `SOTABAND_HYPOXEMIA_PYTHON` 指向该解释器。驱动由部署机器提供。

Windows GPU 可选安装命令（尚未做硬件验收）：

```powershell
uv venv --python 3.12.11 .venv-hypoxemia-cuda
uv pip sync --python .venv-hypoxemia-cuda/Scripts/python.exe requirements/hypoxemia-cuda121.lock --index-strategy unsafe-best-match
$env:SOTABAND_HYPOXEMIA_PYTHON = (Resolve-Path .venv-hypoxemia-cuda/Scripts/python.exe).Path
```

该文件固定 Windows 公共依赖和 CUDA wheel。Linux 的 NVIDIA 运行库依赖及 Mamba 构建需要另行锁定和验收，不能把 Windows 锁文件当作已验证的 Linux 安装方案。

`MambaSingleLayer` 和 `SegRNNMambaFusion` 额外依赖 mamba-ssm/causal-conv1d CUDA 扩展，默认不可用；Linux CUDA 构建候选版本为 `mamba-ssm==2.2.4`、`causal-conv1d==1.5.0.post8`。这些扩展不属于已验收 Windows CPU 安装，也不承诺原生 Windows 可安装。管理员完成相应模型训练/重载验证后，才可设置 `SOTABAND_HYPOXEMIA_ENABLE_MAMBA=1`。实际验证范围以验收记录为准。

## 前端与本地配置

Node.js 仅用于前端开发与构建。Vite 8 要求 Node.js 20.19+ 或 22.12+：

```powershell
cd frontend
npm ci
npm run build
npm run dev
```

开发页面默认端口 5174，代理后端 8002；端口可由 `SOTABAND_FRONTEND_PORT` / `SOTABAND_API_PORT` 调整。使用预构建前端的最终使用者无需安装 Node.js。当前仓库交付的是源码安装流程，不是 Windows EXE 安装包。

LLM API Key 由使用者在本机配置 `.env`。离线合成验收不使用 LLM；`SOTABAND_LOAD_DOTENV=0` 可阻止验收读取本机 `.env`。不要复制开发者的 `.env`。用户选择外部低氧数据目录时不需要额外设置受信任数据根或重启后端。

旧 `SPO2_HYPOXEMIA_PROJECT`、`SPO2_HYPOXEMIA_PYTHON`、`SPO2_HYPOXEMIA_PIPELINE_SCRIPT` 已退出运行路径，新安装不需要它们。计算进程默认由 `.venv-hypoxemia` 启动；只有管理员显式配置新的解释器覆盖变量才会使用其他环境。

运行数据与权重必须备份在可配置运行目录中，不提交到 Git。更新仓库不会自动迁入历史任务或外部项目中的旧模型。

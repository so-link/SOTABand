# 内置低氧功能测试记录

本文只记录测试环境、测试命令、实际结果和未覆盖范围。运行数据、患者数据、模型权重和日志均保存在被 Git 忽略的 `.runtime/` 目录，不属于交付内容。

## 测试环境

- Windows，Python 3.12.11，平台环境 `.venv-platform`。
- 低氧计算环境 `.venv-hypoxemia`，PyTorch 2.5.1+cpu。
- 前端 Node.js/npm，依赖由 `frontend/package-lock.json` 安装。
- 测试使用合成数据；运行时清除 `SPO2_HYPOXEMIA_PROJECT`、旧解释器路径和 `.env` 读取。

## 测试命令

```powershell
$env:SOTABAND_LOAD_DOTENV = '0'
Remove-Item Env:SPO2_HYPOXEMIA_PROJECT -ErrorAction SilentlyContinue
Remove-Item Env:SPO2_HYPOXEMIA_PYTHON -ErrorAction SilentlyContinue
.\.venv-platform\Scripts\python.exe -m pytest tests/unit -q
.\.venv-hypoxemia\Scripts\python.exe scripts/check_hypoxemia_sources.py
.\.venv-hypoxemia\Scripts\python.exe scripts/check_hypoxemia_model_catalog.py
Push-Location frontend
npm ci
npm run build
Pop-Location
```

完整链路验收使用 `scripts/verify_hypoxemia_pr.py`：合成原始 CSV/ZIP 或窗口数据 → 数据检查 → 短训练 → 保存模型 → 按模型 ID 重选 → 另一数据集评测 → HTTP 下载 Excel。每次验收使用新的 `SOTABAND_RUNTIME_ROOT`，不读取旧任务登记表、旧模型绝对路径或外部研究项目。

在仓库根目录、完成 `scripts/install_hypoxemia.ps1 -WithVerification` 安装后运行：

```powershell
$env:SOTABAND_LOAD_DOTENV = '0'
Remove-Item Env:SPO2_HYPOXEMIA_PROJECT -ErrorAction SilentlyContinue
Remove-Item Env:SPO2_HYPOXEMIA_PYTHON -ErrorAction SilentlyContinue
$env:SOTABAND_RUNTIME_ROOT = Join-Path (Get-Location) ('.runtime/acceptance-' + [guid]::NewGuid().ToString('N'))
.\.venv-hypoxemia\Scripts\python.exe scripts/verify_hypoxemia_pr.py --model SegRNN
```

每次执行都会使用一个新的运行目录；不要把该命令指向已有的任务注册表。正常启动平台前，在该终端执行 `Remove-Item Env:SOTABAND_LOAD_DOTENV -ErrorAction SilentlyContinue`，让本机 `.env` 配置生效。

## 测试结果

| 检查 | 结果 |
| --- | --- |
| 平台单元测试 | 131 项通过，其中包含模型分组 API 测试和创建模型示例测试 |
| 前端构建 | `npm run build` 通过；保留既有动态导入和 bundle 大小警告 |
| 模型实现来源校验 | 内置 Time-Series-Library 来源文件和许可证校验通过 |
| 目录模型计算烟测 | 44 个条目：38 个通过，6 个明确跳过，0 个失败；覆盖训练前向、反向、AdamW 更新、普通评估和 EMA 评估 |
| 目录模型跳过项 | `KANAD`、`MultiPatchFormer`、`TiDE`、`WPMixer` 上游未实现分类；两个 Mamba 模型需要 CUDA 扩展 |
| 已登记模型配置 | 21 个资源中 20 个有效；历史无效 TimeMixer 配置在提交前拒绝，不自动修改 |
| 低氧端到端流程 | TimeMixer、SegRNN、SegRNNDualScaleV2 已用合成数据完成训练、保存、模型 ID 重载、独立数据集评测和 Excel 打开检查 |
| 数据契约失败分支 | 错误采样率、错误通道顺序、短窗口、缺失权重、模型或数据元数据不完整均明确拒绝 |
| 任务生命周期 | 启动失败、取消、重复领取和重复完成状态有单元测试覆盖 |
| 模型空间分类与分组 | 空分组、同名跨类别分组、刷新持久化、系统分组删除保护、非空分组删除保护测试通过；训练产物不完整时不会进入已训练列表 |
| 创建新的模型示例 | 使用 `GuideSegRNNDerived` 描述在隔离运行目录调用真实注册接口，得到 `configured`、`creation_type=custom-model`、`model_name=SegRNN` 的可训练配置资源；未生成权重，符合配置与权重分离规则 |
| 服务健康 | 后端 `8002/api/health`、前端代理 `/api/health` 和前端首页均返回 HTTP 200 |

## 测试证据

- 目录模型逐项 JSON 报告位于 `.runtime/model-catalog-checks/`，不提交到 Git。
- 端到端证据位于各自的 `.runtime/*/acceptance.json` 或 `evidence.json`，不提交到 Git。
- Excel 使用 `openpyxl` 打开下载响应，并核对模型 ID、数据集 ID、指标和来源字段。

## 独立检出与空间验收

从 GitHub 的 `review/hypoxemia-builtin-20261011` 分支重新执行 `git clone --depth 1 --single-branch`，检出提交 `9c43d3b`。新目录中没有旧虚拟环境、旧任务注册表、旧模型产物或 `.env`。通过公开依赖安装 `uv==0.8.22`，再执行 `scripts/install_hypoxemia.ps1 -WithVerification`；两套 Python 3.12.11 环境均在新目录创建，低氧环境安装 `torch==2.5.1+cpu`。安装时将 `UV_PYTHON_INSTALL_DIR` 指向该检出的 `.runtime/uv-python`，避免复用机器上已有的 uv Python。首次下载遇到网络超时，改走本机网络代理后安装成功。

在此新检出中执行 `npm ci`、`npm run build`、`pytest tests/unit -q`、来源校验与模型目录烟测，结果分别为前端构建通过、131 项单元测试通过、48 个来源文件校验通过、38 个模型通过且 6 个明确跳过。清除旧低氧项目环境变量并设置 `SOTABAND_LOAD_DOTENV=0`，使用新 `SOTABAND_RUNTIME_ROOT` 运行 `verify_hypoxemia_pr.py --model SegRNN`：合成原始 ZIP 生成 24 个窗口；单轮训练得到模型 ID；按 ID 重载后在另一数据集的 8 个验证窗口评测；Excel 下载路由返回 HTTP 200，工作簿及模型、数据集来源核对通过。错误采样率、通道顺序、短窗口、缺失权重、任务启动失败和取消均按预期拒绝或记录。

首次部署验收还在该新检出启动后端 `18002` 与前端 `15174`，后端健康接口、前端首页和前端代理健康接口均返回 HTTP 200；验收后关闭这两个专用服务。使用不同端口是因为默认 `8002/5174` 已被其他本地进程占用。该次检查没有使用真实 LLM API Key，也没有通过浏览器操作对话。

随后在同一独立检出中，使用临时安装于 Git 忽略目录的 Playwright 和 Edge 驱动浏览器界面，沿使用指南输入并操作：已处理合成数据检查（48 窗口）→ SegRNN 单轮训练 → 保存模型 → 在新一轮对话中按返回的模型 ID 重新选择 → 另一份合成数据（60 窗口）评测。页面显示本次验证集 8 个窗口及指标，两个评测结果均出现 Excel 下载入口；经前端代理请求独立评测的 Excel 返回 HTTP 200，浏览器没有脚本错误。此检查验证了真实网页输入与卡片流程，但未验证开放式 LLM 对话或真实患者数据。浏览器驱动和运行产物均未提交。

补全上方可复制命令后，在新运行目录再次执行，原始 ZIP 得到 24 窗口；SegRNN 单轮训练、模型 ID 重载、60 窗口独立数据的 8 窗口验证集评测、Excel 打开及 HTTP 下载全部通过，错误输入、缺失产物、任务启动失败和取消检查也通过。

| 新检出内容 | 文件逻辑大小，约 |
| --- | ---: |
| Git 检出源码及 `.git` | 34 MiB |
| 平台虚拟环境 | 76 MiB |
| 低氧 CPU 虚拟环境 | 1,492 MiB |
| 前端 `node_modules` | 134 MiB |
| uv 管理的 Python 3.12.11 | 63 MiB |
| uv 下载/安装缓存 | 1,536 MiB |
| npm 与 pip 缓存 | 53 MiB |
| 本次合成短流程产物 | 8 MiB |
| 整个新检出目录 | 约 3.32 GiB |

文件逻辑大小包含虚拟环境和缓存中可能共享的文件，不能简单等同于磁盘实际增量；本机 D 盘可用空间在验收前后约减少 2 GiB。建议全新 Windows CPU 安装至少预留 **5 GiB**，有原始 ZIP 时还要额外预留 ZIP 展开量、处理后的数据及训练结果空间；这些会随实际数据规模增长。只使用平台时不安装低氧虚拟环境及 PyTorch，空间需求明显较低，但本次未单独测量平台独立安装的磁盘增量。

## 未覆盖范围

- 未在 Windows CPU 环境验证 Mamba/CUDA 扩展；不宣称原生 Windows GPU 可用。
- 目录模型烟测不等于每个模型都完成完整数据处理、长轮次训练或医学性能验证。

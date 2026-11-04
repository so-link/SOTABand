# 2026-11-04：低氧功能与最新上游整合

本页记录本次实际执行结果。此前 `pr-readiness.md` 中的历史结果不作为本次验证证据。根 README 未修改，Windows 安装包不属于本次范围。

## Git 调查与来源

- 上游 PR：[so-link/SOTABand#2](https://github.com/so-link/SOTABand/pull/2)，来源 `Trumam:review/hypoxemia-builtin-20261011`，目标 `so-link:main`，本次结束前重新查询仍为 **open、未合并**。
- 已合并的是 fork 自己的 [Trumam/SOTABand#1](https://github.com/Trumam/SOTABand/pull/1)，不是上游低氧 PR。
- `origin` 实际为 `https://github.com/Trumam/SOTABand.git`，`upstream` 实际为 `https://github.com/so-link/SOTABand.git`；各 worktree 共用同一 Git 仓库和远程配置。
- fetch 前 `upstream/main` 为 `f6ea29238b0bc034cf32ff658adaaa0e4a33cc10`；fetch 后及结束前复查均为 **`20bf8923159508bc807ba31be4776ec0d9268aba`**。fetch 只更新远程引用，没有切换原工作区。
- 实际 PR 来源提交为 **`fef9ad339ffd3b4d679fc70e1079686fa740bf56`**。相对于共同祖先 `f6ea292`，PR 有 6 个提交，上游有 26 个新增提交；低氧提交尚未进入上游 main。
- `origin/main` 为 `701772971804caef7400af8a2d2add358e4221d5`，已包含上述 6 个低氧提交，但不包含这次上游新增提交。本地 `main` 仍为 `72895ac`，不是最新上游。

| 原目录 | 分支 / HEAD | 调查结果 |
| --- | --- | --- |
| `D:\code\SOTABand` | `hypoxemia-baseline-v1` / `a6bcc0e` | 保留原可运行基线；已有未跟踪迁移计划文档，未修改或清理 |
| `D:\code\SOTABand-refactor` | `refactor/timeseries-workflow` / `b24c000` | 有大量原有未提交及未跟踪文件，全部保留 |
| `D:\code\SOTABand-builtin-review` | `review/hypoxemia-builtin-20261011` / `fef9ad3` | 干净，正是实际 PR 来源 |
| `D:\code\SOTABand-windows-installer` | `feature/windows-installer` / `f491280` | 干净；在 fork 合并提交 `7017729` 上另有安装包提交，未纳入 |
| 历史 `SOTABand-pr-review` | `review/hypoxemia-offline-workflow` / `5d63629` | Git 中有失效 worktree 记录，未 prune 或删除 |

通过比较实际工作文件（包含 refactor 的未跟踪 runtime、依赖及脚本）与 PR 提交，确认主要低氧实现已经进入 `fef9ad3`。差别主要是旧文档、验收脚本及非本次范围的平台文件，未按目录名称重复移植旧分支。

## 整合方式与基线优先原则

新分支：`integration/hypoxemia-upstream-20261104`。

整合目录：`D:\code\SOTABand-hypoxemia-integration-20261104`。

从实际 PR HEAD 新建 worktree，再合并最新 `upstream/main`。合并提交 `347dc0a` 的两个父提交分别为 `fef9ad3` 和 `20bf892`；随后 `b3100cd` 收回不必要的 Agent 界面/API 覆盖，`af3821f` 修复低氧环境安装的 wheelhouse 验收支持。后续本地提交可用 `git log --oneline integration/hypoxemia-upstream-20261104` 查看。

15 个冲突文件均逐处检查，未整文件盲选：

- `.gitattributes` 合并上游行尾规则与低氧 vendor/lock 文件规则。
- Agent 路由、入口、工厂保留上游三类 Agent、异步 Windows 管道、资源双根和执行接口；补充历史 `interactive-agent` / `interactive_agent` 命名兼容。
- 工具执行器保留上游安全审查，低氧参数通过 JSON 解码进入 Python，支持布尔值和空值。
- 数据/模型登记适配上游私有资源根；API、任务、工具和新进程读取同一登记表。保留上游内置资源读取和私有资源优先规则，不覆盖已有用户资源。
- 前端保留上游模式选择、Agent 类型和依赖/迁移入口，低氧训练参数、模型分类、任务进度及下载卡片追加适配；Agent 详情沿用上游消息展示方式。
- 工具登记表保留上游全部记录及顺序，仅追加低氧工具，未改变其它工具的内容。
- 训练任务尚未完成时，“继续”不再回落到数据扫描；完成后进入保存选择。失败、取消状态也不会误入评测。

设置 `SOTABAND_RUNTIME_ROOT` 时，默认私有根为该目录下的 `resources-private`，保证多次验收隔离；显式 `SOTABAND_PRIVATE_RESOURCES` 优先。未设置时沿用上游私有根默认位置。旧 `.runtime/refactor` 的数据/模型注册表不会自动覆盖上游私有根；本次没有迁移或清理任何旧模型。

## 本次验证结果

| 项目 | 复用环境早期验证 | 新检出独立环境 |
| --- | --- | --- |
| 单元测试 | 最终 218 通过 | 218 通过 |
| `npm ci` 与生产构建 | 通过 | 重新安装与构建通过 |
| 内置源码与许可来源检查 | 48 文件通过 | 48 文件通过 |
| 模型目录计算烟测 | 44 条目：38 通过、6 跳过、0 失败 | 未重复整个目录计算烟测 |
| 原始 CSV/ZIP → 处理 → 检查 → 短训练 | 通过 | 重新执行通过，原始处理得到 24 窗口 |
| 保存 → 新进程按模型 ID 重载 | 通过 | 通过，模型 ID `model-imported-10c10ef78089` |
| 独立数据集评测 | 输入 60 窗口，验证划分 8 窗口 | 输入 60 窗口，验证划分 8 窗口 |
| Excel 生成、HTTP 下载及工作簿来源校验 | 通过 | HTTP 200，下载字节与文件一致，工作簿与模型/数据 ID 核对通过 |
| 采样率、通道顺序、短窗口、所有窗口都偏短、缺失权重 | 全部拒绝 | 全部重新验证并拒绝 |
| 真实子进程启动失败、取消、重复取消 | 持久化状态符合预期 | 重新验证通过 |
| 真实浏览器训练、保存、新会话重选、独立评测和下载 | Edge/Playwright 通过，无页面脚本错误 | 未在新检出重复完整浏览器流程 |

目录跳过项：`KANAD`、`MultiPatchFormer`、`TiDE`、`WPMixer` 未实现分类；`MambaSingleLayer`、`SegRNNMambaFusion` 需要 CUDA 扩展。当前静态登记表没有低氧配置资源，登记配置检查为 0；44 个模型实现条目的烟测是实际执行结果，不引用旧记录中的“21 个登记配置”。

浏览器复核覆盖上游 Windows Agent 管道下的多轮卡片、训练参数编辑、任务轮询、保存模型、模型空间分类、全新浏览器会话按 ID 选择、独立 60 窗口评测及浏览器实际下载。浏览器使用专用端口 `18012/15184`，未使用 LLM API Key。独立平台环境还实际完成了交互 Agent 的启动、重启与状态查询。

前端保留动态导入及 bundle 大小警告。早期一个上游测试假定 uv 环境安装了 `pip`，改用测试必然具备的 `pytest` 来检验已安装发行版；不是跳过测试。

另外尝试运行上游独立的 `tests/test_security_review.py`。它不被 pytest 收集；在 Windows 执行到其 15 秒子进程超时用例时抛出 `subprocess.TimeoutExpired` 并中止，未将整个独立脚本记为通过。上游相关单元测试已包含在 218 项中；完整独立安全脚本仍未通过，不为本次低氧整合改写上游安全机制。

## 干净检出和可复制命令

新检出目录：`D:\code\SOTABand-hypoxemia-clean-20261104`，从本次本地整合提交创建 detached worktree，代码版本包含 `af3821f`。两套环境均在该新目录创建，未复制旧虚拟环境；默认计算解释器指向该检出的 `.venv-hypoxemia`。Python 3.12.11 由 uv 管理，两套依赖由仓库 lock 文件安装。未使用外部低氧研究目录、个人 `.env`、旧模型、旧注册表或患者数据。

工具登记顺序按基线收敛后，又从最终代码提交 **`b5b7b0f`** 在同一独立检出重跑全流程与单元测试：24 窗口原始处理、短训练、新进程重载、独立评测及下载全部通过，模型 ID 为 `model-imported-b9f077cf0eb8`，218 项单元测试再次通过（24.37 秒）。证据为 `.runtime/final-acceptance/acceptance.json`、`.runtime/final-unit.txt`。随后只更新本页验收记录，未再改动代码。专用浏览器验证服务 `18012/15184` 与其 Agent 已停止。

正常联网环境按原安装文档执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_hypoxemia.ps1 -WithVerification
```

本机首次安装受到 IPv6 下载速度及 D 盘不足影响，PyTorch 解包和新检出的第一次 npm 安装失败。通过 uv 自带缓存管理释放本次下载缓存后，使用公开源重新下载并校验的 77 个 wheel 安装成功；没有变更依赖版本。PyPI 包核对其 SHA256，PyTorch CPU wheel 核对官方索引 SHA256 `a6b720410350765d3d77c01a5ce098a6c45af446284e45e87a98b8a16e7d564d`。

本机实际恢复命令（wheelhouse 不提交）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_hypoxemia.ps1 `
  -WithVerification -Wheelhouse <已校验wheel目录> -WheelhouseOnly
```

`-WheelhouseOnly` 禁止访问索引，要求 wheelhouse 包含全部锁定依赖，且 Python 3.12.11 已可用。此选项同时应用于验证依赖，避免 `-WithVerification` 仍访问网络。它属于低氧源码环境安装，不是 Windows EXE 安装包功能。

在新检出根目录执行：

```powershell
$env:SOTABAND_LOAD_DOTENV = '0'
Remove-Item Env:SOTABAND_HYPOXEMIA_PYTHON,Env:SOTABAND_PLATFORM_PYTHON,Env:SOTABAND_PRIVATE_RESOURCES,Env:SPO2_HYPOXEMIA_PROJECT,Env:SPO2_HYPOXEMIA_PYTHON -ErrorAction SilentlyContinue
$env:SOTABAND_RUNTIME_ROOT = Join-Path (Get-Location) ('.runtime/unit-' + [guid]::NewGuid().ToString('N'))
.venv-platform/Scripts/python.exe -m pytest tests/unit -q
.venv-hypoxemia/Scripts/python.exe scripts/check_hypoxemia_sources.py
$env:SOTABAND_RUNTIME_ROOT = Join-Path (Get-Location) ('.runtime/acceptance-' + [guid]::NewGuid().ToString('N'))
.venv-hypoxemia/Scripts/python.exe scripts/verify_hypoxemia_pr.py --model SegRNN
npm --prefix frontend ci
npm --prefix frontend run build
```

早期目录模型命令为 `scripts/check_hypoxemia_model_catalog.py`，使用专用计算解释器。实际证据分别保存在整合目录 `.runtime/model-catalog-checks/`、`.runtime/acceptance-v2/acceptance.json`、`.runtime/browser/evidence.json`，及新检出 `.runtime/clean-acceptance/acceptance.json`、`.runtime/clean-unit.txt`、`.runtime/frontend-build-retry.txt`。全部是本次生成的本机忽略产物，不提交到 Git。

## 限制、Git 状态和后续

- 未覆盖真实患者数据、长轮次训练、医学性能、CUDA/Mamba、开放式 LLM 对话及上游其它工具的完整业务流程。
- 验收是独立数据集的验证划分，不是对全部 60 窗口评测，也不是医学外部验证。
- 新检出的两个上游工具文件 `aerial-image-quality-batch-assessment/tool.py` 和 `function-plotter/tool.py` 有上游 CRLF 与属性规则导致的行尾状态；`git diff --ignore-space-at-eol` 为空。未为消除该提示而修改上游工具内容，不属于用户修改或运行产物。
- 整合分支的增量已检查，不含患者数据、权重、CSV/PKL/ZIP/XLSX、运行日志、虚拟环境、个人配置或密钥。vendor 原文件保留来源哈希，未批量改写其既有尾随空白。
- 原有 refactor 未提交修改、baseline 未跟踪文件、审查分支和安装包分支均保留。可回到 `D:\code\SOTABand-builtin-review` 的 `fef9ad3` 继续运行原 PR 版本；不需要 reset 或删除本次 worktree。
- 未推送、未创建或修改 PR、未合并远程 main。下一步应在用户决定发布后更新原上游 PR #2 的来源分支，而不是重新提交重复功能 PR。当前远程 PR 仍指向 `fef9ad3`，尚未获得本地整合结果。

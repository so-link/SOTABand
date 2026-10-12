# 低氧血症架构与扩展说明

界面步骤见[使用指南](hypoxemia-operation-guide.md)，安装见[运行环境](hypoxemia-runtime-and-delivery.md)，实际验证及边界见[验收记录](pr-readiness.md)。

## 调用链与职责

```mermaid
flowchart TD
    UI[ChatInput / InlineCard] --> API[Chat API / SSE]
    API --> Agent[InteractiveAgent]
    Agent --> Registry[WorkflowRegistry]
    Registry --> Domain[HypoxemiaWorkflowAdapter / Conversation / State]
    Domain --> Service[TimeSeriesWorkflowService]
    Service --> Binding[HypoxemiaTrainer / HypoxemiaEvaluator]
    Binding --> Executor[ToolExecutor]
    Executor --> Tool[timeseries-classification-runner]
    Domain --> Host[WorkflowHost]
    Host --> Tasks[TaskManager / Worker]
    Tool --> Request[训练或原始处理任务请求]
    Request --> Tasks
    Tasks --> Compute[仓库内 hypoxemia/runtime 独立计算进程]
    Tool --> Evaluation[runtime/evaluate_dataset.py]
    Compute --> Handler[低氧 TaskHandler 校验产物]
    Handler --> Catalog[运行目录内的数据集和模型登记]
    Evaluation --> Excel[评测 JSON / XLSX]
    Excel --> Download[GET /api/file/download]
```

### 架构图说明

1. `ChatInput / InlineCard` 收集用户的按钮选择、路径、架构和训练参数；`Chat API / SSE` 将流式事件送到前端。
2. `InteractiveAgent` 只负责会话路由，`WorkflowRegistry` 找到低氧领域工作流；低氧规则不放在通用 Agent 中。
3. `HypoxemiaWorkflowAdapter / Conversation / State` 管理数据类型、模型选择、架构编辑、训练、保存和评测阶段；`TimeSeriesWorkflowService` 提供时序任务的通用调用协议。
4. `HypoxemiaTrainer / HypoxemiaEvaluator` 是领域绑定层，调用 `timeseries-classification-runner`。工具只生成任务请求或执行评测，不在 FastAPI 请求线程中运行 PyTorch 训练。
5. `TaskManager / Worker` 负责排队、领取、取消、失败和完成状态；`hypoxemia/runtime` 是由仓库交付的独立计算进程，负责 CSV/ZIP 处理、窗口检查、训练和权重加载。
6. `低氧 TaskHandler` 在任务完成时校验产物和来源，把数据集、模型、任务及评测结果登记到可配置运行目录；`evaluate_dataset.py` 只使用用户选中的模型和数据集，最后生成 JSON/XLSX，通过文件路由下载。

### 与原架构的区别

这里的“旧架构”特指加入低氧血症预测之前、用户最初 clone 的原版 SOTABand 平台。该平台只有通用 Agent、API、工作流和任务基础设施，没有低氧数据处理、五通道窗口检查、低氧训练评测、模型实现或低氧产物登记链路；在其上接入低氧时，计算入口还需要另行依赖外部研究项目、外部解释器和本机绝对路径。

当前流程把必要的低氧脚本、模型实现闭包、来源校验、依赖声明和计算入口放在仓库内，并通过安装流程管理独立的低氧 Python 环境。平台通用层只负责工作流、任务生命周期、资源登记和文件下载；低氧领域层负责五通道、标签、阈值和模型参数；计算进程只从仓库组件和运行目录读取数据。训练权重、scaler 和报告属于一次运行的产物，不与 Time-Series-Library 模型源码混为一类，也不会写回源码目录。

平台环境承担 API、工作流和资源登记。计算环境承担 NumPy/pandas 数据处理及 PyTorch 训练和评测；它只加载随仓库交付的低氧源码，不加载平台配置，也不接收平台 LLM Key。平台通过解释器绝对路径启动计算进程，用户不需要激活或切换计算环境。

`core/workflow`、`core/task`、`core/timeseries`、`core/runtime` 继续提供通用机制。通道、标签、阈值、预处理及模型配置属于 `resources/workflows/hypoxemia` 和其专用工具。领域任务处理器可提供子进程环境；通用 Worker 不判断低氧模型或医学阈值。

## 随仓库交付的计算组件

| 文件或目录（相对于 hypoxemia/runtime） | 作用 |
| --- | --- |
| `scripts/run_tslib_classification_trial.py` | 优化器、短训练、验证集选阈值、checkpoint 与报告保存 |
| `scripts/tslib_classification_common.py` | 严格数据加载、分组划分、标准化、模型实例化、指标 |
| `scripts/tslib_local_models.py` | 现有五通道目录需要的研究模型及共享分类头，排除波形/多分支实验模型 |
| `raw/run_dedup_pipeline.py` | CSV 列识别、按设备去重、连续段预处理、低氧标签和窗口提取 |
| `raw_zip_pipeline.py` | ZIP 安全展开、临时目录、调用原始处理入口、检查非空结果 |
| `evaluate_dataset.py` | 加载所选模型权重和训练 scaler，在所选数据集验证划分上评测 |
| `scripts/provenance.py` | 记录实际加载的自有/模型模块、文件 SHA-256、解释器和包版本 |
| `vendor/Time-Series-Library` | 现有五通道目录所需模型、层和工具的导入闭包；不含数据、训练权重、上游实验入口 |

旧工具目录中的计算脚本仅作为兼容入口。`SPO2_HYPOXEMIA_PROJECT`、`SPO2_HYPOXEMIA_PIPELINE_SCRIPT` 和 `SPO2_HYPOXEMIA_PYTHON` 不再用于选择计算源码或环境，可以从本地配置中删除。管理员可通过 `SOTABAND_HYPOXEMIA_PYTHON` 显式覆盖解释器；默认使用安装流程创建的 `.venv-hypoxemia`。

## 数据和模型契约

- 数值通道顺序固定为 `SpO2, HR, PR, RR, PI`；窗口元数据可在最前面包含 `Time`。
- 当前低氧适配器只接受 1 Hz。默认回看 256 秒，预测间隔 30 秒；配置窗口长度必须与每个实际窗口严格一致。
- `window_features.pkl` 与 `window_info.pkl` 数量一致且非空；必须有 `window_stats.json` 或 `dataset_manifest.json` 声明采样率和长度。两份声明同时存在时必须一致。
- 逐窗口核验列顺序、形状、有限数值和标签；训练与评测共用严格加载器。不会通过补齐、裁剪、默认换列或把未知标签当阴性来生成结果。
- 原始预处理默认阈值 94，预处理判断 `<94`，窗口判断 `<=94`，事件持续 20 秒；参数保存在处理配置与数据集清单中。
- 默认按 `group_id` 划分，seed=42，验证/测试各 15%；缺少分组拒绝运行。scaler 只在训练集拟合，评测加载保存的 scaler。
- 默认评测当前所选数据集的验证划分，复用训练时的划分方式、比例、seed 和验证集阈值。此结果不能替代独立测试集或临床效能验证。
- TimeMixer 系列需要多尺度输入。选择目录模型时，平台展示、保存及提交的有效默认值为 `down_sampling_layers=2`、`down_sampling_window=2`；旧资源若保存了 `0/1`，训练前应要求修改，不能等计算进程在前向阶段报 `IndexError`。
- 模型目录保留 44 个来源条目。当前 CPU 安装中，38 个分类实现经 256×5 合成张量的前向、反向、优化器更新及评估前向检查；`KANAD`、`MultiPatchFormer`、`TiDE`、`WPMixer` 的上游代码尚未实现分类任务，因此从训练候选列表移除并在提交前拒绝旧配置。两个 Mamba 模型需要另行验证 CUDA 扩展，默认也不展示为可训练候选。复查命令为 `.venv-hypoxemia/Scripts/python.exe scripts/check_hypoxemia_model_catalog.py`，结果写到被忽略的 `.runtime/model-catalog-checks/`。

模型产物包括 `best_model.pt`、`scaler.pkl`、`tslib_model_config.json`、`threshold_report_f1.json`、`manifest.json` 和 `runtime_provenance.json`。`SOURCE.json` 说明的是模型实现代码版本；`best_model.pt` 是一次训练的权重，二者不能互相替代。

任务 ID 对应运行目录和模型 manifest；保存模型后产生模型资源 ID，并记录训练任务和数据集 ID。重新选择模型必须解析其登记的真实权重路径。评测记录模型 ID、数据集 ID、数据路径、模型路径、阈值与划分范围；Excel 同时包含来源信息和指标。

## 运行状态与存储

运行目录由 `SOTABAND_RUNTIME_ROOT` 配置，默认 `.runtime/refactor`。任务、模型登记、数据登记、原始处理结果、评测及下载文件都属于运行数据。源码和 vendor 目录不作为输出目录。新运行目录只允许从静态目录初始化可移植的资源配置，不导入历史训练记录或绝对模型/数据路径。

任务领取在登记锁内完成，只有一个 Worker 能领取 queued 任务；终态不被覆盖。启动失败写入 failed，取消终止工作进程及计算进程树，完成处理期间阻止取消破坏产物登记。模型保存和完成处理保持幂等。

pickle 和 PyTorch checkpoint 只用于用户明确选择的本地研究数据与模型。平台不再要求为每个数据目录设置 `SPO2_HYPOXEMIA_TRUSTED_DATA_ROOTS`；数据路径、文件完整性、通道顺序、采样率和窗口长度仍会在使用前校验。

## 前端实现与回归点

原始前端在请求等待期间没有可见状态；当前实现已补充等待旋转图标和秒数，下表记录当前已实现行为和回归验收点：

| 需求 | 当前行为 | 改进要求与验收方式 |
| --- | --- | --- |
| 等待输出时显示旋转进度图标 | `MessageList` 在发送后、收到第一段回复前显示旋转图标和等待秒数；后台任务卡片显示排队、运行、失败、取消和完成状态；收到内容、错误或取消后停止等待提示 | 已完成；回归快速回复、长时间无首段回复、流式输出和任务异常 |
| 模型列表分类 | `ResourceBrowser` 将模型空间拆为“已训练模型”和“模型架构与配置”两个可折叠列表。只有 `trained` 且权重、配置、scaler、manifest 完整的资源进入已训练模型；其他资源进入架构与配置。刷新时重新读取状态，训练完成后自动移动分类 | 已完成；回归空列表、刷新、重启及训练完成后的分类 |
| 两类模型各自新建分组 | 两个类别各有独立的新建分组按钮、折叠分组和模型数量。分组记录保存 `category` 与名称，同名分组可存在于两个类别；空分组刷新后保留。非空分组和系统分组不能删除，模型详情只显示当前类别的分组 | 已完成；回归创建、刷新、空分组、同名分组、非空分组删除和系统分组删除 |

前端实现遵循后端资源状态；分类和分组操作不改写训练产物、模型 ID 或任务来源。实现入口为 `frontend/src/components/center-panel/MessageList.tsx`、`frontend/src/components/left-panel/ResourceBrowser.tsx`、`frontend/src/stores/resource-store.ts` 和 `app/api/routes/model_routes.py`；状态判定及分组持久化由 `core/resource/registry/model_registry.py` 负责。

## 扩展

用户文档统一位于本目录，入口为 [index.md](index.md)。`scripts/create_hypoxemia_demo_data.py` 只生成用户指南的合成 ZIP、训练窗口和另一批评测窗口，不注册资源、不调用 LLM、不自动训练；需用计算环境执行。它拒绝覆盖已有输出目录，生成文件应留在被忽略的运行目录中。

新增低氧模型应在领域目录增加实现、依赖声明和目录项，并验证固定通道契约、权重重载及失败分支。依赖升级后重新生成锁文件并运行验收脚本。其他领域应实现自己的 WorkflowAdapter、Trainer/Evaluator 和 TaskHandler，复用通用任务及资源接口，不修改通用 Agent 来添加医学规则。

沿用单机静态工作流挂载方式；多实例任务调度、进程被操作系统强杀后的自动恢复、实时报警和在线单窗口预测不在本次交付范围内。

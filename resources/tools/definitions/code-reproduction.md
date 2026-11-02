---
id: code-reproduction
name: GPU代码复现
version: 1.5.4
type: function
language: python
status: active
created: 2026-09-28
modes: 20
---

# GPU代码复现

## 1. 功能概述

11.3.2 以固定代码快照和不可变计划创建 GPU 容器任务，提供排队、资源租约、超时、取消、恢复、指标比较与报告。 独立实现于本项目，不依赖旧 ResearchAgent 的代码路径、服务或镜像。

## 2. 模式定义

入口为 `execute(**kwargs) -> dict`。mode 缺省为 inspect。自然语言复现只要求一段普通文本；高级计划仍支持 JSON。列表参数支持数组、JSON 数组字符串或逗号分隔文本。路径均指部署机器本地路径。

### 模式 1：reproduce（复现）

- 模式标识：reproduce
- 描述：从一句普通描述中解析源码、命令和 GPU 资源，创建不可变计划并开始调度。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| request | string | 是 | - | 本地源码须包含目录和命令；使用 snapshot_id 时可省略目录，仅在 README 有唯一候选命令时允许省略命令 |
| snapshot_id | string | 否 | - | GitHub 代码查找工具返回的固定快照 ID；提供时不再填写本地源码目录 |
| idempotency_key | string | 否 | - | 重复提交同一请求时可提供相同键 |

#### 输出说明

返回任务 ID、状态、前置检查，以及工具实际解析出的固定来源、命令来源、GPU 数量、超时和镜像。使用 GitHub 快照时按固定 URL 与 commit 克隆，不跟随默认分支漂移。路径含空格时用引号括起；启动命令只接受程序及参数，不接受管道、重定向或多条 shell 命令。

### 模式 2：inspect（检查环境）

- 模式标识：inspect
- 描述：检查 NVIDIA GPU 和 Docker 服务。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 3：plan（创建复现计划）

- 模式标识：plan
- 描述：校验 JSON 计划并固定代码来源；source_path 和 snapshot_id 二选一。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| request | string | 否 | - | 用普通文字说明源码或快照、命令、GPU 和时限；无需 JSON |
| plan_json | string | 否 | - | 仅供高级程序调用；与 request 至少提供一个 |
| snapshot_id | string | 否 | - | 代码查找工具固定的仓库快照 |
| source_path | string | 否 | - | 本地源码目录，不含数据集或模型权重 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 4：run（提交任务）

- 模式标识：run
- 描述：幂等提交持久任务，不同步等待训练。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| plan_id | string | 是 | - | 前序操作返回的 plan_id |
| idempotency_key | string | 否 | - | 重复提交同一计划和键返回同一任务 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 5：resume（检查点恢复）

- 模式标识：resume
- 描述：新建任务，将原输出只读挂载到 /checkpoint；命令必须显式加载检查点。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_id | string | 是 | - | 前序操作返回的 job_id |
| plan_id | string | 否 | - | 含检查点加载命令的新计划 ID |
| idempotency_key | string | 否 | - | 幂等请求键 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 6：jobs（任务列表）

- 模式标识：jobs
- 描述：列出当前用户的任务。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 7：status（任务状态）

- 模式标识：status
- 描述：查询并推进任务状态；无后台调度时可手工轮询。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_id | string | 是 | - | 前序操作返回的 job_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 8：cancel（取消任务）

- 模式标识：cancel
- 描述：停止实际容器并释放 GPU 租约。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_id | string | 是 | - | 前序操作返回的 job_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 9：logs（查看日志）

- 模式标识：logs
- 描述：返回容器标准输出和错误输出的尾部。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_id | string | 是 | - | 前序操作返回的 job_id |
| lines | int | 否 | 200 | 1-10000 行 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 10：report（复现报告）

- 模式标识：report
- 描述：导出环境、代码、状态及指标比较记录。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_id | string | 是 | - | 前序操作返回的 job_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 11：diagnose（失败分析）

- 模式标识：diagnose
- 描述：调用平台 LLM 根据日志提出诊断，不自动修改代码或声称修复。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_id | string | 是 | - | 前序操作返回的 job_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 12：worker_start（启动调度）

- 模式标识：worker_start
- 描述：启动本工具附属后台进程，处理 GPU 队列和论文订阅。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 13：worker_status（调度状态）

- 模式标识：worker_status
- 描述：查看心跳、进程和错误。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 14：worker_stop（停止调度）

- 模式标识：worker_stop
- 描述：停止调度进程；已运行容器需单独 cancel，容器内超时仍生效。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 15：config_status（配置状态）

- 模式标识：config_status
- 描述：查看凭据是否配置及来源，不发起付费模型请求。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| config_file | string | 否 | - | 可选临时配置 JSON 路径，仅预览不保存 |

#### 输出说明

返回 llm/github/openreview 配置状态、缺少字段、持久路径及本机配置命令；不返回密钥。

### 模式 16：configure（持久配置）

- 模式标识：configure
- 描述：从本机文件替换当前用户的学术工具配置，不改平台设置。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| config_file | string | 是 | - | 部署机上的受保护 UTF-8 JSON 文件路径 |

#### 输出说明

保存成功后返回脱敏配置状态；失败保留原配置。

### 模式 17：config_reset（清除私有配置）

- 模式标识：config_reset
- 描述：删除当前用户的学术工具配置，恢复环境或平台默认。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回删除后的配置状态；环境变量和平台 Key 不会被删除。

### 模式 18：preflight（运行前检查）

- 模式标识：preflight
- 描述：在占用 GPU 前固定源码，检查入口、依赖、命令、数据/权重提示和可选运行环境。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| source_path | string | 否 | - | 本地源码目录；与 snapshot_id 二选一 |
| snapshot_id | string | 否 | - | GitHub 代码查找工具返回的固定快照 ID |
| command | string | 否 | "" | 拟运行的程序及参数，例如 python train.py |
| image | string | 否 | "sotaband-academic-gpu:1.0" | 检查运行环境时使用的镜像 |
| gpu_count | int | 否 | 1 | 检查运行环境时要求的 GPU 数量 |
| check_runtime | bool | 否 | false | 是否实际检查 Docker 镜像和 GPU；默认只做源码静态检查 |

#### 输出说明

返回固定快照、候选入口、依赖文件、建议命令、阻断问题和警告。ready 只表示已通过这些前置检查，不代表论文指标一定可复现。 未指定启动命令时 ready=false；不猜测首个 Python 文件。自然语言 reproduce 在预检失败时返回 PREFLIGHT_FAILED，不创建任务。依赖清单缺失的历史仓库须明确准备并检查镜像后使用高级 plan/run；本工具不声称能自动完成所有论文的环境适配。

### 模式 19：benchmark（多次实验汇总）

- 模式标识：benchmark
- 描述：汇总多个已结束复现任务的数值指标，并检查源码、镜像和命令是否一致。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_ids | list | 是 | - | 至少两个已结束任务 ID |

#### 输出说明

返回每个数值指标的样本数、均值、总体标准差、最小值和最大值；协议不一致时明确警告，不把不可比结果合并解释。

### 模式 20：recover（错误处理与重试）

- 模式标识：recover
- 描述：查看实际失败原因；配置或修复后按恢复编号重试原任务，不自动切换来源或重放训练提交。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| request | string | 是 | - | 普通回复：配置好了，继续／查看刚才的问题／第二个；也支持恢复编号 |
| config_file | string | 否 | - | 后端机器上的受保护配置文件路径，仅用于本次重试，不在对话中提供密码或 Key |

#### 输出说明

错误保留原 failed 状态和错误码，并在 message 中说明真实原因、需要补齐的配置或依赖、恢复编号。部分来源失败保留真实成功结果并明确失败来源，不把失败解释成零篇论文。恢复记录按当前应用用户隔离；查看和取消不发起网络请求。仅明确“重试”才执行原任务，仍使用同一来源和条件；同一记录重试互斥，完成或取消后不重复执行。

OpenReview/GitHub/LLM 凭据由本机终端隐藏输入向导配置，或通过 config_file 指定本地文件。对话只提示缺少字段及下一步，不接收明文凭据；现有前端没有新增密码控件。XeLaTeX 等系统依赖由用户在后端环境安装后重试，不自动安装或把 PDF 改成其他格式。DBLP 验证页明确报错，不绕过验证、不换源冒充成功。

track 已创建订阅时重试其 sync，不重复创建订阅；compose 已创建项目而模型步骤失败时重试原项目的生成步骤。生成仍可能产生实际模型费用。训练提交、续跑、部署、删除及其他未列入可重放范围的操作仅提供修复说明，须检查实际状态后重新提交。进程被平台超时强杀、后端退出或 SSE 连接中断可能无法返回工具消息；本工具不能修复框架传输，也不宣称可自动恢复训练检查点。重试期间被强杀的记录保留 retrying，禁止盲目再次执行。

前端需把用户回复路由到本工具 recover 模式；若默认自然语言入口收到完整“重试 recovery-编号”也会直接识别。多个待处理任务时先列出候选；只有一个待处理任务时可用“配置好了，继续”接着执行。调用示例：选择本工具的“错误处理与重试”，输入“重试 recovery-实际返回编号”。

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed；任务提交成功不代表训练或论文复现成功 |
| output_format | string | text / table / file |
| message | string | 结果说明或错误原因 |
| data | object | text 为文本，table 包含 columns/rows/items，file 包含 file_path；同时保留结构化字段 |

失败时 data 包含 error_code、retryable 和 text，必要时 details 提供缺少的配置字段或来源诊断证据。公共参数 config_file 指向本次调用的本机配置文件，不接受把密钥放进聊天；可选 register_output=true 会通过既有 api-data-register 登记导出目录，登记失败保留文件并返回 registration_error。

## 4. 依赖环境

Python >= 3.12，与平台相同的核心依赖；额外依赖在本工具 requirements.txt，共享实现位于 resources/tools/_academic。部署每个工具时在平台 Python 环境基础上建立本地 .venv 并安装依赖：

```bash
python -m venv --system-site-packages resources/tools/implementations/code-reproduction/.venv
resources/tools/implementations/code-reproduction/.venv/bin/python -m pip install -r resources/tools/implementations/code-reproduction/requirements.txt
```

Windows 本地测试可使用统一测试环境运行 ToolExecutor；服务器独立 .venv 使用现有执行器约定的 bin/python，不修改执行器。共享依赖固定 `openreview-py==2.6.2`。GITHUB_TOKEN 为可选 GitHub 令牌；SOTABAND_ACADEMIC_DATA 指定工具数据根目录（默认 data/academic）；当前用户由平台 SOTABAND_USER_ID 上下文决定。SOTABAND_ACADEMIC_HTTP_PROXY=direct 只控制工具自有 HTTP 客户端；OpenReview 官方 SDK 遵循进程的代理与 NO_PROXY 环境。凭据不写进目录、计划或规格文件。

## 5. 运行机制

### 5.1 执行流程

读取 mode、校验参数、调用独立实现、将业务记录写入用户隔离的 SQLite/目录、返回统一结果。跨调用状态保存在磁盘，不依赖工具进程内存。仅新增工具及注册适配，不改平台、前端或其他工具。

### 5.2 错误处理

参数错误返回 INVALID_INPUT；缺少记录为 NOT_FOUND；并发冲突为 BUSY 或 REVISION_CONFLICT；来源异常、限流和依赖缺失显式报告。不会以示例数据替代真实返回。私有记录只在当前用户空间读取。

### 5.3 计划格式与执行边界

对话中优先使用 `reproduce`：本地代码可说“复现 `E:\projects\paper-code` 中的代码，使用1张GPU运行 `python train.py`，超时120秒”；前一步已查到 GitHub 代码时，Agent 直接传 `snapshot_id`，用户只需说“使用1张GPU复现这个仓库，超时2小时”。工具优先采用仓库 README 中识别的候选命令，并回显命令来源；没有可靠候选命令时要求补充，不猜测复杂运行参数。默认镜像为 `sotaband-academic-gpu:1.0`，可在文本中覆盖。复杂指标、数据挂载、网络和恢复设置继续使用结构化 `plan` 模式。

`plan_json` 可以是 JSON 字符串或对象。必填 image（预先准备的 Docker 镜像）、command（非空参数数组）；默认 gpu_count=1、min_free_mib=1024、memory_gb=16、cpus=4、shm_gb=2、timeout_seconds=21600、seed=42、network=none、metric_file=metrics.json。上游仓库不能写 `metrics.json` 时，可用 `log_metrics` 提供至多 32 个简单指标名；工具只解析日志中的 `name: number` 或 `name=number`，不执行用户正则，并记录每个指标来自文件还是容器日志。

```json
{"image":"sotaband-academic-gpu:1.0","command":["python","train.py"],"gpu_count":1,"min_free_mib":1024,"timeout_seconds":300,"memory_gb":4,"targets":{"mse":{"value":0.0001,"direction":"lower","tolerance":0.0001,"source":"synthetic smoke-test criterion","protocol":"1024 synthetic samples, seed 42; not a paper benchmark"}}}
```

targets 每项须提供有限数值 value、非负 tolerance、direction（higher/lower/match）、source、protocol。训练程序将指标对象写到 `/output/metrics.json`，输出目录由 ACADEMIC_OUTPUT_DIR 提供，随机种子由 ACADEMIC_SEED 提供。设置环境变量不等于保证框架确定性，训练代码必须自行设置各库种子。dataset_path 只读挂载到 /data；恢复任务的旧输出只读挂载到 /checkpoint。默认仅检出主仓库；plan.submodules=true 会按固定提交初始化子模块。只允许加载事先审查可信的代码、权重和镜像。镜像必须含 python；任务使用工具内的容器启动器强制超时，并记录实际 image ID。

GPU 按 UUID 原子独占分配，不做 CPU 降级。队列仅协调使用同一 SOTABAND_ACADEMIC_DATA 的本工具任务，不管理外部训练进程。租约占用和显存共同参与准入；单机多 GPU，非跨主机集群调度。资源不足为 waiting_resources。容器不自动删除，以保留审计记录；报告归档后管理员可按任务名称清理。容器隔离不是不可信代码的完整安全边界，生产应使用专用 GPU 主机及受控 Docker 权限。

执行成功与指标匹配分开报告；metric_matched 仅表示用户指定指标和容差满足，不证明论文完整复现。没有可比较指标为 not_comparable；未完成任务不会标为复现成功。

### 5.4 服务器部署

Linux 服务器安装 Docker Engine、NVIDIA 驱动与 NVIDIA Container Toolkit，先验证 `docker run --rm --gpus all <可信镜像> nvidia-smi`。按论文环境构建独立镜像，不依赖 ResearchAgent。附带 Dockerfile 是 PyTorch CUDA 12.4 验证基线，不是任意论文的通用依赖环境：

```bash
docker build -t sotaband-academic-gpu:1.0 resources/tools/implementations/code-reproduction
python -m resources.tools._academic.worker
```

新机可从项目根目录使用独立部署入口；`check` 不修改系统，`install --dry-run` 只显示动作。Debian/Ubuntu 可选择自动安装 Docker 和 NVIDIA Container Toolkit、创建工具 venv、构建基线镜像及 LTSF-Linear 论文镜像、验证 GPU 容器并安装 systemd worker：

```bash
python3 resources/tools/implementations/code-reproduction/deploy.py check --verify-container
sudo python3 resources/tools/implementations/code-reproduction/deploy.py install \
  --install-host-packages --with-ltsf-linear --systemd \
  --service-user <服务器账户> --user-id server
```

部署器不自动安装 NVIDIA 内核驱动；驱动与内核、GPU 型号和云厂商镜像绑定，缺失时会明确停止并要求安装驱动、重启后重试。非 Debian/Ubuntu 或集群环境只执行 `check` 与应用层安装，主机包、共享存储和调度器由运维系统负责。已有 Docker/NVIDIA Container Toolkit 时省略 `--install-host-packages`。

后台 worker 可由 systemd 管理；worker_start 返回 launcher_pid 和 launch_id，worker_status 中的 pid 才是实际解释器进程，Windows 下两者可能不同。停止请求会等待当前来源请求结束后退出；容器须单独取消。工作目录为本项目根目录，设置与工具调用相同的 SOTABAND_USER_ID、SOTABAND_ACADEMIC_DATA 和 Python 环境。避免同时由 systemd 与 worker_start 管理同一进程。SQLite WAL 仅放本机磁盘，不放 NFS；迁移时停止工具写入和 worker 后备份整个数据根目录。跨机器移动源码、数据集后须新建计划以更新绝对路径。

检查点恢复不替程序实现优化器或采样器恢复；命令和训练代码负责恢复语义。诊断仅产生建议，用户审阅后创建新计划。接口依据：[Docker GPU](https://docs.docker.com/engine/containers/resource_constraints/#gpu)、[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

### 5.5 配置与凭据

LLM 默认直接复用项目全局配置：根目录 `.env` 中的 `LLM_PROVIDER`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，以及平台设置页写入并动态刷新的 `settings.llm`；无需为学术工具重复填写。仅当用户明确需要不同模型账户时，才使用 `SOTABAND_ACADEMIC_LLM_*`、本节终端向导或临时配置覆盖。GitHub 与 OpenReview 凭据属于学术工具的当前应用用户配置，不写平台 `.env`、不修改全局模型、不影响其他工具。所有业务模式可传公共参数 `config_file`，指向部署机 UTF-8 JSON 配置文件，只在本次调用生效，不保存到订阅、复现计划、写作项目或导出结果。后台 worker 不继承这类临时参数，需持久配置或进程环境。

无需在聊天、提示词或命令行参数里粘贴 API Key、token 或密码。在项目根目录、与平台相同的 `SOTABAND_USER_ID` 和 `SOTABAND_ACADEMIC_DATA` 下，用实际运行工具的 Python 执行：

```bash
python -m resources.tools._academic.configuration --service llm
python -m resources.tools._academic.configuration --service github
python -m resources.tools._academic.configuration --service openreview
```

仅配置所需服务；终端对 API Key、token 和密码使用隐藏输入。LLM 输入 provider、base_url、model、api_key；已知服务商的端点和模型可留空使用平台预设，自建兼容服务须明确端点与模型。自建无鉴权接口也须提供其客户端接受的非空占位 Key。远程端点要求 HTTPS，仅回环地址允许 HTTP。不混用其他服务商或平台 Key。GitHub 按官方 REST 规范输入 fine-grained PAT、classic PAT、GitHub App token 或 Actions GITHUB_TOKEN，不支持账号密码。OpenReview 默认输入官方账户 username/email 与 password，也可输入已合法取得的 session token。

非交互部署可准备受保护的 JSON，结构如下（占位符不是有效凭据）：

```json
{
  "llm": {"provider": "custom", "base_url": "https://model.example.org/v1", "model": "your-model", "api_key": "<secret>"},
  "github": {"token": "<secret>"},
  "openreview": {"username": "<account-email>", "password": "<secret>"}
}
```

只保留实际需要的服务。调用 `configure(config_file=...)` 会替换当前用户整份学术配置，不做隐式合并；终端向导则只更新选中服务。导入后源文件由操作者妥善保管或自行清理，工具不会擅自删除。不要把明文配置提交 Git 或挂入训练容器。

持久文件为 `<SOTABAND_ACADEMIC_DATA>/<用户ID的SHA256前20位>/private/configuration.bin`，`config_status` 返回实际位置。Windows 使用 DPAPI 加密，绑定当前操作系统账户；Linux 为明文 JSON，目录权限 0700、文件 0600，需保护服务器账户和备份。应用用户隔离不是同一操作系统账户下的安全沙箱。Windows 配置不能直接搬到 Linux，若迁移数据包含该文件，先调用 config_reset，再在服务器重新配置；`config_reset` 只删除工具私有配置，不删除环境变量或平台设置。

按服务选择来源，优先级：本次 config_file > 当前用户持久配置 > 环境/平台。LLM 专用环境变量为 `SOTABAND_ACADEMIC_LLM_PROVIDER`、`SOTABAND_ACADEMIC_LLM_BASE_URL`、`SOTABAND_ACADEMIC_LLM_MODEL`、`SOTABAND_ACADEMIC_LLM_API_KEY`；任一存在即视为独立 LLM 配置，不再借用平台密钥。未提供专用配置时才使用平台既有设置页及 LLM_* 配置。GitHub 使用官方 `GITHUB_TOKEN`。OpenReview 官方客户端读取 `OPENREVIEW_USERNAME` + `OPENREVIEW_PASSWORD`，或读取已有的 `OPENREVIEW_TOKEN`；二者择一。持久配置允许保存 OpenReview 密码，遵循上述 Windows DPAPI / Linux 0600 存储边界。

缺少凭据返回 `CONFIG_REQUIRED` / `AUTH_REQUIRED`，`data.details` 给出服务、缺少字段及本机配置方式；由调用者提示用户补齐后重试，后台进程不会等待交互输入。状态只返回是否配置，不返回 Key；“已配置”仅表示字段齐全，不代表已联网、额度充足或通过站点验证。

| 配置/依赖 | 使用场景 | 不依赖它的功能 |
|-----------|----------|----------------|
| LLM API Key、端点和模型 | 写作 outline/draft/revise；复现 diagnose 生成建议 | 材料导入、人工编辑、引用校验、实际训练执行、指标报告 |
| GitHub 官方 token | 远程代码内容搜索；可选提高其他 GitHub REST API 的可用额度 | 公共仓库检索、已有快照文件名搜索 |
| OpenReview 官方账号密码或 session token | 通过 openreview-py API V2 登录并检索论文 | 认证成功仍不等于站点挑战一定放行 |
| XeLaTeX 与 Noto Serif CJK SC | PDF 编译和中文排版，不是 API 配置 | Markdown、BibTeX、ZIP；DOCX/LaTeX 导出仅需 Pandoc |
| GPU 服务器环境 | 实际多卡训练、驱动/容器及论文镜像验收 | 不是购买 API Key 可以补齐的条件 |

## 6. 测试与调用

```python
from core.executor.tool_executor import ToolExecutor

result = ToolExecutor.execute_sync("code-reproduction", {"mode": "inspect"}, timeout=300)
```

离线测试：`python -m pytest resources/tools/_academic/tests -q`。联网和实际 GPU 测试需显式启用环境变量，测试输出与离线模拟结果分开；GPU fixture 仅为执行链路验证，不是论文基准实验。

验证记录（2026-09-28）：本地 RTX 4070 Laptop GPU 上通过真实 CUDA 训练、检查点恢复、容器超时、取消、GPU 租约释放和实验结果到写作工具的联动。验证镜像基于本机已有 Python 3.9 镜像独立构建，使用 PyTorch 2.5.1+cu124；服务器 Dockerfile 默认 Python 3.12，仍需在目标服务器验证驱动和论文镜像。多 GPU 原子分配已通过并发测试，未声称完成多卡硬件或多机部署验收。

真实论文验证（2026-10-09）：固定 `cure-lab/LTSF-Linear` 提交 `0c113668a3b88c4c4ee586b8c5ec3e539c4de5a6`，使用公开 ETTh1 数据运行 AAAI 2023 DLinear 多变量 336→96 协议。原仓库零修改，专用镜像为 PyTorch 2.5.1+cu124；实际 MSE 0.384144、MAE 0.404713，相对官方仓库基准 0.375/0.399 均处于预先设定的 ±0.03 容差，结论为 metric_matched。该结果验证单个论文配置，不代表所有论文可免适配运行。

联网验证：`ACADEMIC_LIVE_HTTP=1`；实际 GPU 验证：`ACADEMIC_LIVE_GPU=1`（Windows 使用对应的 $env 设置）。本工具范围的测试位于 resources/tools/_academic/tests，不会自动执行其他工具。离线运行默认跳过这些外部资源测试。

真实随机验证（2026-10-30）：作者仓库 PetarV-/DGI 固定提交 61baf67d7052905c77bdeb28c22926f04e182362，使用仓库自带 Cora 数据，原始 execute.py 零修改。images/dgi.Dockerfile 固定 Python 3.9、PyTorch 2.5.1、NumPy 1.21.6、SciPy 1.7.3、NetworkX 2.6.3，解决上游旧版 SciPy 导入兼容问题。首轮三个随机种子 1578499926、2007643797、359529304 的准确率均值分别为 81.466%、82.054%、81.782%（每轮执行作者原程序的 50 次分类器评估）。指标直接读取真实训练结果，不设置未经核实的论文目标值，结论保持 not_comparable。运行 `python -m resources.tools._academic.tests.live_dgi_audit --source <真实DGI源码目录> --runs 3` 可复测；需先构建 `docker build -f resources/tools/implementations/code-reproduction/images/dgi.Dockerfile -t sotaband-paper-dgi:1.0 resources/tools/implementations/code-reproduction`。本机复用了已有 Python 3.9/PyTorch 基础镜像的层；新服务器默认镜像构建和 Linux 系统安装尚需现场验证。

补充轮次（2026-10-30）：另三个随机种子 904381395、1628677374、1498078839 的真实准确率均值为 81.398%、81.806%、81.156%，累计六轮完整训练及 300 次上游分类器评估。所有测试容器均已退出并删除，日志、固定源摘要、随机种子和真实指标保留在验收输出目录。

恢复验证（2026-10-30）：本轮 28 项相关检查通过（含发现问题后的定向复测），新增恢复测试不模拟网络响应或模型输出。验证实际缺失凭据、缺失 XeLaTeX、用户隔离、恢复互斥、拒绝重复训练提交、12 组随机路径的跨进程真实失败与修复；通过平台执行器实际查询 GitHub，并使用真实模型完成修复配置后的生成，确认未重复创建写作项目。DBLP 实测仍为 HTTP 202 / Anubis 验证页，验证的是错误准确呈现，不算来源恢复成功。OpenReview 未配置真实账号，未宣称登录验收通过。GitHub 实测发现旧 microsoft/LightGBM 搜索名称被拒绝而仓库接口指向 lightgbm-org/LightGBM；现保留 HTTP 422 官方校验信息，提示修正条件，不自动修改查询。

对话可以输入 `重试 recovery-实际编号，配置文件："后端机器上的文件路径"`，仅传文件路径，无须手写 JSON 参数。也可在同一后端环境、同一用户下使用终端恢复入口：

```bash
python -m resources.tools._academic.recovery --tool reproduction --request "查看 recovery-实际编号"
python -m resources.tools._academic.recovery --tool reproduction --request "重试 recovery-实际编号"
```

`--tool` 可选 github、papers、reproduction、writing；需要替换临时配置文件时加 `--config-file`。查看不会重试，重试仍失败时保留同一编号和真实错误。已完成或取消的编号不会再次执行。对话路由和 SSE 属于现有框架，本轮没有修改或宣称已验证其端到端恢复。

新增验证轮次（2026-10-30）：11 项新边界回归覆盖随机文稿修改、持有执行锁时查看状态、完成后读取产物引用、执行入口版本检查；连同此前本地恢复回归共 30 项通过，4 项显式联网测试未在该命令中运行。另以独立真实验收脚本完成 EMNLP 2023（1182 篇）、NAACL 2022（538 篇）、JMLR 2022（353 篇）采集，共 2073 篇及 120 次随机标题检索；EMNLP 返回真实结果、OpenReview 缺凭据的混合请求明确展示部分失败。随机种子为 2137325033。

新仓库实测固定了 tkipf/pygcn、facebookresearch/mae、microsoft/LoRA 的提交和 README 证据。GPU 测试首次因 Git 未读取 Windows 已配置代理而连接失败，原失败任务保留；修正子进程网络配置后，同一官方源码地址和提交成功下载。pygcn 提交 1600b5b748b3976413d1e307540ccc62605b4d6d 在真实 GPU 上按上游默认 200 epochs 运行三个随机种子，accuracy 为 0.836、0.824、0.829。上游明确说明该 PyTorch 重实现不用于对齐论文报告数值，因此结论保持 not_comparable，仅证明实际仓库执行；不声称复现论文指标。

写作从本轮语料随机选取《Are ChatGPT and GPT-4 General-Purpose Solvers for Financial Text Analytics? A Study on Several Typical Tasks》和《Generalized Ambiguity Decomposition for Ranking Ensemble Learning》，读取各自官方摘要后完成两次真实模型生成、引用格式验证与八次导出。两个真实模型请求运行期间均可同时查看恢复状态；引用有效不代表摘要外结论已获证明。证据保存在独立临时目录 sotaband-new-cases-hx1ulspq，复测命令为 `python -m resources.tools._academic.tests.live_cross_tool_audit --root <独立验收目录> --phase papers|github|gpu|writing`（每次选择一个 phase；GPU 需要该目录已有的 pygcn-snapshot.json，写作需要 papers 阶段语料）。不使用模拟来源、预制模型回答或虚构实验数值。

恢复补充约束：写作恢复记录保存项目版本；恢复前与写作执行入口分别校验。版本变化或旧记录缺少版本证据时返回 RECOVERY_STALE，要求按当前文稿重新提交，避免覆盖用户修改。查看状态不获取执行锁。完成记录保留产物路径/项目引用，can_retry=false；历史错误与当前完成状态分开呈现。训练原始失败原因继续显示，失败任务不会被重新标为成功。

Git 代理规则仅作用于本工具子进程：direct 设置显式禁用代理；已有 Git http.proxy 配置优先保留；否则读取与 Python 请求相同的系统/环境代理，通过进程环境传递，不写 Git 全局配置、不把代理凭据写入命令参数、不在失败后自动更换来源或路由。预检引用 README 的明确复现限制；提到 dataset 不再被直接解释为必须额外挂载外部数据。

对话交互补充（2026-10-30）：常用恢复回复支持“配置好了，继续”“请继续刚才的任务”“查看刚才的问题”，不强制复制长恢复编号。多个待处理任务先列出选择，支持“第二个”等中文序号；列表序号固定绑定展示时的任务，不因新失败任务插入而改变。配置文件可用“配置文件在‘本机路径’，请继续”描述（使用双引号或中文双引号），仍不接收聊天中的明文密钥。仅在调用已到达对应工具时生效，不修改平台会话路由或 SSE。

复现任务缺目录或命令时保留原描述与已提供参数，只追问缺少的信息；可依次回复“源码在...”和“python train.py”。存在多个未补齐任务时要求用完整自然语言明确目标，不猜测。创建计划支持 request 自然语言，plan_json 仅保留给程序调用；创建计划不会提交训练。中文一至十/两、显卡数量及“最多跑两分钟”等表达可解析，数量冲突或不支持的数字写法要求澄清，不静默改用默认值。

论文一次性自然语言检索尊重单个年份，不自动创建订阅；持续/订阅请求才进入跟踪。写作可用“导出这篇文稿”，缺格式时追问并接受“Word 就行”；多篇文稿先列出供用户选择，支持《标题》定位。不会把标题里的 PDF 当作导出格式，也不会把“不要 PDF，导出 Word”理解成 PDF。缺少 XeLaTeX 仍真实失败。GitHub 限流提示在响应包含重置时间时展示 UTC 时间和本机授权配置方法，不通过换账号或换来源规避限流。

对话最终验收（2026-10-30）：按“发现错误或修改后换例重跑”完成八轮用例迭代。保留了匿名 GitHub 限流、仓库树接口 404、JMLR 2019 方向过滤后零匹配的真实记录，未换源填充结果。过程中修正了嵌套启动脚本被误识别为源码目录、导出格式回答被误当写作请求，以及“只依据摘要生成提纲”误选摘要章节的问题。新增章节/路径/格式交互回归均使用实际解析和持久状态；相关检查最终 78 项通过，4 项原有显式联网测试跳过，真实外部验收另行执行。

最后第八轮随机种子 1750619586，通过平台 ToolExecutor 实测 facebookresearch/dino（提交 7c446df5b9f45747937fb0d72314eb9f7b66930a）、ACL 2022 图方向检索（15 篇、抽查 12 篇详情）、《Hypergraph Transformer: Weakly-Supervised Multi-hop Reasoning for Knowledge-based Visual Question Answering》的官方摘要写作。实际操作包含缺少模型配置时明确提示、普通文字“麻烦你重试一下”继续真实模型请求、“导出这篇文稿”后用“Word 就行”完成导出，以及真实 GPU/Docker 检查和普通文字创建固定快照计划。该轮仅创建计划，没有提交新训练。人工复核确认生成的是全文提纲，内容与官方摘要相符；未把引用格式检查等同于全文证据验证。最后一轮无未预期失败、无后续代码修改。

实测入口：`python -m resources.tools._academic.tests.live_conversation_round --round 8`；会创建独立临时数据目录并发起真实网络/模型请求。当前证据目录为 `C:/Users/kepu/AppData/Local/Temp/sotaband-dialogue-round8-e5fjye53`，包含每一步真实响应和 summary.json。前端路由、SSE、其他工具与平台框架未改动，不把工具执行器层面的验收表述为前端传输已修复。

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.5.4 | 2026-10-30 | 增加普通语言补充信息、稳定序号选择、计划与文稿导出对话输入；修正单年份解释和资源数量解析。 |
| 1.5.3 | 2026-10-30 | Git 未单独配置代理时读取系统既有代理；预检保留上游复现限制，训练失败展示实际原因。 |
| 1.5.2 | 2026-10-30 | 增加错误可读说明、用户隔离恢复记录、纯文本显式重试；保留已创建订阅与写作项目，禁止重放训练提交。 |
| 1.5.1 | 2026-10-30 | 预检不通过时阻止自然语言任务提交；取消猜测入口，识别 execute.py；加入真实 DGI 专用镜像与随机种子验收程序。 |
| 1.5.0 | 2026-10-10 | 自然语言复现可直接消费 GitHub 固定快照，自动选择仓库候选命令并返回写作交接 |
| 1.4.0 | 2026-10-09 | 支持从上游日志安全提取指标，增加真实 LTSF-Linear/ETTh1 复现镜像与 Linux 新机自动部署器 |
| 1.3.0 | 2026-10-09 | 增加源码与运行环境前置检查，以及多任务指标统计和协议一致性检查 |
| 1.2.0 | 2026-09-28 | 增加面向对话的纯文本复现模式，自动解析本地源码、启动命令和常用 GPU 参数 |
| 1.1.0 | 2026-09-28 | OpenReview 改用官方 V2 Python 客户端，增加官方登录凭据配置与错误分类 |
| 1.0.0 | 2026-09-28 | 独立学术工具、标准接口、持久状态和证据记录 |

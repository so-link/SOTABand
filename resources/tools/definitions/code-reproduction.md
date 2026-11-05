---
id: code-reproduction
name: GPU代码复现
version: 1.5.0
type: function
language: python
status: active
created: 2026-09-28
modes: 19
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
| request | string | 是 | - | 本地源码须包含目录和命令；使用 snapshot_id 时可省略目录并自动采用仓库候选命令 |
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
| plan_json | string | 是 | - | 前序操作返回的 plan_json |
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

返回固定快照、候选入口、依赖文件、建议命令、阻断问题和警告。ready 只表示已通过这些前置检查，不代表论文指标一定可复现。

### 模式 19：benchmark（多次实验汇总）

- 模式标识：benchmark
- 描述：汇总多个已结束复现任务的数值指标，并检查源码、镜像和命令是否一致。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| job_ids | list | 是 | - | 至少两个已结束任务 ID |

#### 输出说明

返回每个数值指标的样本数、均值、总体标准差、最小值和最大值；协议不一致时明确警告，不把不可比结果合并解释。

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

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.5.0 | 2026-10-10 | 自然语言复现可直接消费 GitHub 固定快照，自动选择仓库候选命令并返回写作交接 |
| 1.4.0 | 2026-10-09 | 支持从上游日志安全提取指标，增加真实 LTSF-Linear/ETTh1 复现镜像与 Linux 新机自动部署器 |
| 1.3.0 | 2026-10-09 | 增加源码与运行环境前置检查，以及多任务指标统计和协议一致性检查 |
| 1.2.0 | 2026-09-28 | 增加面向对话的纯文本复现模式，自动解析本地源码、启动命令和常用 GPU 参数 |
| 1.1.0 | 2026-09-28 | OpenReview 改用官方 V2 Python 客户端，增加官方登录凭据配置与错误分类 |
| 1.0.0 | 2026-09-28 | 独立学术工具、标准接口、持久状态和证据记录 |

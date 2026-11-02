---
id: paper-writing
name: 论文撰写
version: 1.3.4
type: function
language: python
status: active
created: 2026-09-28
modes: 20
---

# 论文撰写

## 1. 功能概述

11.3.4 以论文元数据、用户材料和真实复现结果为依据，组织论文、生成章节、检查引用、管理修订并导出。 独立实现于本项目，不依赖旧 ResearchAgent 的代码路径、服务或镜像。

## 2. 模式定义

入口为 `execute(**kwargs) -> dict`。mode 缺省为 create。列表参数支持数组、JSON 数组字符串或逗号分隔文本。路径均指部署机器本地路径。

### 模式 1：create（创建写作项目）

- 模式标识：create
- 描述：导入可追踪材料，BibTeX 导入项标记为用户提供而非已验证文献。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| title | string | 是 | - | 前序操作返回的 title |
| research_question | string | 否 | "" | 研究问题或综述主线 |
| target_venue | string | 否 | "" | 目标会议或期刊，用于组织写作要求 |
| language | string | 否 | "中文" | 生成内容使用的语言 |
| paper_ids | list | 否 | [] | 论文跟踪工具返回的 paper ID 数组 |
| report_ids | list | 否 | [] | 论文跟踪工具返回的 digest ID，批量导入其中的具体论文 |
| job_ids | list | 否 | [] | 复现任务 ID 数组，保存当前结果快照 |
| material_paths | list | 否 | [] | 当前部署机器上的 PDF、Markdown、TXT、JSON 文件路径 |
| bibliography_path | string | 否 | - | UTF-8 BibTeX 文件 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 2：projects（写作项目列表）

- 模式标识：projects
- 描述：列出当前用户项目。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 3：attach（补充材料）

- 模式标识：attach
- 描述：添加或刷新论文、实验和文件材料，使用乐观并发控制。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |
| base_revision | int | 是 | - | read 返回的当前 revision |
| paper_ids | list | 否 | [] | 论文跟踪工具返回的 paper ID 数组 |
| report_ids | list | 否 | [] | 论文跟踪工具返回的 digest ID |
| job_ids | list | 否 | [] | 复现任务 ID 数组，保存当前结果快照 |
| material_paths | list | 否 | [] | 当前部署机器上的 PDF、Markdown、TXT、JSON 文件路径 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 4：outline（生成提纲）

- 模式标识：outline
- 描述：通过平台配置的 LLM 生成文本，不编造证据。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |
| section | string | 否 | "outline" | 章节名称 |
| instruction | string | 否 | - | 写作要求和目标语言 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 5：draft（起草章节）

- 模式标识：draft
- 描述：通过平台配置的 LLM 生成文本，不编造证据。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |
| section | string | 否 | "draft" | 章节名称 |
| instruction | string | 否 | - | 写作要求和目标语言 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 6：revise（修订章节）

- 模式标识：revise
- 描述：通过平台配置的 LLM 生成文本，不编造证据。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |
| section | string | 否 | "draft" | 章节名称 |
| instruction | string | 否 | - | 写作要求和目标语言 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 7：save_section（保存章节）

- 模式标识：save_section
- 描述：保存手工编辑内容；版本冲突时拒绝覆盖。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |
| base_revision | int | 是 | - | 当前 revision |
| section | string | 是 | - | 前序操作返回的 section |
| content | string | 是 | - | 前序操作返回的 content |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 8：read（读取项目）

- 模式标识：read
- 描述：返回项目、引用键和 Markdown 文稿。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 9：validate（引用检查）

- 模式标识：validate
- 描述：检查未知引用、待补证据标记和实验状态。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 10：versions（修订历史）

- 模式标识：versions
- 描述：查看不可变修订快照。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 11：restore（恢复版本）

- 模式标识：restore
- 描述：把历史快照保存为新的版本，不覆盖历史。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |
| version_id | string | 是 | - | 前序操作返回的 version_id |
| base_revision | int | 是 | - | 当前 revision |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 12：export（导出文稿）

- 模式标识：export
- 描述：阻止未知引用导出，生成文稿、参考文献和证据记录。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 前序操作返回的 project_id |
| format | string | 否 | "markdown" | markdown / latex / bibtex / docx / pdf / bundle |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 13：config_status（配置状态）

- 模式标识：config_status
- 描述：查看凭据是否配置及来源，不发起付费模型请求。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| config_file | string | 否 | - | 可选临时配置 JSON 路径，仅预览不保存 |

#### 输出说明

返回 llm/github/openreview 配置状态、缺少字段、持久路径及本机配置命令；不返回密钥。

### 模式 14：configure（持久配置）

- 模式标识：configure
- 描述：从本机文件替换当前用户的学术工具配置，不改平台设置。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| config_file | string | 是 | - | 部署机上的受保护 UTF-8 JSON 文件路径 |

#### 输出说明

保存成功后返回脱敏配置状态；失败保留原配置。

### 模式 15：config_reset（清除私有配置）

- 模式标识：config_reset
- 描述：删除当前用户的学术工具配置，恢复环境或平台默认。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回删除后的配置状态；环境变量和平台 Key 不会被删除。

### 模式 16：evidence_matrix（证据矩阵）

- 模式标识：evidence_matrix
- 描述：把跟踪到的论文整理为可引用的证据矩阵，区分摘要证据和仅元数据记录。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 写作项目 ID |

#### 输出说明

返回引用键、标题、作者、会刊、方向相关性、命中词、证据层级、摘要和链接，不从摘要臆测数据集或实验结果。

### 模式 17：literature_review（文献综述）

- 模式标识：literature_review
- 描述：基于证据矩阵综合主题、差异和研究缺口，并保存为项目章节。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 写作项目 ID |
| section | string | 否 | "Related Work" | 保存章节名 |
| instruction | string | 否 | - | 综述结构、语言和侧重点 |

#### 输出说明

使用项目 LLM 配置并只允许已有引用键；摘要之外的断言标记 NEEDS_EVIDENCE，同时返回逐句引用检查。

### 模式 18：claim_check（论断引用检查）

- 模式标识：claim_check
- 描述：逐句检查引用缺口、未知引用和待补证据标记。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| project_id | string | 是 | - | 写作项目 ID |
| section | string | 否 | - | 可选，仅检查指定章节 |

#### 输出说明

返回 cited、citation_gap、unknown_citation 或 placeholder。该检查验证引用存在性，不声称引用内容必然支持论断。

### 模式 19：compose（自然语言撰写）

- 模式标识：compose
- 描述：用一句普通描述创建或继续写作项目，并自动选择提纲、章节起草或基于证据的文献综述。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| request | string | 是 | - | 例如“用中文撰写关于时间序列预测的文献综述”或“为《研究标题》生成英文提纲” |
| project_id | string | 否 | - | 已有项目 ID；省略时自动创建项目 |
| title | string | 否 | 自动识别 | 无法从描述识别标题时可由 Agent 补充 |
| paper_ids | list | 否 | [] | 论文跟踪结果 ID |
| report_ids | list | 否 | [] | 论文跟踪摘要 ID |
| job_ids | list | 否 | [] | 代码复现任务 ID |
| material_paths | list | 否 | [] | 本机材料文件路径 |

#### 输出说明

返回生成内容、项目 ID、修订号和对自然语言的解释。涉及文献综述时必须有已跟踪论文证据；LLM 未配置时明确返回配置需求，不生成占位内容。

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
python -m venv --system-site-packages resources/tools/implementations/paper-writing/.venv
resources/tools/implementations/paper-writing/.venv/bin/python -m pip install -r resources/tools/implementations/paper-writing/requirements.txt
```

Windows 本地测试可使用统一测试环境运行 ToolExecutor；服务器独立 .venv 使用现有执行器约定的 bin/python，不修改执行器。共享依赖固定 `openreview-py==2.6.2`。GITHUB_TOKEN 为可选 GitHub 令牌；SOTABAND_ACADEMIC_DATA 指定工具数据根目录（默认 data/academic）；当前用户由平台 SOTABAND_USER_ID 上下文决定。SOTABAND_ACADEMIC_HTTP_PROXY=direct 只控制工具自有 HTTP 客户端；OpenReview 官方 SDK 遵循进程的代理与 NO_PROXY 环境。凭据不写进目录、计划或规格文件。

## 5. 运行机制

### 5.1 执行流程

读取 mode、校验参数、调用独立实现、将业务记录写入用户隔离的 SQLite/目录、返回统一结果。跨调用状态保存在磁盘，不依赖工具进程内存。仅新增工具及注册适配，不改平台、前端或其他工具。

### 5.2 错误处理

参数错误返回 INVALID_INPUT；缺少记录为 NOT_FOUND；并发冲突为 BUSY 或 REVISION_CONFLICT；来源异常、限流和依赖缺失显式报告。不会以示例数据替代真实返回。私有记录只在当前用户空间读取。

LLM 默认使用 SOTABand 现有模型配置，也支持本工具临时或按用户持久配置，见 5.3；人工编辑、引用检查和导出不要求 LLM。论文摘要不等同于全文证据；未提供的数值、实验和结论应标记 [NEEDS_EVIDENCE]。材料按页提取并保存校验值；扫描 PDF 无文本时返回 OCR_REQUIRED，不假装完成阅读。单文件不超过 30 MB 或 50 万字符；发送给模型的证据上下文最多 10 万字符，截断时显式返回 context_truncated。引用存在性校验不保证每个论断被原文支持，最终稿须人工审阅。

引用格式为 Pandoc [@key]，key 由 read 返回。实验结果是附加时的快照，任务完成后可 attach 同一 job_id 刷新。导出附带 references.bib 和 provenance.json；bundle 是便于迁移的 ZIP。docx、latex 需要 Pandoc（可安装 pypandoc_binary），PDF 需要 XeLaTeX 和 Noto Serif CJK SC 字体。缺少转换器会返回 DEPENDENCY_MISSING，不以其他文件冒充 PDF。LaTeX 默认通用论文样式，投稿前需按目标会议的官方模板排版。

导出目录不互相覆盖。版本冲突返回 REVISION_CONFLICT，重新 read 后再修改。不能以工具生成文字代替文献核实、作者审校或科研诚信审查。格式依据：[Pandoc 引用与转换](https://pandoc.org/MANUAL.html)。

### 5.3 配置与凭据

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

result = ToolExecutor.execute_sync(
    "paper-writing",
    {"mode": "create", "title": "研究论文", "paper_ids": [], "job_ids": []},
    timeout=300,
)
```

离线测试：`python -m pytest resources/tools/_academic/tests -q`。联网和实际 GPU 测试需显式启用环境变量，测试输出与离线模拟结果分开；GPU fixture 仅为执行链路验证，不是论文基准实验。

验证记录（2026-10-09）：引用校验、版本冲突、恢复、Markdown/BibTeX/ZIP 及 Pandoc DOCX/LaTeX 转换已测试；真实 GPU 指标可进入文稿。使用 ACL Anthology 的 5 篇 2025 年 LLM 论文及当前 DeepSeek 配置完成真实文献综述生成，逐句检查同时识别出 cited、citation_gap 和 placeholder。XeLaTeX 仍未安装，PDF 编译只验证了依赖缺失时的明确错误，不作成功声明。

联网验证：`ACADEMIC_LIVE_HTTP=1`；实际 GPU 验证：`ACADEMIC_LIVE_GPU=1`（Windows 使用对应的 $env 设置）。本工具范围的测试位于 resources/tools/_academic/tests，不会自动执行其他工具。离线运行默认跳过这些外部资源测试。

真实随机验证（2026-10-30）：随机种子 3107555793，从刚下载的 CVPR/JMLR 2024 记录中抽取 4 篇真实论文，再读取各官方详情页摘要，调用平台配置的 DeepSeek 生成中英文综述。4 次引用检查及 16 次 Markdown/LaTeX/DOCX/BibTeX 导出通过。另通过平台执行器复测真实 compose 请求：修正“生成提纲，不要编造实验结果”误选实验章节的问题。使用 `python -m resources.tools._academic.tests.live_writing_audit --corpus <真实检索验收输出目录> --count 4` 可再随机验证。摘要级证据只支持摘要内陈述；输出中的 NEEDS_EVIDENCE 保留给用户审阅，不把引用格式通过视为全文结论已核实。

补充轮次（2026-10-30）：随机种子 1690929693，新增抽取 RepViT、Crossmodal Feature Mapping、DiaLoc、Universal Hand Model、GLOW、BA-SAM 六篇真实论文，六次真实 LLM 写作与 24 次导出全部完成；两轮累计十篇论文与 40 次导出。当前机器真实 PDF 导出仍返回 DEPENDENCY_MISSING（xelatex 不存在），不计入通过数。自然语言提纲修复后的真实模型复测也已完成。

恢复验证（2026-10-30）：本轮 28 项相关检查通过（含发现问题后的定向复测），新增恢复测试不模拟网络响应或模型输出。验证实际缺失凭据、缺失 XeLaTeX、用户隔离、恢复互斥、拒绝重复训练提交、12 组随机路径的跨进程真实失败与修复；通过平台执行器实际查询 GitHub，并使用真实模型完成修复配置后的生成，确认未重复创建写作项目。DBLP 实测仍为 HTTP 202 / Anubis 验证页，验证的是错误准确呈现，不算来源恢复成功。OpenReview 未配置真实账号，未宣称登录验收通过。GitHub 实测发现旧 microsoft/LightGBM 搜索名称被拒绝而仓库接口指向 lightgbm-org/LightGBM；现保留 HTTP 422 官方校验信息，提示修正条件，不自动修改查询。

对话可以输入 `重试 recovery-实际编号，配置文件："后端机器上的文件路径"`，仅传文件路径，无须手写 JSON 参数。也可在同一后端环境、同一用户下使用终端恢复入口：

```bash
python -m resources.tools._academic.recovery --tool writing --request "查看 recovery-实际编号"
python -m resources.tools._academic.recovery --tool writing --request "重试 recovery-实际编号"
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
| 1.3.4 | 2026-10-30 | 增加普通语言补充信息、稳定序号选择、计划与文稿导出对话输入；修正单年份解释和资源数量解析。 |
| 1.3.3 | 2026-10-30 | 恢复任务绑定文稿版本，执行入口再次检查；并发查看与完成产物引用可用。 |
| 1.3.2 | 2026-10-30 | 增加错误可读说明、用户隔离恢复记录、纯文本显式重试；保留已创建订阅与写作项目，禁止重放训练提交。 |
| 1.3.1 | 2026-10-30 | 向模型传递项目语言、研究问题和目标会议；否定约束不再被误判为目标章节。 |
| 1.3.0 | 2026-10-10 | 增加自然语言写作编排，并接收论文跟踪与复现实验的标准交接 |
| 1.2.0 | 2026-10-09 | 支持导入论文跟踪摘要、证据矩阵、文献综述和逐句引用缺口检查 |
| 1.1.0 | 2026-09-28 | OpenReview 改用官方 V2 Python 客户端，增加官方登录凭据配置与错误分类 |
| 1.0.0 | 2026-09-28 | 独立学术工具、标准接口、持久状态和证据记录 |

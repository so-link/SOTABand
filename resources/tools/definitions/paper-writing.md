---
id: paper-writing
name: 论文撰写
version: 1.3.0
type: function
language: python
status: active
created: 2026-09-28
modes: 19
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

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.3.0 | 2026-10-10 | 增加自然语言写作编排，并接收论文跟踪与复现实验的标准交接 |
| 1.2.0 | 2026-10-09 | 支持导入论文跟踪摘要、证据矩阵、文献综述和逐句引用缺口检查 |
| 1.1.0 | 2026-09-28 | OpenReview 改用官方 V2 Python 客户端，增加官方登录凭据配置与错误分类 |
| 1.0.0 | 2026-09-28 | 独立学术工具、标准接口、持久状态和证据记录 |

---
id: paper-tracker
name: 顶会顶刊论文跟踪
version: 2.2.4
type: function
language: python
status: active
created: 2026-09-28
modes: 22
---

# 顶会顶刊论文跟踪

## 1. 功能概述

11.3.3 按具体研究方向持续检索顶会顶刊论文，提供方向画像、相关性评分、作者与排除词过滤、多源证据、订阅、增量摘要、论文详情和相似论文。独立实现于本项目，不依赖旧 ResearchAgent 的代码路径、服务或镜像。

## 2. 模式定义

入口为 `execute(**kwargs) -> dict`。mode 缺省为 search。列表参数支持数组、JSON 数组字符串或逗号分隔文本。路径均指部署机器本地路径。

### 模式 1：track（跟踪）

- 模式标识：track
- 描述：用一句自然语言描述方向、会刊和时间，立即检索具体论文并创建持续订阅。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| request | string | 是 | - | 例如“持续跟踪 ICML、NeurIPS 2025 年以来的时间序列异常检测论文，每周更新” |
| result_limit | int | 否 | 20 | 对话表格展示前 1-100 篇；完整匹配仍写入订阅和摘要 |

#### 输出说明

返回按相关性排序的具体论文、命中方向词、来源健康状态、订阅 ID 和首份增量摘要，并提供批量写作交接和前十篇论文的代码查找交接。对话仅返回 result_limit 篇并附 total_matches；订阅与报告在响应中返回计数摘要，不重复传输全部论文 ID 和快照，完整匹配仍保存在持久订阅报告中。组合方向会保留具体限定，例如“大语言模型推理”不会退化为宽泛的“大语言模型”。若订阅创建成功但首次检索全源失败，返回 retrieval_status=failed 和未知论文数量，不报告为“0 篇”。

### 模式 2：catalog（会议期刊目录）

- 模式标识：catalog
- 描述：查看当前版本、分级、领域及来源。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| venues | list | 否 | [] | 会议简称或目录 ID 数组；空值选择目录范围内全部 |
| scope | string | 否 | "all" | all / ccf_a |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 3：import_catalog（导入目录）

- 模式标识：import_catalog
- 描述：导入含 version、source_url、venues 的 JSON 目录，仅影响当前用户。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| file_path | string | 是 | - | 前序操作返回的 file_path |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 4：search（论文检索）

- 模式标识：search
- 描述：查询来源并保存证据，返回来源健康状态和变更 ID。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| venues | list | 否 | [] | 会议简称或目录 ID 数组；空值选择目录范围内全部 |
| query | string | 否 | "" | 传给来源的宽召回检索词；省略时从 topic 选择 |
| topic | string | 否 | "" | 具体研究方向，中英文均可，也支持自定义方向 |
| keywords | list | 否 | [] | 额外纳入的关键词或短语 |
| exclude_keywords | list | 否 | [] | 标题或摘要命中即排除的词 |
| authors | list | 否 | [] | 作者姓名过滤 |
| min_relevance | int | 否 | 1 | 最低相关性分数，0-100 |
| scope | string | 否 | "all" | all / ccf_a |
| source | string | 否 | "auto" | auto / dblp / crossref / openreview / cvf / anthology / jmlr |
| max_pages | int | 否 | 3 | 分页源每个会议每年最多 1-100 页，未取完会标记 truncated |
| since | string | 否 | - | YYYY-MM-DD，默认最近 30 天 |
| until | string | 否 | - | YYYY-MM-DD，默认今天 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 5：subscribe（创建订阅）

- 模式标识：subscribe
- 描述：保存可定时执行的论文检索配置。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| name | string | 是 | - | 前序操作返回的 name |
| venues | list | 否 | [] | 会议简称或目录 ID 数组；空值选择目录范围内全部 |
| query | string | 否 | "" | 关键词 |
| topic | string | 否 | "" | 持续跟踪的研究方向 |
| keywords | list | 否 | [] | 额外纳入词 |
| exclude_keywords | list | 否 | [] | 排除词 |
| authors | list | 否 | [] | 作者姓名过滤 |
| min_relevance | int | 否 | 1 | 最低相关性分数，0-100 |
| scope | string | 否 | "all" | all / ccf_a |
| source | string | 否 | "auto" | auto / dblp / crossref / openreview / cvf / anthology / jmlr |
| max_pages | int | 否 | 3 | 分页源每个会议每年最多 1-100 页，未取完会标记 truncated |
| interval_hours | int | 否 | 24 | 同步间隔 1-8760 小时 |
| lookback_days | int | 否 | 365 | 滚动重扫窗口，覆盖晚索引论文 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 6：subscriptions（订阅列表）

- 模式标识：subscriptions
- 描述：查看当前用户的订阅。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 7：update_subscription（暂停或恢复订阅）

- 模式标识：update_subscription
- 描述：改变订阅启用状态，不会被正在执行的同步覆盖。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| subscription_id | string | 是 | - | 前序操作返回的 subscription_id |
| action | string | 否 | "pause" | pause / resume |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 8：unsubscribe（删除订阅）

- 模式标识：unsubscribe
- 描述：移除订阅，已存论文和摘要保留。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| subscription_id | string | 是 | - | 前序操作返回的 subscription_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 9：sync（同步订阅）

- 模式标识：sync
- 描述：手动执行一次订阅并保存增量摘要。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| subscription_id | string | 是 | - | 前序操作返回的 subscription_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 10：digest（查看摘要）

- 模式标识：digest
- 描述：查看某次同步中的变化，省略 ID 为全部已存论文。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| report_id | string | 否 | - | 同步返回的 digest ID |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 11：export（导出论文）

- 模式标识：export
- 描述：导出论文与来源记录为 JSON。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| report_id | string | 否 | - | 同步返回的 digest ID |

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

### 模式 18：diagnose_sources（来源访问诊断）

- 模式标识：diagnose_sources
- 描述：探测固定官方接口，区分网络、鉴权、限流与站点验证，不绕过访问限制。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| sources | list | 否 | ["dblp","openreview"] | dblp / openreview / github / crossref |
| routing | string | 否 | "configured" | configured 使用当前 HTTP 路由；direct 不使用环境代理，仅用于允许直连的部署网络 |

#### 输出说明

返回 probes、routing、all_reachable；每项包含来源、接口、UTC 时间、状态、error_code 及脱敏 evidence。DBLP/GitHub/Crossref 记录安全的 HTTP 证据；OpenReview 记录官方 SDK、API 版本、认证方式及经脱敏的官方异常分类，不返回响应正文、用户名、密码、token 或请求头。OpenReview 登录成功后同时探测 notes 和 groups；登录失败时返回 official_client_login。诊断成功不表示所有数据源畅通，必须检查各探针结果。

### 模式 19：topics（方向画像）

- 模式标识：topics
- 描述：查看内置研究方向、同义词和推荐会刊；未列出的方向仍可自定义。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回方向 ID、中英文别名、检索词和推荐会刊。

### 模式 20：paper_detail（论文详情）

- 模式标识：paper_detail
- 描述：查看一篇已索引论文的摘要、作者、方向命中、来源记录和评级依据。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| paper_id | string | 是 | - | search、track 或 digest 返回的论文 ID |

#### 输出说明

返回完整持久记录及多来源证据。

### 模式 21：related（相似论文）

- 模式标识：related
- 描述：在当前用户已索引论文中按标题和摘要词汇查找相似论文。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| paper_id | string | 是 | - | 作为相似度基准的论文 ID |
| limit | int | 否 | 10 | 返回 1-100 篇 |

#### 输出说明

返回相似度、共享词和论文元数据；这是词汇相似度，不冒充引文网络或语义向量结论。

### 模式 22：recover（错误处理与重试）

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
python -m venv --system-site-packages resources/tools/implementations/paper-tracker/.venv
resources/tools/implementations/paper-tracker/.venv/bin/python -m pip install -r resources/tools/implementations/paper-tracker/requirements.txt
```

Windows 本地测试可使用统一测试环境运行 ToolExecutor；服务器独立 .venv 使用现有执行器约定的 bin/python，不修改执行器。共享依赖固定 `openreview-py==2.6.2`。GITHUB_TOKEN 为可选 GitHub 令牌；SOTABAND_ACADEMIC_DATA 指定工具数据根目录（默认 data/academic）；当前用户由平台 SOTABAND_USER_ID 上下文决定。SOTABAND_ACADEMIC_HTTP_PROXY=direct 只控制工具自有 HTTP 客户端；OpenReview 官方 SDK 遵循进程的代理与 NO_PROXY 环境。凭据不写进目录、计划或规格文件。

## 5. 运行机制

### 5.1 执行流程

读取 mode、校验参数、调用独立实现、将业务记录写入用户隔离的 SQLite/目录、返回统一结果。跨调用状态保存在磁盘，不依赖工具进程内存。仅新增工具及注册适配，不改平台、前端或其他工具。

### 5.2 错误处理

参数错误返回 INVALID_INPUT；缺少记录为 NOT_FOUND；并发冲突为 BUSY 或 REVISION_CONFLICT；来源异常、限流和依赖缺失显式报告。不会以示例数据替代真实返回。私有记录只在当前用户空间读取。自定义目录每项须含 id、name、kind（conference / journal）、rank（A / B / C / null），并按来源配置 dblp_key、issn 或 openreview_id。

目录依据 [2026 年第七版 CCF 正式目录的高校发布件](https://kyc.lsu.edu.cn/2026/0518/c1772a367789/page.htm)，内置 95 个 A 类条目和 16 个领域重要条目，覆盖 10 个领域，保留 PDF 页码、文件 SHA256 和来源链接。以当前目录分类，不自动追溯不同发表年份的历史评级。原目录名称按原文提取，支持简称、别名与 ID 检索；如 USENIX ATC、ACM SIGOPS ATC 共用一个条目。

auto 优先使用已配置的 ACL Anthology、CVF、JMLR、Crossref 或 OpenReview，其余使用 DBLP；选定来源失败时直接保留失败状态，不自动换源。CVF 仅支持 CVPR/ICCV，其他会议不可强制当作 CVF 检索。ACL Anthology 直接读取其官方仓库发布的原始 XML，不使用 GitHub REST API 配额，并在取得完整年度集合后按标题和摘要联合评分。仅提供 query 时按字面检索，显式 topic 或自然语言跟踪才使用方向扩展；多模态推理要求多模态与推理两组概念均命中。ACL Anthology XML 与 CVF 年度索引按当前用户缓存 6 小时，以缩短重复检索时间。DBLP 提供广泛索引，但索引和访问可能滞后或受限。Crossref 优先使用目录中的 issn，否则仅在官方期刊接口精确匹配到唯一标题时解析 ISSN；有歧义则要求显式配置。OpenReview 使用官方 `openreview-py` 的 API V2 `OpenReviewClient`，以 username/password 或 session token 登录，调用 `get_notes(content={"venueid": ...})`；需要 openreview_id（支持 {year}）并仅接收匹配的接受论文。来源目录 URL 不代表任意地址抓取授权。

仅正式长文、常规会议论文或期刊论文可赋予有效 ccf_rank；Findings、Workshop、短文等单独标注，DBLP 未确认轨道时仅返回 catalog_rank、ccf_rank=null。按 DOI 优先去重；缺 DOI 时要求同年同标题及作者或会议证据。source_records 保留每个来源内容，重复同步不会因来源切换反复报新论文。

日期精度由来源决定：DBLP、OpenReview、CVF、Anthology 和 JMLR 列表主要按年过滤并返回 date_precision=year，不冒充精确到日。分页上限命中返回 truncated；任一来源失败或截断标记 partial。全源失败的检索报错；订阅仍保存带错误来源的部分报告，不能把它视为没有新论文。ACL Anthology 请求范围中的未来年度集合尚未发布时跳过该年；全部年度均不存在时才报告来源错误。

不提供会议截稿日期猜测、站点登录绕过或付费全文下载。定时同步须运行本工具 worker，启动方式及服务器数据迁移见 GPU代码复现规格。PDF 目录更新用内置解析器生成后人工检查再 import_catalog：

```bash
python -m resources.tools._academic.catalog /path/ccf.pdf --source-url https://example.edu/ccf.pdf --version ccf-new --output /path/catalog.json
```

来源依据：[ACL Anthology 数据](https://github.com/acl-org/acl-anthology/tree/master/data/xml)、[CVF Open Access](https://openaccess.thecvf.com/)、[JMLR](https://www.jmlr.org/)、[Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[OpenReview](https://docs.openreview.net/)。

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
    "paper-tracker", {"mode": "catalog", "scope": "ccf_a"}, timeout=300
)
```

离线测试：`python -m pytest resources/tools/_academic/tests -q`。联网和实际 GPU 测试需显式启用环境变量，测试输出与离线模拟结果分开；GPU fixture 仅为执行链路验证，不是论文基准实验。

验证记录（2026-10-10）：ACL Anthology 官方原始 XML 联网测试通过；“ACL 2025 + 大语言模型推理”从 1,966 篇筛出 387 篇，“EMNLP 2025 + 大语言模型推理”从 2,086 篇筛出 547 篇，均返回具体标题和 Anthology 页面，来源覆盖 `partial=false`。此前 CVF、JMLR 官方来源测试及“CVPR 2025 + 多模态”124 篇结果仍有效。OpenReview 已锁定官方 `openreview-py==2.6.2`，登录、分页、内容校验、凭据脱敏及 SDK 错误分类均有测试；真实账号认证仍取决于部署用户配置。DBLP 的历史受限原因及匿名探测证据保留如下，不将来源失败解释为零论文。

### 6.1 访问限制的实际证据

以下是迁移官方 SDK 之前的匿名 HTTP 基线：2026-09-28 07:44 UTC，在本机以 direct 路由、未带认证令牌、使用 SOTABand-Academic/1.0 客户端标识探测。它只说明匿名直连结果，不代表官方 SDK 登录后的结果：

| 来源/接口 | 实际响应 | 能确定的原因 |
|-----------|----------|--------------|
| DBLP /search/publ/api | HTTP 200，text/html；Anubis 验证页面，标题 Making sure you're not a bot!；前次原始探测显示 v1.27.0 | 请求被反自动化验证拦截，未得到 JSON；不能解释为缺少 API Key |
| OpenReview /notes | HTTP 403，JSON name=ChallengeRequiredError；限流上限 180，剩余 179 | 明确要求完成站点挑战；当前响应不支持“额度耗尽”结论，也不证明缺少 token |
| OpenReview /groups（同主机对照） | HTTP 200，有效 JSON；剩余 178 | 同一主机可连通，不是整个站点网络不通 |

此前环境代理曾失效，但本次默认路由也能收到 OpenReview HTTP 403，直连结果相同，因此不能把当前限制归因于代理离线。只确定服务返回的验证要求，无法从这些响应推断具体触发规则、地域限制或 IP 封禁。来源诊断不保存含客户端 IP、挑战 token 的原始网页。

当前处理方式遵循 [OpenReview 官方安装与登录文档](https://docs.openreview.net/getting-started/using-the-api/installing-and-instantiating-the-python-client)：安装 `openreview-py`，构造 API V2 `OpenReviewClient(baseurl, username, password)`；也支持该官方客户端提供的已有 session token。用户可在本机终端输入登录信息，再运行 diagnose_sources 验证。若账号启用 MFA，官方 SDK 在交互式 TTY 中按其支持的 TOTP、邮件验证码或 passkey 流程处理；后台 worker 无法交互时返回 MFA_REQUIRED。仍不能承诺登录后站点挑战一定放行，不复制验证 Cookie、不破解挑战、不更换身份绕过限制。GitHub 认证遵循 [GitHub REST 官方文档](https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api)，只使用 token，不接受账号密码。DBLP 当前没有待填的 API Key；可用会议官方源及 Crossref 等继续获取覆盖范围内的数据，并保留 partial 标记。

错误码区分 CONFIG_REQUIRED、AUTH_REQUIRED、AUTH_FAILED、MFA_REQUIRED、CHALLENGE_REQUIRED、ACCESS_DENIED、RATE_LIMITED、PROXY_ERROR、TLS_ERROR、DNS_ERROR、NETWORK_TIMEOUT、SOURCE_FORMAT_ERROR。普通 403 只说明拒绝访问，不臆测后台原因。检索全源失败和订阅报告都会保留各来源 error_code、retryable、details，不再仅留下笼统 SOURCE_UNAVAILABLE。

重新诊断（应在实际 GPU 服务器的服务账户及网络下执行）：

```python
ToolExecutor.execute_sync(
    "paper-tracker",
    {"mode": "diagnose_sources", "sources": ["dblp", "openreview"], "routing": "configured"},
    timeout=180,
)
```

联网验证：`ACADEMIC_LIVE_HTTP=1`；实际 GPU 验证：`ACADEMIC_LIVE_GPU=1`（Windows 使用对应的 $env 设置）。本工具范围的测试位于 resources/tools/_academic/tests，不会自动执行其他工具。离线运行默认跳过这些外部资源测试。

真实随机验证（2026-10-30）：`python -m resources.tools._academic.tests.live_random_audit --samples 80` 从官方源下载 ACL/EMNLP/NAACL/CVPR/JMLR 2024 及 ICCV 2023，共 8,433 篇论文；随机种子 1059320658，480 次真实标题检索全部通过。前一轮另有 250 次完成，EMNLP 整批达到 150 秒截止，未记为通过；修正精确查询语义后，第二轮 EMNLP 80 次检索约 33 秒完成。时间包含下载及数据库处理，不代表独立网络性能基准。真实平台自然语言请求“CVPR 2024 多模态推理、每周更新”得到 13 篇匹配论文，周期 168 小时。DBLP 返回 HTTP 202 站点验证页，OpenReview 因未配置官方登录凭据报错；ECCV+CVF 明确 SOURCE_NOT_CONFIGURED，这三项均未记为检索成功。验收脚本保留实际响应、随机种子和失败记录，不使用替代来源或模拟数据。

恢复验证（2026-10-30）：本轮 28 项相关检查通过（含发现问题后的定向复测），新增恢复测试不模拟网络响应或模型输出。验证实际缺失凭据、缺失 XeLaTeX、用户隔离、恢复互斥、拒绝重复训练提交、12 组随机路径的跨进程真实失败与修复；通过平台执行器实际查询 GitHub，并使用真实模型完成修复配置后的生成，确认未重复创建写作项目。DBLP 实测仍为 HTTP 202 / Anubis 验证页，验证的是错误准确呈现，不算来源恢复成功。OpenReview 未配置真实账号，未宣称登录验收通过。GitHub 实测发现旧 microsoft/LightGBM 搜索名称被拒绝而仓库接口指向 lightgbm-org/LightGBM；现保留 HTTP 422 官方校验信息，提示修正条件，不自动修改查询。

对话可以输入 `重试 recovery-实际编号，配置文件："后端机器上的文件路径"`，仅传文件路径，无须手写 JSON 参数。也可在同一后端环境、同一用户下使用终端恢复入口：

```bash
python -m resources.tools._academic.recovery --tool papers --request "查看 recovery-实际编号"
python -m resources.tools._academic.recovery --tool papers --request "重试 recovery-实际编号"
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
| 2.2.4 | 2026-10-30 | 增加普通语言补充信息、稳定序号选择、计划与文稿导出对话输入；修正单年份解释和资源数量解析。 |
| 2.2.3 | 2026-10-30 | 恢复状态可并发查看；新年份官方语料随机检索与混合来源失败实测。 |
| 2.2.2 | 2026-10-30 | 增加错误可读说明、用户隔离恢复记录、纯文本显式重试；保留已创建订阅与写作项目，禁止重放训练提交。 |
| 2.2.1 | 2026-10-30 | 移除自动换源，拒绝不支持的 CVF 会议；区分精确 query 与主题扩展，保留多模态推理组合条件。 |
| 2.2.0 | 2026-10-10 | ACL Anthology 改用官方原始 XML，保留大语言模型推理组合方向，并区分订阅创建与首次检索失败 |
| 2.1.0 | 2026-10-10 | 检索与跟踪结果增加到代码查找、论文写作的标准交接数据 |
| 2.0.0 | 2026-10-09 | 增加具体方向画像、自然语言持续跟踪、相关性评分、论文详情和相似论文 |
| 1.1.0 | 2026-09-28 | OpenReview 改用官方 V2 Python 客户端，增加官方登录凭据配置与错误分类 |
| 1.0.0 | 2026-09-28 | 独立学术工具、标准接口、持久状态和证据记录 |

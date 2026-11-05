---
id: paper-tracker
name: 顶会顶刊论文跟踪
version: 2.1.0
type: function
language: python
status: active
created: 2026-09-28
modes: 21
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

#### 输出说明

返回按相关性排序的具体论文、命中方向词、来源健康状态、订阅 ID 和首份增量摘要，并提供批量写作交接和前十篇论文的代码查找交接。

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

auto 优先使用已配置的 ACL Anthology、CVF、JMLR、Crossref 或 OpenReview，其余使用 DBLP；主源失败时可回退 DBLP，并保留失败状态。DBLP 提供广泛索引，但索引和访问可能滞后或受限。Crossref 优先使用目录中的 issn，否则仅在官方期刊接口精确匹配到唯一标题时解析 ISSN；有歧义则要求显式配置。OpenReview 使用官方 `openreview-py` 的 API V2 `OpenReviewClient`，以 username/password 或 session token 登录，调用 `get_notes(content={"venueid": ...})`；需要 openreview_id（支持 {year}）并仅接收匹配的接受论文。来源目录 URL 不代表任意地址抓取授权。

仅正式长文、常规会议论文或期刊论文可赋予有效 ccf_rank；Findings、Workshop、短文等单独标注，DBLP 未确认轨道时仅返回 catalog_rank、ccf_rank=null。按 DOI 优先去重；缺 DOI 时要求同年同标题及作者或会议证据。source_records 保留每个来源内容，重复同步不会因来源切换反复报新论文。

日期精度由来源决定：DBLP、OpenReview、CVF、Anthology 和 JMLR 列表主要按年过滤并返回 date_precision=year，不冒充精确到日。分页上限命中返回 truncated；任一来源失败或截断标记 partial。全源失败的检索报错；订阅仍保存带错误来源的部分报告，不能把它视为没有新论文。年度列表尚未发布会明确报来源错误。

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

验证记录（2026-10-09）：ACL Anthology、CVF、JMLR 官方来源及 GitHub REST 联网测试通过；以“CVPR 2025 + 多模态”真实检索到 124 篇具体论文，相关性命中与排序正常，来源覆盖 `partial=false`。OpenReview 已锁定官方 `openreview-py==2.6.2`，登录、分页、内容校验、凭据脱敏及 SDK 错误分类均有测试；真实账号认证仍取决于部署用户配置。DBLP 的历史受限原因及匿名探测证据保留如下，不将来源失败解释为零论文。

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

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 2.1.0 | 2026-10-10 | 检索与跟踪结果增加到代码查找、论文写作的标准交接数据 |
| 2.0.0 | 2026-10-09 | 增加具体方向画像、自然语言持续跟踪、相关性评分、论文详情和相似论文 |
| 1.1.0 | 2026-09-28 | OpenReview 改用官方 V2 Python 客户端，增加官方登录凭据配置与错误分类 |
| 1.0.0 | 2026-09-28 | 独立学术工具、标准接口、持久状态和证据记录 |

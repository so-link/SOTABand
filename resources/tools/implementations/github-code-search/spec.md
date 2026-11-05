---
id: github-code-search
name: GitHub学术代码查找
version: 1.3.0
type: function
language: python
status: active
created: 2026-09-28
modes: 10
---

# GitHub学术代码查找

## 1. 功能概述

11.3.1 检索 GitHub 仓库和代码，固定 commit，记录许可证、依赖、入口和论文关联证据。 独立实现于本项目，不依赖旧 ResearchAgent 的代码路径、服务或镜像。

## 2. 模式定义

入口为 `execute(**kwargs) -> dict`。mode 缺省为 search。列表参数支持数组、JSON 数组字符串或逗号分隔文本。路径均指部署机器本地路径。

### 模式 1：search（仓库检索）

- 模式标识：search
- 描述：使用 GitHub 原生搜索表达式检索仓库。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| query | string | 是 | - | 前序操作返回的 query |
| limit | int | 否 | 10 | 每页 1-100 条 |
| page | int | 否 | 1 | 第 1-10 页 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 2：inspect（固定仓库快照）

- 模式标识：inspect
- 描述：解析提交、README、文件树、许可证和论文关联。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| repository | string | 是 | - | 前序操作返回的 repository |
| ref | string | 否 | - | 分支、标签或 SHA，省略为默认分支 |
| paper_url | string | 否 | "" | 用于检查 README 中的论文链接 |
| paper_title | string | 否 | "" | 用于检查 README 中的论文标题证据 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 3：search_code（代码检索）

- 模式标识：search_code
- 描述：有 snapshot_id 时搜索固定版本文件路径，否则使用需授权的 GitHub 内容搜索。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| query | string | 是 | - | 前序操作返回的 query |
| snapshot_id | string | 否 | - | inspect 返回的快照 ID |
| repository | string | 否 | - | 可选 owner/name 仓库范围 |
| limit | int | 否 | 10 | 每页 1-100 条 |
| page | int | 否 | 1 | 第 1-10 页 |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 4：export（导出快照）

- 模式标识：export
- 描述：导出可用于复现计划的 JSON 仓库证据。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| snapshot_id | string | 是 | - | 前序操作返回的 snapshot_id |

#### 输出说明

返回标准输出；数据字段保留实体 ID、状态和来源证据。

### 模式 5：paper_code（按论文找代码）

- 模式标识：paper_code
- 描述：按论文标题检索候选仓库，固定提交并评估论文关联和复现完整度。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| paper_title | string | 是 | - | 论文完整标题 |
| paper_url | string | 否 | "" | DOI、OpenReview 或论文主页链接，用于 README 证据核验 |
| keywords | string | 否 | "" | 方法名、作者名或框架等补充检索词 |
| limit | int | 否 | 10 | 返回候选数，1-30 |
| inspect_limit | int | 否 | 5 | 固定并深度检查的前若干候选，1-limit |

#### 输出说明

按综合分数返回仓库、固定提交、论文关联证据、许可证、入口、依赖和复现缺口。README 声明仅作为仓库自述证据，不等同于独立作者认证。

### 模式 6：compare（比较候选代码）

- 模式标识：compare
- 描述：比较多个固定仓库快照的入口、依赖、许可证和复现准备度。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| snapshot_ids | list | 是 | - | 至少两个 inspect 或 paper_code 返回的快照 ID |

#### 输出说明

按复现完整度排序，返回固定提交、关系证据、候选入口和缺失项。

### 模式 7：config_status（配置状态）

- 模式标识：config_status
- 描述：查看凭据是否配置及来源，不发起付费模型请求。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| config_file | string | 否 | - | 可选临时配置 JSON 路径，仅预览不保存 |

#### 输出说明

返回 llm/github/openreview 配置状态、缺少字段、持久路径及本机配置命令；不返回密钥。

### 模式 8：configure（持久配置）

- 模式标识：configure
- 描述：从本机文件替换当前用户的学术工具配置，不改平台设置。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| config_file | string | 是 | - | 部署机上的受保护 UTF-8 JSON 文件路径 |

#### 输出说明

保存成功后返回脱敏配置状态；失败保留原配置。

### 模式 9：config_reset（清除私有配置）

- 模式标识：config_reset
- 描述：删除当前用户的学术工具配置，恢复环境或平台默认。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

无额外参数。

#### 输出说明

返回删除后的配置状态；环境变量和平台 Key 不会被删除。

### 模式 10：find（自然语言查找）

- 模式标识：find
- 描述：用一句普通描述查找论文代码或检查指定 GitHub 仓库，并生成可直接交给复现工具的固定快照。

#### 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| request | string | 是 | - | 例如“查找论文《Are Transformers Effective for Time Series Forecasting?》的官方代码”或“检查仓库 https://github.com/cure-lab/LTSF-Linear” |
| ref | string | 否 | - | 检查指定仓库时可选的分支、标签或 SHA |

#### 输出说明

论文请求返回候选排名、推荐固定快照和 `reproduction_handoff`；仓库请求直接固定 commit 并返回同样的复现交接对象。交接对象是 Agent 调用参数，用户无需输入 JSON。

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
python -m venv --system-site-packages resources/tools/implementations/github-code-search/.venv
resources/tools/implementations/github-code-search/.venv/bin/python -m pip install -r resources/tools/implementations/github-code-search/requirements.txt
```

Windows 本地测试可使用统一测试环境运行 ToolExecutor；服务器独立 .venv 使用现有执行器约定的 bin/python，不修改执行器。共享依赖固定 `openreview-py==2.6.2`。GITHUB_TOKEN 为可选 GitHub 令牌；SOTABAND_ACADEMIC_DATA 指定工具数据根目录（默认 data/academic）；当前用户由平台 SOTABAND_USER_ID 上下文决定。SOTABAND_ACADEMIC_HTTP_PROXY=direct 只控制工具自有 HTTP 客户端；OpenReview 官方 SDK 遵循进程的代理与 NO_PROXY 环境。凭据不写进目录、计划或规格文件。

## 5. 运行机制

### 5.1 执行流程

读取 mode、校验参数、调用独立实现、将业务记录写入用户隔离的 SQLite/目录、返回统一结果。跨调用状态保存在磁盘，不依赖工具进程内存。仅新增工具及注册适配，不改平台、前端或其他工具。

### 5.2 错误处理

参数错误返回 INVALID_INPUT；缺少记录为 NOT_FOUND；并发冲突为 BUSY 或 REVISION_CONFLICT；来源异常、限流和依赖缺失显式报告。不会以示例数据替代真实返回。私有记录只在当前用户空间读取。

仓库检索支持 GitHub 的 language、stars、pushed 等原生过滤表达式。官方身份仅记录 unknown、paper_linked、author_claimed，不以星数或 README 自称证明作者身份。tree_truncated 表示文件树不完整；远程内容搜索针对默认分支，不代表固定 commit 内容。GITHUB_TOKEN 仅用于请求头，不写入快照。

接口依据：[GitHub REST Search](https://docs.github.com/en/rest/search/search)。

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
    "github-code-search",
    {"mode": "search", "query": "pytorch examples language:Python", "limit": 5},
    timeout=300,
)
```

离线测试：`python -m pytest resources/tools/_academic/tests -q`。联网和实际 GPU 测试需显式启用环境变量，测试输出与离线模拟结果分开；GPU fixture 仅为执行链路验证，不是论文基准实验。

验证记录（2026-10-09）：真实 GitHub REST 仓库检索、提交快照和固定 SHA 已通过；新增的论文标题证据、入口/依赖/命令识别及复现完整度排序通过离线边界测试。远程代码内容搜索仍需 GITHUB_TOKEN；README 声明只记为仓库自述证据，不当成作者认证。

联网验证：`ACADEMIC_LIVE_HTTP=1`；实际 GPU 验证：`ACADEMIC_LIVE_GPU=1`（Windows 使用对应的 $env 设置）。本工具范围的测试位于 resources/tools/_academic/tests，不会自动执行其他工具。离线运行默认跳过这些外部资源测试。

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.3.0 | 2026-10-10 | 增加自然语言论文/仓库查找、推荐固定快照和复现工具标准交接 |
| 1.2.0 | 2026-10-09 | 增加按论文找代码、固定候选比较及仓库复现完整度评估 |
| 1.1.0 | 2026-09-28 | OpenReview 改用官方 V2 Python 客户端，增加官方登录凭据配置与错误分类 |
| 1.0.0 | 2026-09-28 | 独立学术工具、标准接口、持久状态和证据记录 |

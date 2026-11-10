# Data 资源管理设计

> 数据集为基本单元，MD 规范描述 → 注册 → 发现 → 处理 → 预览，全生命周期管理。

---

## 一、核心概念

### 1.1 数据集 (Dataset)

数据集是数据空间管理的基本单元。每个数据集：

- 对应一个**物理目录**，可包含多个文件和子目录
- 拥有一份**标准 MD 描述文档**，说明数据内容、格式、来源、使用方法
- 在资源注册中心**注册登记**，可通过发现器检索
- 支持**工具处理**和**可视化预览**

### 1.2 与 Agent / Tool 管理的一致性

| 概念 | Agent | Tool | Data |
|------|-------|------|------|
| 基本单元 | Agent | Tool | Dataset |
| 规格文档 | agent-design.md | tool-design.md | 本文档 |
| 注册表 | registry.json | registry.json | registry.json |
| 存储位置 | resources/agents/ | resources/tools/ | resources/data/ |
| MD 生成 | LLM 从 NL 生成 | LLM 从 NL 生成 | LLM 从 NL + 数据检测生成 |
| 发现 | AgentDiscoverer | ToolDiscoverer | DataDiscoverer |
| [+] 按钮 | Agent 空间 | 工具空间 | 工作区间 / 数据空间 |

---

## 二、数据集 MD 规范描述文档

### 2.1 标准模板

```markdown
---
id: eeg-subject-001
name: EEG 受试者 001 数据集
version: 1.0.0
type: time-series
status: active
created: 2026-06-30
---

# EEG 受试者 001 数据集

## 1. 数据集概述

64 通道 EEG 脑电数据，采集自健康受试者，采样率 256Hz，时长 10 分钟。
包含静息态和视觉刺激态两段记录。适用于脑电信号分析、异常检测、频谱分析等研究。

## 2. 目录结构

```
eeg-subject-001/
├── resting_state.edf       # 静息态 EEG 数据 (5min)
├── visual_stimulus.edf     # 视觉刺激态 EEG 数据 (5min)
├── channels.tsv            # 通道标注文件
├── events.tsv              # 事件标记文件
└── metadata.json           # 采集参数元数据
```

## 3. 数据格式

| 文件 | 格式 | 大小 | 说明 |
|------|------|------|------|
| resting_state.edf | EDF | 23MB | 64通道 × 256Hz × 5min |
| visual_stimulus.edf | EDF | 23MB | 64通道 × 256Hz × 5min |
| channels.tsv | TSV | 2KB | 通道名称和坐标 |
| events.tsv | TSV | 1KB | 事件类型和时间戳 |
| metadata.json | JSON | 1KB | 采集参数 |

## 4. 数据 Schema

| 通道 | 类型 | 单位 | 采样率 | 说明 |
|------|------|------|--------|------|
| C3 | EEG | μV | 256Hz | 中央区 |
| C4 | EEG | μV | 256Hz | 中央区 |
| F3 | EEG | μV | 256Hz | 额叶区 |
| ... | ... | ... | ... | ... |

## 5. 数据来源

- **采集设备**: Neuroscan SynAmps2
- **采集日期**: 2026-05-15
- **受试者**: 健康成人，年龄 25，男性
- **伦理审批**: IRB-2026-001

## 6. 使用场景

| 场景 | 推荐工具 | 说明 |
|------|---------|------|
| 异常信号检测 | eeg_anomaly_detector | 检测异常波形 |
| 频谱分析 | spectral_analyzer | 功率谱密度分析 |
| 滤波预处理 | eeg_bandpass_filter | 带通滤波去噪 |

## 7. 质量评估

- **信号质量**: 95/100
- **完整性**: 100%（无缺失通道）
- **信噪比**: 14.2 dB
- **标注质量**: 已人工校验

## 8. 访问权限

- **级别**: public
- **允许操作**: read, process, derive

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-06-30 | 初始版本 |
```

### 2.2 NL → MD 生成流程

```
用户在 workspace 中选择/上传数据目录
    ↓
用户输入描述: "64通道EEG数据，256Hz，5min静息态+5min刺激态"
    ↓
系统扫描目录: 自动检测文件列表、格式、大小
    ↓
调用 LLM（`.env` 配置的服务商，OpenAI 兼容协议）:
  System: "你是一个数据集规格文档生成器..."（严禁编造未提供的信息）
  User: "[文件列表（含大小/格式/描述）] + [用户描述]"
    ↓
生成标准 MD 文档（另有一次独立 LLM 调用生成 3-5 个中文标签）→ 展示给用户审阅编辑
    ↓
用户确认 → 注册到数据空间
```

### 2.3 MD 生成 Prompt（实际实现）

`app/api/routes/data_routes.py` 中的 `SPEC_PROMPT`，核心约束是**严禁编造**：

```
你是一个数据集规格文档生成器。你必须严格根据用户提供的数据文件信息
来生成文档，**严禁编造不存在的信息**。如果某个信息没有提供，填写"待补充"。

规则：
1. 只使用用户提供的文件信息，不要假设或编造任何数据属性
2. 文件描述(description)直接用作数据格式表的说明列
3. dataset-id 用小写英文+连字符，从用户描述中提取关键词
4. type 从实际文件格式推断: png/jpg→image, csv→tabular, edf→timeseries, txt/md→text
5. 只输出 Markdown，不要额外解释
```

注册成功后还会有一次后台 LLM 调用为数据集自动匹配预览工具（`_match_preview_tool`），结果以 `preview_tool` 字段写回注册表，供预览时直接使用。

---

## 三、数据集注册与存储

### 3.1 存储结构（双层资源根）

数据集遵循全局的**双层资源根**模型（`core/resource/paths.py`）：读取时按私有根→公共系统根合并，写入只进私有根。**数据文件不做复制**——注册只登记元数据，`data_path` 指向数据的原始位置（注册时 `sourceFiles` resolve 后必须仍在当前用户目录内，任意绝对路径会被 400 拒绝，防止任意路径枚举）。

```
SB-USER/100/data/                     # 公共系统根（系统用户目录，内置数据集经种子脚本导入）
├── registry.json
├── definitions/
└── datasets/                         # 仅为兼容保留的目录，注册流程不向其复制数据

<USER_HOME>/<uid>/data/               # 私有根（USER_HOME 默认 /root/SB-USER，SOTABAND_HOME 可覆盖）
├── registry.json                     # 新注册条目一律写这里
└── definitions/{id}.md               # MD 规范文档写入私有根

resources/data/                       # 出厂种子包（随仓库分发，运行期不解析，仅供种子导入参照）
```

注册表条目字段：`id / name / version / type / status / spec_path / data_path / file_count / total_size / formats / tags / quality_score / created_at / owner / visibility / storage`。其中 `owner`（当前用户，不接受调用方传入）、`visibility`（一律 `private`）、`storage`（`local`/`remote`）由注册中心强制注入。

### 3.2 注册流程

```
用户确认 MD 文档 → 点击 [注册数据集]
    ↓
POST /api/data/register（文件保持在原位置，不复制）
    ↓
DataRegistry.register(dataset_info)
    ├── 1. data_path 取上传文件列表中第一个文件的父目录（绝对路径）
    ├── 2. 统计实际 file_count / total_size / formats
    ├── 3. 注入 owner / visibility=private / storage 字段
    ├── 4. MD 保存到私有根 definitions/{id}.md（末尾追加数据集目录信息）
    ├── 5. 条目写入私有根 registry.json（读时与内置根合并，同 id 私有覆盖内置）
    └── 6. 后台异步匹配预览工具（LLM），结果缓存进注册表
```

由于 `data_path` 指向注册时的机器路径，从他人环境复制项目时会带来「注册记录在本机无数据」的幽灵条目。`check_availability` / `is_present_locally` 逐个校验目录真实存在且有文件；`/api/data/list` 默认只返回**属于当前用户**且可见的条目（无 owner 的历史条目按「本机有数据视为本人」启发式平滑迁移，可用 `POST /api/data/claim-local` 一次性认领）。

---

## 四、用户添加数据集的完整流程

```
用户在左侧工作区间 → 右键或 [+] 按钮 → [添加数据集]
    ↓
主面板切换到 → 数据集编辑器视图

┌─ Step 1: 选择数据 ────────────────────────────────────────┐
│  选择或上传数据目录                                          │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 📁 EEG数据/                                         │  │
│  │ ├── resting_state.edf (23MB)                        │  │
│  │ ├── visual_stimulus.edf (23MB)                      │  │
│  │ ├── channels.tsv (2KB)                              │  │
│  │ └── events.tsv (1KB)                                │  │
│  └─────────────────────────────────────────────────────┘  │
│  系统自动扫描: 4 个文件, 2 个 EDF, 2 个 TSV                 │
└──────────────────────────────────────────────────────────────┘
    ↓
┌─ Step 2: 描述数据集 ──────────────────────────────────────┐
│  用自然语言描述数据集内容                                     │
│  "64通道EEG脑电数据，256Hz，包含静息态和视觉刺激态..."         │
│                                      [生成 MD 文档 →]       │
└──────────────────────────────────────────────────────────────┘
    ↓
┌─ Step 3: 审阅 MD 规范文档 ────────────────────────────────┐
│  展示生成的 MD 文档，用户可编辑修改                            │
│                                      [← 返回] [→ 注册]      │
└──────────────────────────────────────────────────────────────┘
    ↓
┌─ Step 4: 注册完成 ────────────────────────────────────────┐
│  ✅ "eeg-subject-001" 已注册                               │
│  📁 数据位置: 原始目录（注册不复制文件，data_path 指向原路径）│
│  📋 MD 文档: 私有资源根 data/definitions/eeg-subject-001.md │
│                                                          │
│  该数据集现在:                                             │
│  - 在工作区间文件树中可见                                    │
│  - 在资源空间的数据空间中可检索                               │
│  - 可被工具处理和预览                                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 五、数据集展示

### 5.1 工作区间显示

注册后的数据集出现在左侧工作区间文件树中：

```
📁 工作区间
├── 📁 EEG数据/
│   ├── 📁 eeg-subject-001/        ← 已注册数据集
│   │   ├── 📄 resting_state.edf
│   │   ├── 📄 visual_stimulus.edf
│   │   ├── 📄 channels.tsv
│   │   └── 📄 events.tsv
│   └── ...未分类文件...
├── 📁 图像/
└── 📁 结果/
```

### 5.2 资源空间数据空间显示

```
📦 资源空间
├── 🗄 数据空间                      [+]
│   ├── 📊 eeg-subject-001          ← 已注册数据集
│   ├── 📊 mri-brain-003
│   └── 📊 fmri-resting-001
├── 🔧 工具空间
├── 🧠 模型空间
...
```

---

## 六、数据集处理（核心流程）

### 6.1 处理流程

```
用户点击数据集（工作区间或数据空间中的数据集）
    ↓
在对话界面输入框自动附加该数据集
    ↓
用户描述处理需求: "对这个EEG数据做带通滤波，0.5-45Hz"
    ↓
POST /api/chat/send { content: "...", attachments: [eeg-subject-001] }
    ↓
交互Agent 收到请求 → 分析需求: "带通滤波" + 数据集 "eeg-subject-001"
    ↓
┌─ 调用 ToolDiscoverer 查找匹配工具 ───────────────────────┐
│                                                          │
│  LLM: "用户需要对 EEG 数据做带通滤波，                       │
│  匹配可用工具..."                                          │
│                                                          │
│  检索工具空间:                                             │
│  ├── eeg_bandpass_filter  (输入: EDF, 输出: EDF) ✓       │
│  ├── eeg_anomaly_detector (输入: EDF, 输出: JSON)        │
│  └── spectral_analyzer    (输入: EDF, 输出: PNG)         │
│                                                          │
│  匹配结果: 1 个工具可用                                    │
└──────────────────────────────────────────────────────────┘
    ↓
┌─ 有匹配工具 ──────────────────────────────────────────────┐
│                                                          │
│  交互Agent 回复:                                          │
│  "找到匹配工具 eeg_bandpass_filter v2.1.0，是否执行？"      │
│                                                          │
│  ┌─ 内联卡片: 工具匹配 ─────────────────────────────┐    │
│  │ ✅ eeg_bandpass_filter v2.1.0                    │    │
│  │    输入格式: EDF                                 │    │
│  │    输出格式: EDF                                 │    │
│  │    匹配度: 96%                                   │    │
│  │    [执行处理] [查看详情]                           │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  用户点击 [执行处理]                                       │
│      ↓                                                   │
│  POST /api/tool/eeg_bandpass_filter/execute               │
│  {                                                        │
│    "params": {                                            │
│      "data_path": "resources/data/datasets/...",         │
│      "low_freq": 0.5,                                     │
│      "high_freq": 45.0                                    │
│    }                                                      │
│  }                                                        │
│      ↓                                                   │
│  工具执行...                                              │
│      ↓                                                   │
│  ┌─ 内联卡片: 处理结果 ─────────────────────────────┐    │
│  │ ✅ 处理完成                                      │    │
│  │    输入: resting_state.edf (23MB)               │    │
│  │    输出: resting_state_filtered.edf (22MB)      │    │
│  │    耗时: 3.2s                                   │    │
│  │    输出文件已保存到工作区间                        │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
└──────────────────────────────────────────────────────────┘

┌─ 无匹配工具 ──────────────────────────────────────────────┐
│                                                          │
│  交互Agent 回复:                                          │
│  "目前没有匹配的工具可以处理此需求。是否创建新工具？"         │
│                                                          │
│  ┌─ 内联卡片: 工具缺失 ─────────────────────────────┐    │
│  │ ⚠️ 未找到匹配工具                                │    │
│  │    需求: EEG 带通滤波                            │    │
│  │    建议: 自动生成工具代码                          │    │
│  │    [→ 创建新工具]                                │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  用户点击 [→ 创建新工具]                                   │
│      ↓                                                   │
│  跳转到工具编辑器视图                                       │
│      ↓                                                   │
│  Step 1 描述需求输入框中已自动填充:                          │
│  "对EEG数据做带通滤波，输入EDF文件，输出滤波后的EDF文件，     │
│   支持可配置的低频和高频截止频率"                              │
│      ↓                                                   │
│  用户确认 → 生成 MD → 生成代码 → 沙箱测试 → 注册            │
│      ↓                                                   │
│  注册完成后自动跳回对话界面                                  │
│      ↓                                                   │
│  交互Agent: "工具 eeg_bandpass_filter 已创建，是否立即使用？" │
│      ↓                                                   │
│  用户点击 [执行处理] → 用刚创建的工具处理数据集               │
│      ↓                                                   │
│  显示处理结果                                              │
└──────────────────────────────────────────────────────────┘
```

### 6.2 工具匹配逻辑

工具匹配由 `POST /api/data/match-tools` 承担（`data_routes.py`）：

```
POST /api/data/match-tools {dataset_id, request}
    │
    ├── 1. 读取数据集 MD 规范前 1000 字作为数据集上下文
    ├── 2. 取注册表中 status=active 的全部工具（id/name/type/tags）
    ├── 3. 调用 LLM 进行语义匹配:
    │       "用户需求 + 数据集信息 + 可用工具列表
    │        请判断哪些工具可以处理此需求，返回工具ID列表"
    ├── 4. 返回 {matches: [tool-id...], reason, total_tools}
    └── 5. 如果没有匹配，返回空列表 → 触发工具创建流程
```

（`ToolDiscoverer` 另提供 `search / get_by_tags / get_by_input_format / match_by_capability` 等结构化检索方法，供 Agent 与路由复用。）

---

## 七、数据集预览

### 7.1 预览流程

```
用户点击数据集（工作区间或数据空间）
    ↓
主面板切换到 → 数据预览视图 (DataPreviewView)
    ↓
┌─ GET /api/data/{id}/preview ─────────────────────────────┐
│                                                          │
│  1. 从 MD 规范"数据格式"表格解析文件列表，                  │
│     表格无内容时回退为扫描实际目录                           │
│  2. 预览工具: 优先读注册表中缓存的 preview_tool             │
│     （注册时由后台 LLM 匹配写入），无缓存则跳过，            │
│     避免每次点击都调 LLM；接口本身不自动执行预览工具          │
│  3. 返回 {dataset, spec_md, files, preview_tool,          │
│          has_preview_tool}                                │
└──────────────────────────────────────────────────────────┘
    ↓
┌─ 有预览工具 ──────────────────────────────────────────────┐
│                                                          │
│  前端按 preview_tool 呈现可视化结果展示在主面板:              │
│                                                          │
│  ┌─ 数据预览: eeg-subject-001 ─────────────────────────┐ │
│  │                                                      │ │
│  │  📊 概览: 64通道 × 256Hz × 10min │ 质量: 95/100      │ │
│  │                                                      │ │
│  │  [波形图] [频谱图] [地形图] [原始数据]                  │ │
│  │                                                      │ │
│  │  ┌──────────────────────────────────────────────┐   │ │
│  │  │  ╭╮  ╭╮    ← 通道 C3                        │   │ │
│  │  │  ││  ││  ╭╮                                  │   │ │
│  │  │  │╰──││──││───                               │   │ │
│  │  │  │   ││  ││                                  │   │ │
│  │  │  ╰───╯╰──╯╰──                               │   │ │
│  │  │  ────────────────────                        │   │ │
│  │  │  0s    5s    10s   15s   20s                │   │ │
│  │  └──────────────────────────────────────────────┘   │ │
│  │                                                      │ │
│  │  文件列表:                                           │ │
│  │  📄 resting_state.edf      23MB  [预览] [下载]       │ │
│  │  📄 visual_stimulus.edf    23MB  [预览] [下载]       │ │
│  │  📄 channels.tsv           2KB   [查看] [下载]       │ │
│  │  📄 events.tsv             1KB   [查看] [下载]       │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                          │
└──────────────────────────────────────────────────────────┘

┌─ 无预览工具 ──────────────────────────────────────────────┐
│                                                          │
│  展示数据集的 MD 描述信息:                                  │
│                                                          │
│  ┌─ 数据集信息: eeg-subject-001 ───────────────────────┐ │
│  │                                                      │ │
│  │  EEG 受试者 001 数据集                                │ │
│  │  64 通道 EEG 脑电数据，采样率 256Hz，时长 10 分钟      │ │
│  │                                                      │ │
│  │  文件列表:                                           │ │
│  │  📄 resting_state.edf      23MB  EDF                │ │
│  │  📄 visual_stimulus.edf    23MB  EDF                │ │
│  │  📄 channels.tsv           2KB   TSV                │ │
│  │  📄 events.tsv             1KB   TSV                │ │
│  │                                                      │ │
│  │  暂无专用预览工具                                      │ │
│  │  [创建预览工具]                                       │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 八、API 端点

`app/api/routes/data_routes.py` 实际暴露的端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/data/scan-directory` | POST | 扫描目录（限当前用户目录内，空串/根路径只扫 uploads/plots），返回文件列表、总数、格式与逐个文件大小 |
| `/api/data/generate-spec` | POST (SSE) | NL + 文件信息（含描述）→ MD 数据集描述 + 3-5 个标签（流式） |
| `/api/data/register` | POST | 注册数据集（文件保持原位置不复制；后台匹配预览工具） |
| `/api/data/repository` | GET | 数据集仓库列表，含标签统计与缩略图/首句预览 |
| `/api/data/list` | GET | 按归属隔离列出数据集（`available_only` / `include_others` 可选） |
| `/api/data/claim-local` | POST | 把本机有数据、缺归属标记的历史条目认领给当前用户 |
| `/api/data/{id}` | GET | 数据集详情（含 MD 文档） |
| `/api/data/{id}/archive` | GET | 把数据集目录打包为 zip 下载 |
| `/api/data/{id}/files` | GET | 数据集文件列表（目录不存在时明确报错，不静默返回空） |
| `/api/data/{id}/table` | GET | 读取数据集内表格文件（csv/xlsx/parquet/tsv）返回统一表格结构（`?path=&limit=`，防目录穿越） |
| `/api/data/match-tools` | POST | 根据数据集与需求，LLM 语义匹配可用工具 |
| `/api/data/{id}/preview` | GET | 预览：解析文件列表 + 读缓存的 `preview_tool`（不自动执行） |
| `/api/data/search/find` | GET | 按关键词/标签搜索数据集（`?q=&tags=`） |
| `/api/data/{id}` | DELETE | 删除数据集（移除注册表条目与 MD 文档；仅托管目录内的数据副本会被删除） |

> `DELETE` 只删除位于托管目录（私有根 `data/datasets/`）之内的数据副本；`data_path` 指向托管目录之外的源文件（如上传时的原位置）时仅注销条目、不动源文件。响应以 `files_removed` 标记文件是否真的被删除，并兜底拒绝删除数据根目录本身。

---

## 九、归属与可见性

数据集是**私有**资源（数据文件在本机，按 owner 隔离），与工具/Agent 的「共享代码资产」不同：

- 注册时强制注入 `owner = 当前用户`（`core/user_context.get_current_user_id`），不接受调用方伪造；`visibility = private`；`storage = local/remote`。
- 读取时 `is_visible` 叠加可见性：`public` 全员可见，`private` 需归属匹配。当前所有数据集均为 `private`，行为等同纯归属判定；未来上云把 `visibility` 设为 `public` 即可共享，无需改判定逻辑。
- `/api/data/list` 默认只返回当前用户可见的条目，并逐个用 `check_availability` 校验本机是否真的有数据（`available` / `file_count_actual`），避免展示「一运行就报路径不存在」的幽灵数据集。
- 历史无 `owner` 条目走「本机有数据即视为本人」的平滑迁移启发式，可用 `/api/data/claim-local` 一次性打补丁，此后走精确 owner 匹配。

---

## 十、相关实现文件

后端：

```
core/resource/registry/data_registry.py       # 数据注册中心（双层根读合并/写私有、归属与可用性校验）
core/resource/discoverer/data_discoverer.py    # 数据发现器（search / get_by_tags / get_by_format）
core/resource/paths.py                         # 双层资源根：私有根 + 公共系统根解析
core/user_context.py                           # 当前用户标识、可见性/归属判定、storage 探测
app/api/schemas/data_schemas.py                # 数据 Pydantic 模型
app/api/routes/data_routes.py                  # 数据 API 路由
```

前端：

```
frontend/src/components/center-panel/DatasetEditorView.tsx       # 数据集编辑器（生成 MD → 注册）
frontend/src/components/center-panel/DataPreviewView.tsx         # 数据预览视图
frontend/src/components/center-panel/DatasetRepositoryView.tsx   # 数据仓库视图（标签统计/缩略图）
frontend/src/components/center-panel/DataTablePreview.tsx        # 表格类预览
frontend/src/stores/dataset-editor-store.ts                      # 编辑器状态
frontend/src/stores/dataset-repository-store.ts                  # 仓库视图状态
frontend/src/stores/workspace-dataset-store.ts                   # 工作区间数据集状态
frontend/src/services/api/data.ts                                # Data API 服务
```

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
调用 DeepSeek v4:
  System: "你是一个数据集规格文档生成器..."
  User: "[目录结构] + [用户描述]"
    ↓
生成标准 MD 文档 → 展示给用户审阅编辑
    ↓
用户确认 → 注册到数据空间
```

### 2.3 MD 生成 Prompt

```
你是一个数据集规格文档生成器。根据用户描述和目录扫描结果，
生成标准化的 Dataset MD 描述文档。

输入信息：
- 目录路径: {path}
- 文件列表: {files}
- 用户描述: {description}

规则：
1. 自动检测文件格式并填充数据格式表
2. 从文件名推断数据类型（EEG/图像/表格/文本）
3. 如果用户未提及，合理推断使用场景和推荐工具
4. dataset-id 使用小写字母+连字符
5. 只输出 Markdown
```

---

## 三、数据集注册与存储

### 3.1 存储结构

```
resources/data/
├── registry.json                     # 数据注册表
├── definitions/                      # MD 规范文档
│   ├── eeg-subject-001.md
│   └── ...
└── datasets/                         # 实际数据文件（软链接或复制）
    └── eeg-subject-001/
        ├── resting_state.edf
        ├── visual_stimulus.edf
        └── ...
```

### 3.2 注册流程

```
用户确认 MD 文档 → 点击 [注册数据集]
    ↓
DataRegistry.register(dataset_info)
    ├── 1. 校验 MD 规范文档完整性
    ├── 2. 复制/链接数据目录到 resources/data/datasets/{id}/
    ├── 3. 保存 MD 到 resources/data/definitions/{id}.md
    ├── 4. 写入 resources/data/registry.json
    ├── 5. 建立索引: 类型/格式/标签/大小
    └── 6. 发布注册事件
```

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
│  📁 数据位置: resources/data/datasets/eeg-subject-001/     │
│  📋 MD 文档: resources/data/definitions/eeg-subject-001.md │
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
│  deepseek v4: "用户需要对 EEG 数据做带通滤波，               │
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

```
ToolDiscoverer.match_for_dataset(dataset_md, user_request)
    │
    ├── 1. 从 dataset MD 提取数据格式 (EDF, CSV, PNG...)
    ├── 2. 从 user_request 提取处理意图 (滤波, 分析, 转换...)
    ├── 3. 调用 LLM 进行语义匹配:
    │       "数据集格式: EDF, 用户需求: 带通滤波
    │        可用工具列表: [...]
    │        请判断哪些工具可以处理此需求，返回工具ID列表"
    ├── 4. 返回匹配的工具列表（按匹配置信度排序）
    └── 5. 如果没有匹配，返回空列表 → 触发工具创建流程
```

---

## 七、数据集预览

### 7.1 预览流程

```
用户点击数据集（工作区间或数据空间）
    ↓
主面板切换到 → 数据预览视图
    ↓
┌─ 系统调用 LLM 匹配预览工具 ──────────────────────────────┐
│                                                          │
│  deepseek v4: "数据集类型: EDF 时间序列，64通道，          │
│  推荐预览方式: 波形图 + 频谱图"                             │
│                                                          │
│  检索工具空间中适合预览的工具:                               │
│  ├── eeg_waveform_viewer   → 波形图                      │
│  ├── eeg_spectrum_viewer   → 频谱图                      │
│  └── eeg_topomap_viewer    → 地形图                      │
│                                                          │
│  匹配结果: 3 个预览工具可用                                 │
└──────────────────────────────────────────────────────────┘
    ↓
┌─ 有预览工具 ──────────────────────────────────────────────┐
│                                                          │
│  自动执行预览工具，可视化结果展示在主面板:                     │
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

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/data/scan-directory` | POST | 扫描目录，返回文件列表和格式检测结果 |
| `/api/data/generate-spec` | POST | NL + 目录扫描结果 → MD 数据集描述 |
| `/api/data/register` | POST | 注册数据集 |
| `/api/data/list` | GET | 列出所有已注册数据集 |
| `/api/data/{id}` | GET | 数据集详情（含 MD） |
| `/api/data/{id}/files` | GET | 数据集文件列表 |
| `/api/data/{id}/preview` | GET | 预览数据集（自动匹配预览工具） |
| `/api/data/{id}/process` | POST | 处理数据集（指定工具和参数） |
| `/api/data/match-tools` | POST | 根据数据集和需求匹配可用工具 |
| `/api/data/search` | GET | 搜索数据集 |

---

## 九、前端改动

### 新增文件

```
frontend/src/components/center-panel/DatasetEditorView.tsx  # 数据集编辑器
frontend/src/components/center-panel/DataPreviewView.tsx     # 数据集预览（重写）
frontend/src/stores/dataset-editor-store.ts                 # 编辑器状态管理
frontend/src/services/api/data.ts                           # Data API 服务
```

### 修改文件

```
frontend/src/stores/ui-store.ts              # + 'dataset-editor'
frontend/src/components/center-panel/CenterPanel.tsx  # + 数据集编辑器路由
frontend/src/components/left-panel/WorkspaceFileTree.tsx  # + 数据集右键菜单/标记
frontend/src/components/left-panel/ResourceBrowser.tsx    # + 数据空间 [+] 按钮 + 加载真实数据
frontend/src/stores/chat-store.ts             # 支持数据集附件
frontend/src/stores/resource-store.ts         # + fetchDatasetsFromApi
```

### 后端新增文件

```
core/resource/registry/data_registry.py       # 数据注册中心
core/resource/discoverer/data_discoverer.py    # 数据发现器
app/api/schemas/data_schemas.py               # 数据 Pydantic 模型
app/api/routes/data_routes.py                 # 数据 API 路由
resources/data/registry.json                  # 数据注册表
resources/data/definitions/                   # MD 规范文档
resources/data/datasets/                      # 实际数据存储
```

---

## 十、实现顺序

```
Phase 1: 数据注册基础设施
  ├── 1. core/resource/registry/data_registry.py
  ├── 2. core/resource/discoverer/data_discoverer.py
  ├── 3. app/api/schemas/data_schemas.py
  ├── 4. app/api/routes/data_routes.py (scan, generate-spec, register, list, get)
  └── 5. resources/data/ 目录初始化

Phase 2: 数据集编辑器
  ├── 6. frontend DatasetEditorView + store + api service
  ├── 7. WorkspaceFileTree 右键菜单 / [+] 按钮
  └── 8. 端到端: 选择目录 → 描述 → 生成 MD → 注册

Phase 3: 数据集处理
  ├── 9. POST /api/data/match-tools (语义匹配)
  ├── 10. 交互Agent 集成: 附加数据集 → 匹配工具 → 执行/跳转
  ├── 11. 无工具时自动跳转工具编辑器 + 预填需求
  └── 12. 端到端: 点击数据集 → 对话描述 → 工具处理 → 显示结果

Phase 4: 数据集预览
  ├── 13. GET /api/data/{id}/preview (自动匹配预览工具)
  ├── 14. DataPreviewView 重写 (集成预览工具调用)
  └── 15. 无预览工具时展示 MD 描述信息
```

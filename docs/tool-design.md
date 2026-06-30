# Tool 完整生命周期设计

> 工具的规格化、生成、测试、注册、发现、调用全流程。
> 所有工具必须 **规范描述 → 注册登记 → 发现使用**。

---

## 一、核心理念

工具（Tool）是系统中最小的可执行单元。每个工具必须：
1. 有标准化的 MD 规范描述文档
2. 在资源注册中心登记
3. 通过资源发现器检索
4. 被 Agent 或编排流程调用

与 Agent 的区别：Agent 是独立进程，有自主行为；Tool 是被动调用，无状态，同步/异步执行。

---

## 二、用户添加工具的完整流程

```
用户在左侧资源空间 → 工具空间 → [+] 按钮
    ↓
主面板切换到 → 工具编辑器视图
    ↓
┌─ Step 1: 描述需求 ──────────────────────────────────────────┐
│  自然语言描述工具功能                                          │
│  调用 DeepSeek v4 → 生成标准化 MD 工具描述文档                  │
└──────────────────────────────────────────────────────────────┘
    ↓ 用户审阅编辑
┌─ Step 2: 审阅 MD 规范文档 ──────────────────────────────────┐
│  展示生成的 MD 文档，用户可编辑修改                             │
│  确认后 → 调用 LLM 生成工具代码 + 构造测试数据                  │
└──────────────────────────────────────────────────────────────┘
    ↓
┌─ Step 3: 代码预览 + 沙箱测试 ───────────────────────────────┐
│  左: 生成的 Python 代码                                       │
│  右: 沙箱测试结果（语法/依赖/接口/功能）                        │
│  用户可修改代码重新测试                                        │
└──────────────────────────────────────────────────────────────┘
    ↓ 用户批准
┌─ Step 4: 注册发布 ─────────────────────────────────────────┐
│  ToolRegistry.register(tool)                                │
│  工具出现在资源空间 → 工具空间列表中                           │
│  可被 Agent 发现和调用                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、工具 MD 规范描述文档

### 3.1 标准模板

```markdown
---
id: {tool-id}
name: {工具名称}
version: 0.1.0
type: function           # function | script | api-wrapper
language: python          # python | shell | javascript
status: active
created: {日期}
---

# {工具名称}

## 1. 功能概述

{用自然语言描述工具做什么}

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| data_path | string | 是 | — | 输入数据文件路径 |
| channels | list[int] | 否 | [] | 要处理的通道列表 |
| low_freq | float | 否 | 0.5 | 低通截止频率(Hz) |
| high_freq | float | 否 | 45.0 | 高通截止频率(Hz) |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| output_path | string | 处理后数据保存路径 |
| metadata | dict | 处理参数和统计信息 |
| status | string | 执行状态 (success/failed) |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| numpy | >=1.24 | 数值计算 |
| scipy | >=1.10 | 信号处理 |
| mne | >=1.5 | EEG 数据读写 |

## 5. 运行机制

### 5.1 执行流程

1. 读取输入数据文件
2. 校验输入参数
3. 执行核心处理逻辑
4. 保存处理结果
5. 返回输出信息

### 5.2 性能指标

- 预期执行时间: < 5s (64通道, 10min数据)
- 内存占用: < 500MB

### 5.3 错误处理

- 文件不存在 → 返回错误信息
- 参数无效 → 返回验证错误
- 处理异常 → 捕获并返回详细错误

## 6. 测试用例

### 6.1 测试数据描述

```json
{
  "input": {
    "data_path": "/test/fixtures/sample_eeg.edf",
    "channels": [0, 1, 2],
    "low_freq": 1.0,
    "high_freq": 40.0
  },
  "expected": {
    "status": "success",
    "output_exists": true
  }
}
```

### 6.2 边界条件

- 空数据文件
- 无效通道索引
- 极端频率参数

## 7. 调用示例

```python
from core.tool.registry import ToolRegistry

tool = ToolRegistry.get("eeg-bandpass-filter")
result = tool.execute(
    data_path="/data/subj01.edf",
    channels=[0, 1, 2],
    low_freq=0.5,
    high_freq=45.0,
)
# result: {"output_path": "/data/subj01_filtered.edf", "status": "success"}
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | {日期} | 初始版本 |
```

### 3.2 NL → MD 转换 Prompt

```
你是一个工具规格文档生成器。根据用户描述生成标准化的 Tool MD 规范文档。

规则：
1. tool-id 使用小写字母+连字符，如 "eeg-bandpass-filter"
2. type 根据描述推断：function(函数) / script(脚本) / api-wrapper(API封装)
3. 合理推断输入参数和输出格式
4. 建议合适的依赖库和版本
5. 生成至少 2 个测试用例（正常+边界）
6. 标注不确定的字段
```

---

## 四、工具代码生成

### 4.1 生成器 `core/resource/builder/tool_builder.py`

```python
class ToolCodeBuilder(BaseBuilder):
    """从 Tool MD 规范文档生成 Python 代码"""

    async def build(self, spec: dict) -> str:
        # 策略：
        # 1. 标准类型（eeg/信号处理/数据转换等）→ 模板生成
        # 2. 自定义类型 → LLM 生成
        ...

    async def generate_test_data(self, spec: dict) -> dict:
        """根据输入规范构造测试数据"""
        # 分析 spec 中的输入参数
        # 为每个参数生成合理测试值
        # 生成 mock 文件（如需要）
        ...

    async def dry_run(self, code: str, test_input: dict) -> dict:
        """沙箱执行并返回结果"""
        # 1. 语法检查 (ast.parse)
        # 2. 依赖检查 (import)
        # 3. 接口检查 (函数签名)
        # 4. 功能测试 (用测试数据执行)
        ...
```

### 4.2 生成代码模板

```python
"""Tool: {tool_name} — {description}"""

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def execute(**kwargs) -> dict[str, Any]:
    """工具执行入口

    Args:
{param_docs}

    Returns:
        dict: {output_docs}
    """
    # 1. 参数校验
{param_validation}

    # 2. 核心逻辑
{core_logic}

    # 3. 返回结果
    return {return_statement}
```

### 4.3 测试数据生成策略

| 参数类型 | 正常值 | 边界值 | 异常值 |
|---------|--------|--------|--------|
| string(path) | `/data/sample.edf` | 空字符串 `""` | `/nonexistent/file` |
| int | `3` | `0`, `999999` | `-1`, `"abc"` |
| float | `1.0` | `0.0`, `1e10` | `-1.0`, `NaN` |
| list[int] | `[0,1,2]` | `[]`, `[999]` | `[-1]`, `["a"]` |
| bool | `True` | `False` | `"yes"` |

---

## 五、工具注册（资源空间）

### 5.1 注册流程

```
ToolRegistry.register(tool_spec, tool_code)
    │
    ├── 1. 校验 MD 规范文档完整性（8 段落）
    ├── 2. 校验代码（语法 + 接口）
    ├── 3. 分配唯一 Tool ID
    ├── 4. 保存代码到 resources/tools/implementations/{tool-id}/
    ├── 5. 写入 resources/tools/registry.json
    ├── 6. 建立索引: 类型/标签/输入输出格式
    └── 7. 发布注册事件
```

### 5.2 存储结构

```
resources/tools/
├── registry.json                       # 工具注册表
│   [{
│     "id": "eeg-bandpass-filter",
│     "name": "EEG 带通滤波器",
│     "version": "0.1.0",
│     "type": "function",
│     "language": "python",
│     "status": "active",
│     "spec_path": "definitions/eeg-bandpass-filter.md",
│     "impl_path": "implementations/eeg-bandpass-filter/",
│     "input_schema": {...},
│     "output_schema": {...},
│     "tags": ["EEG", "滤波", "预处理"],
│     "usage_count": 0,
│     "created_at": "..."
│   }]
│
├── definitions/                        # MD 规范文档
│   ├── eeg-bandpass-filter.md
│   └── ...
│
└── implementations/                    # 工具代码
    └── eeg-bandpass-filter/
        ├── tool.py                     # 工具实现
        ├── spec.md                     # MD 规范文档副本
        ├── config.yaml                 # 依赖配置
        └── tests/                      # 测试用例
            ├── test_normal.json
            ├── test_boundary.json
            └── test_error.json
```

---

## 六、工具发现

### 6.1 `core/resource/discoverer/tool_discoverer.py`

```python
class ToolDiscoverer(BaseDiscoverer):
    """工具发现器"""

    async def search(
        self,
        query: str = None,
        tags: list[str] = None,
        input_format: str = None,      # 输入数据格式
        output_format: str = None,      # 输出数据格式
        tool_type: str = None,          # function/script/api-wrapper
    ) -> list[dict]:
        """按多维度检索工具"""
        ...

    async def match_by_capability(self, description: str) -> list[dict]:
        """根据能力描述匹配工具（语义搜索）"""
        # 调用 LLM 将自然语言描述与工具描述进行语义匹配
        ...

    async def get_by_input_format(self, format: str) -> list[dict]:
        """按输入数据格式查找工具"""
        ...
```

---

## 七、工具调用

### 7.1 调用方式

```python
# 方式 1: 直接调用（Agent 内）
from core.resource.registry.tool_registry import ToolRegistry

tool = await ToolRegistry.get("eeg-bandpass-filter")
result = tool.execute(data_path="/data/subj01.edf", channels=[0,1,2])

# 方式 2: 通过 API 调用
POST /api/tool/{tool_id}/execute
{
  "params": {
    "data_path": "/data/subj01.edf",
    "channels": [0, 1, 2]
  }
}
```

### 7.2 执行机制

```
Agent 调用 tool.execute(**params)
    │
    ├── 1. ToolRegistry 查找工具
    ├── 2. 校验输入参数（与 MD spec 的输入规范对比）
    ├── 3. 沙箱检查（如果启用）
    ├── 4. 加载工具模块
    ├── 5. 执行 tool.execute(**params)
    ├── 6. 校验输出（与 MD spec 的输出规范对比）
    ├── 7. 记录使用次数
    └── 8. 返回结果
```

### 7.3 沙箱安全

| 安全措施 | 说明 |
|---------|------|
| 文件访问限制 | 只能访问 `/data/` 和 `/tmp/` 目录 |
| 网络限制 | 默认禁止外网访问（API wrapper 类型除外） |
| 超时控制 | 默认 60s，可通过 spec 配置 |
| 内存限制 | 默认 1GB |
| 进程隔离 | 在独立子进程中执行 |

---

## 八、API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tool/generate-spec` | POST | NL → MD 工具描述 |
| `/api/tool/generate-code` | POST | MD → 代码 + 测试数据 |
| `/api/tool/test` | POST | 沙箱执行测试 |
| `/api/tool/register` | POST | 注册发布 |
| `/api/tool/list` | GET | 列出所有工具 |
| `/api/tool/{id}` | GET | 工具详情 |
| `/api/tool/{id}/execute` | POST | 调用工具 |
| `/api/tool/search` | GET | 搜索工具 |

---

## 九、前端工具编辑器视图

```
┌──────────────────────────────────────────────────────────┐
│  🔧 工具编辑器                                     [× 关闭] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Step 1: 描述需求 → [生成 MD 文档]                        │
│  ┌──────────────────────────────────────────────────────┐│
│  │ "我需要一个EEG带通滤波器，支持delta/theta/alpha/      ││
│  │  beta/gamma频段，Butterworth滤波器..."               ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  Step 2: MD 规范文档 (可编辑)          [→ 生成代码和测试] │
│  ┌──────────────────────────────────────────────────────┐│
│  │ ## 1. 功能概述                                       ││
│  │ EEG 带通滤波器...                                    ││
│  │ ## 2. 输入规范                                       ││
│  │ | data_path | string | 是 | ...                     ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  Step 3: 代码 + 测试结果                                   │
│  ┌──────────────────┬───────────────────────────────────┐│
│  │  # 生成的代码      │  沙箱测试                         ││
│  │  def execute(...  │  ✅ 语法通过                       ││
│  │      ...          │  ✅ 依赖检查                       ││
│  │                   │  ✅ 正常输入: 3.2s, 输出正确        ││
│  │                   │  ✅ 边界输入: 正确处理              ││
│  │                   │  ✅ 异常输入: 正确报错              ││
│  └──────────────────┴───────────────────────────────────┘│
│                                                          │
│                    [修改代码] [拒绝] [✅ 批准并注册发布]    │
│                                                          │
│  Step 4: 注册完成                                          │
│  ┌──────────────────────────────────────────────────────┐│
│  │ ✅ "eeg-bandpass-filter" 已注册                       ││
│  │ 📁 代码: resources/tools/implementations/...          ││
│  │ 🔍 可通过工具空间检索                                  ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

---

## 十、实现文件清单

```
# 后端 — 工具注册/发现/构建
core/resource/registry/tool_registry.py     # ToolRegistry
core/resource/discoverer/tool_discoverer.py # ToolDiscoverer
core/resource/builder/tool_builder.py       # ToolCodeBuilder + 测试数据生成
core/resource/builder/builder_base.py       # [已存在] BaseBuilder

# 后端 — API
app/api/schemas/tool_schemas.py             # Tool Pydantic 模型
app/api/routes/tool_routes.py               # /api/tool/* 端点

# 后端 — 存储
resources/tools/registry.json              # 工具注册表
resources/tools/definitions/               # MD 规范文档
resources/tools/implementations/           # 工具代码

# 前端 — 工具编辑器
frontend/src/components/center-panel/ToolEditorView.tsx
frontend/src/stores/tool-editor-store.ts
frontend/src/services/api/tool.ts

# 前端 — 修改
frontend/src/stores/ui-store.ts            # + 'tool-editor'
frontend/src/components/center-panel/CenterPanel.tsx  # + ToolEditor 路由
frontend/src/components/left-panel/ResourceBrowser.tsx # + 工具空间 [+] 按钮
frontend/src/stores/resource-store.ts      # + fetchToolsFromApi
```

## 十一、实现顺序

```
1. core/resource/builder/tool_builder.py   # 代码生成 + 测试数据生成
2. core/resource/registry/tool_registry.py  # 注册中心
3. core/resource/discoverer/tool_discoverer.py # 发现器
4. app/api/schemas/tool_schemas.py         # API 模型
5. app/api/routes/tool_routes.py           # API 端点
6. resources/tools/ 目录初始化              # 存储结构
7. 前端: ToolEditorView + store + api      # UI
8. 前端: ui-store + CenterPanel + ResourceBrowser 修改
```

# Tool 完整生命周期设计 v2

> 工具的规格化、生成、测试、注册、发现、调用全流程。
> v2 核心变更：简化接口、移除后处理、用户驱动测试、自动化调试增强。

---

## 一、核心理念

工具（Tool）是系统中最小的可执行单元。每个工具必须：
1. 有标准化的 MD 规范描述文档
2. 实现统一的 `execute(**kwargs) -> dict` 接口
3. 在资源注册中心登记
4. 通过资源发现器检索
5. 被 Agent 或编排流程调用

与 Agent 的区别：Agent 是独立进程，有自主行为；Tool 是被动调用，无状态，同步执行。

---

## 二、工具接口规范

### 2.1 唯一入口函数

```python
def execute(**kwargs) -> dict:
    """工具唯一入口，所有调用方通过此函数调用工具"""
```

### 2.2 参数访问

```python
# 正确：使用 kwargs.get 提供默认值
param = kwargs.get("param_name", default_value)

# 禁止：直接使用 kwargs["param_name"]（KeyError 风险）
```

### 2.3 返回值规范

```python
{
    "status": "success" | "failed",                           # 必须：执行状态
    "output_format": "text" | "image" | "table" | "file",    # 必须：输出类型
    "message": "...",                                          # 必须：成功说明或失败原因
    "data": { ... }                                            # 必须：实际结果数据
}
```

### 2.4 错误处理

```python
try:
    # 核心逻辑
    return {"status":"success", ...}
except Exception as e:
    return {"status":"failed", "message":str(e)}
```

### 2.5 调用约定

| 规则 | 说明 |
|------|------|
| 参数传入 | 始终通过 `**kwargs` 键值对传入 |
| 返回值 | 始终是 `dict`，方便 JSON 序列化 |
| 无状态 | 每次调用独立，不依赖全局状态 |
| 子进程隔离 | 工具在独立子进程中执行 |
| 超时控制 | 默认 30 秒 |
| 依赖隔离 | 每个工具使用本地 `.venv` |

### 2.6 LLM 生成时的强约束（写入 Prompt）

```
接口规范（必须遵守）：
1. 唯一入口：def execute(**kwargs) -> dict
2. 参数访问：kwargs.get("param_name", default)，禁止 kwargs["param_name"]
3. 返回值：{"status":"success"|"failed","output_format":"...","message":"...","data":{...}}
4. 错误处理：所有异常 try/except 包裹，返回 {"status":"failed","message":str(e)}
5. 无全局状态：每次调用独立
6. 文件路径：使用 _PROJECT_ROOT / "data" / ...
7. API调用：使用 _call_api("api-id", param=value)
8. 工具调用：使用 _call_tool("tool-id", param=value)
```

---

## 三、用户添加工具的完整流程

```
用户在左侧资源空间 → 工具空间 → [+] 按钮
    ↓
主面板切换到 → 工具编辑器视图
    ↓
┌─ Step 1: 描述需求 ──────────────────────────────────────────┐
│  自然语言描述工具功能                                          │
│  支持特殊标记：                                                │
│    【API名称】     → 引用系统 API                              │
│    【【工具名称】】 → 引用已注册工具                            │
│  调用 LLM → 生成标准化 MD 工具描述文档                          │
└──────────────────────────────────────────────────────────────┘
    ↓ 用户审阅编辑
┌─ Step 2: 审阅 MD 规范文档 ──────────────────────────────────┐
│  展示生成的 MD 文档，用户可编辑修改                             │
│  确认后 → 调用 LLM 生成完整工具代码                             │
└──────────────────────────────────────────────────────────────┘
    ↓
┌─ Step 3: 代码预览 + 沙箱测试 ───────────────────────────────┐
│  左: 生成的完整 Python 代码                                    │
│  右: 沙箱测试                                                 │
│    - 用户手动输入测试参数（弹窗）                               │
│    - 文件路径参数：提供上传按钮                                 │
│    - 点击"运行测试"执行                                        │
│    - 显示执行结果                                              │
│                                                              │
│  自动调试：                                                   │
│    - 点击"自动调试"启动                                        │
│    - 测试失败 → LLM 分析 → 重新生成代码 → 再测试               │
│    - 最多 50 轮，可随时停止                                    │
│    - 下方滚动显示调试日志                                      │
└──────────────────────────────────────────────────────────────┘
    ↓ 用户批准
┌─ Step 4: 注册发布 ─────────────────────────────────────────┐
│  ToolRegistry.register(tool)                                │
│  工具出现在资源空间 → 工具空间列表中                           │
│  可被 Agent 发现和调用                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 四、工具 MD 规范描述文档

### 4.1 标准模板

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
| param1 | string | 是 | — | 参数说明 |

## 3. 输出规范
### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | text / image / table / file |
| data | dict/list | 输出数据 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"text":"..."}` | 纯文本 |
| `image` | `{"image_path":"/path/to/file.png"}` | 直接绘制图片 |
| `table` | `{"columns":[...], "rows":[[...]]}` | 渲染表格 |
| `file` | `{"file_path":"/path/to/result.csv"}` | 下载链接 |

## 4. 依赖环境
| 依赖 | 版本 | 用途 |
|------|------|------|

## 5. 运行机制
### 5.1 执行流程
1. 读取输入数据
2. 校验参数
3. 执行核心逻辑
4. 返回结果

### 5.2 错误处理
- 文件不存在 → 返回错误信息
- 参数无效 → 返回验证错误
- 处理异常 → 捕获并返回详细错误

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | {日期} | 初始版本 |
```

### 4.2 NL → MD 转换 Prompt

```
你是一个工具规格文档生成器。根据用户描述生成标准化的 Tool MD 规范文档。

规则：
1. tool-id 使用小写字母+连字符
2. type 根据描述推断：function / script / api-wrapper
3. 合理推断输入参数和输出格式
4. 建议合适的依赖库和版本
5. 标注不确定的字段
6. 【重要】用户描述中的【xxx】标记表示系统API调用，【【xxx】】标记表示工具调用
7. 【严格禁止】除非用户描述中明确出现标记，否则不添加系统API或工具引用
```

---

## 五、工具代码生成

### 5.1 核心原则

**LLM 根据模板 + 提示，直接生成完整的可执行 Python 文件。不做额外后处理，完全依赖 LLM 生成符合规范的代码。**

### 5.2 模板结构

```python
# === SOTABand 工具标准模板 ===
import os, sys, json, time
from pathlib import Path
from typing import Any
import requests
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda *a, **kw: None

# 项目根路径
_tool_dir = os.environ.get("TOOL_DIR", "")
if _tool_dir:
    _PROJECT_ROOT = Path(_tool_dir).resolve().parent.parent.parent.parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 数据目录
_DATA_DIR = _PROJECT_ROOT / "data"
_DOWNLOADS_DIR = _DATA_DIR / "downloads"

# API 调用辅助
def _call_api(api_name: str, **params) -> dict:
    from core.api import get_api
    api = get_api(api_name)
    return api.call(**params)

# 工具调用辅助
def _call_tool(tool_name: str, **params) -> dict:
    from core.resource.registry.tool_registry import ToolRegistry
    import subprocess
    # 加载工具并执行

# === 以下由 LLM 生成 ===
```

### 5.3 生成流程

1. 前端调用 `POST /api/tool/generate-code`，传入 MD 文档
2. 后端解析 MD 中的标记：
   - `【xxx】` → 查找系统 API，生成 `_call_api("api-id", ...)` 调用示例
   - `【【xxx】】` → 查找注册工具，生成 `_call_tool("tool-id", ...)` 调用示例
3. 构建 Prompt，包含：
   - 模板框架说明（可用函数和变量）
   - MD 规范文档全文
   - API 和工具的调用方式
   - 接口规范约束
4. LLM 生成完整 Python 文件
5. **不进行任何后处理**，直接返回

### 5.4 调用方式区分

| 标记 | 含义 | 代码调用方式 |
|------|------|------------|
| `【API名称】` | 系统 API | `_call_api("api-id", **params)` |
| `【【工具名称】】` | 注册工具 | `_call_tool("tool-id", **params)` |

两者统一使用类似函数调用的模式，简化调用复杂度。

---

## 六、沙箱测试

### 6.1 核心原则

**不做语法检查、接口检查、静态分析。直接用测试数据驱动工具执行，捕获结果。**

### 6.2 测试数据获取

- 点击"沙箱测试"时，**弹窗让用户手动输入**测试参数
- 对于**文件路径类型**的参数，提供**上传按钮**：
  1. 用户点击上传 → 选择本地文件
  2. 上传到后端临时目录 `/tmp/sotaband-uploads/xxx.png`
  3. 返回临时路径，自动填入测试输入框
- 用户确认输入后，点击"运行测试"

### 6.3 测试执行

1. 将工具代码写入工具实现目录下的 `.py` 文件
2. 子进程执行，传入测试参数
3. 捕获 stdout 输出（`execute()` 的返回值）
4. 展示执行结果（成功/失败、输出数据、错误信息）

### 6.4 依赖管理

- 执行时遇到 `ModuleNotFoundError`：
  1. 自动在工具目录创建 `.venv` 虚拟环境
  2. `pip install` 缺失的库到 `.venv`
  3. 后续执行使用 `.venv/bin/python`
- 调用工具时优先使用本地 `.venv` 中的库
- 本地没有的库，回退到全局 Python 环境

---

## 七、自动调试

### 7.1 入口

- 沙箱测试按钮旁边提供**"自动调试"按钮**
- 点击后启动自动化调试循环

### 7.2 调试流程

```
┌─────────────────────────────────────────────────────┐
│  自动调试循环（最多 50 轮）                           │
│                                                     │
│  1. 使用测试输入执行工具代码                           │
│  2. 检查执行结果：                                    │
│     ├── 成功 → 调试完成 ✅                            │
│     └── 失败 → 进入步骤 3                             │
│  3. 将以下信息输入 LLM 重新生成代码：                  │
│     - 当前完整工具代码                                 │
│     - 测试输入                                        │
│     - 执行输出 / 错误信息                              │
│     - 模板提示（可用函数、接口规范、约束规则）           │
│  4. LLM 生成新代码 → 自动更新到界面代码展示区           │
│  5. 重复步骤 1                                        │
│                                                     │
│  用户可随时点击"停止调试"终止                          │
└─────────────────────────────────────────────────────┘
```

### 7.3 调试界面

- 测试界面下方**滚动显示**调试日志
- 日志重点输出：
  - 每轮的**测试输出**（stdout / stderr）
  - LLM 的**分析过程**（流式逐 token 显示）
  - 失败原因摘要
- **不输出完整修改代码**，代码变化自动更新到界面代码展示区
- 日志格式示例：
  ```
  [第3轮] 测试执行...
  [第3轮] 输出: {"status":"failed","message":"NameError: name 'x' is not defined"}
  [第3轮] 🤔 LLM分析: 变量x未定义，需要在函数顶部初始化...
  [第3轮] ✅ 代码已更新
  ```

### 7.4 停止机制

- 用户点击"停止调试"按钮 → 立即中断循环
- 达到 50 轮仍未通过 → 自动停止，提示用户手动介入

---

## 八、工具注册

### 8.1 注册流程

```
ToolRegistry.register(tool_spec, tool_code)
    │
    ├── 1. 校验 MD 规范文档完整性
    ├── 2. 分配唯一 Tool ID
    ├── 3. 保存代码到 resources/tools/implementations/{tool-id}/
    ├── 4. 写入 resources/tools/registry.json
    ├── 5. 建立索引: 类型/标签/输入输出格式
    └── 6. 发布注册事件
```

### 8.2 存储结构

```
resources/tools/
├── registry.json                       # 工具注册表
├── definitions/                        # MD 规范文档
│   └── {tool-id}.md
└── implementations/                    # 工具代码
    └── {tool-id}/
        ├── tool.py                     # 工具实现
        ├── spec.md                     # MD 规范文档副本
        └── .venv/                      # 独立虚拟环境（自动创建）
```

---

## 九、工具发现

```python
class ToolDiscoverer(BaseDiscoverer):
    async def search(self, query, tags, input_format, output_format, tool_type) -> list[dict]:
        """按多维度检索工具"""
    
    async def match_by_capability(self, description) -> list[dict]:
        """根据能力描述匹配工具（语义搜索）"""
```

---

## 十、工具调用

```python
# 方式 1: Agent 内直接调用
from core.resource.registry.tool_registry import ToolRegistry
tool = await ToolRegistry.get("eeg-bandpass-filter")
result = tool.execute(data_path="/data/subj01.edf", channels=[0,1,2])

# 方式 2: 通过 API 调用
POST /api/tool/{tool_id}/execute
{"params": {"data_path":"/data/subj01.edf","channels":[0,1,2]}}

# 方式 3: 工具间调用
_calli_tool("eeg-bandpass-filter", data_path="...", channels=[0,1,2])
```

---

## 十一、API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tool/generate-spec` | POST | NL → MD 工具描述 |
| `/api/tool/generate-code` | POST | MD → 完整代码 |
| `/api/tool/test` | POST | 沙箱执行测试 |
| `/api/tool/auto-debug` | POST | SSE 自动化调试 |
| `/api/tool/register` | POST | 注册发布 |
| `/api/tool/list` | GET | 列出所有工具 |
| `/api/tool/{id}` | GET | 工具详情 |
| `/api/tool/{id}/execute` | POST | 调用工具 |
| `/api/tool/search` | GET | 搜索工具 |

---

## 十二、前端工具编辑器视图

```
┌──────────────────────────────────────────────────────────┐
│  🔧 工具编辑器                                     [× 关闭] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Step 1: 描述需求 → [生成 MD 文档]                        │
│  ┌──────────────────────────────────────────────────────┐│
│  │ "我需要一个EEG带通滤波器..."                           ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  Step 2: MD 规范文档 (可编辑)          [→ 生成代码]       │
│  ┌──────────────────────────────────────────────────────┐│
│  │ ## 功能概述...                                       ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  Step 3: 代码 + 测试                                      │
│  ┌──────────────────┬───────────────────────────────────┐│
│  │  # 生成的代码      │  沙箱测试                         ││
│  │  def execute(...  │  [参数输入弹窗]                    ││
│  │      ...          │  [运行测试] [自动调试]             ││
│  │                   │  测试结果...                       ││
│  └──────────────────┴───────────────────────────────────┘│
│                                                          │
│  ┌── 🧠 自动调试日志（滚动） ──────────────────────────┐  │
│  │ [第1轮] 测试执行...                                  │  │
│  │ [第1轮] 输出: {"status":"failed","message":"..."}    │  │
│  │ [第1轮] 🤔 LLM分析: ...                              │  │
│  │ [第1轮] ✅ 代码已更新                                │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│                    [修改代码] [拒绝] [✅ 批准并注册发布]    │
└──────────────────────────────────────────────────────────┘
```

---

## 十三、实现文件清单

```
# 后端
core/resource/registry/tool_registry.py     # ToolRegistry
core/resource/discoverer/tool_discoverer.py # ToolDiscoverer
core/resource/builder/tool_builder.py       # ToolCodeBuilder（模板 + LLM 生成）
core/resource/builder/builder_base.py       # BaseBuilder
app/api/schemas/tool_schemas.py             # Tool Pydantic 模型
app/api/routes/tool_routes.py               # /api/tool/* 端点
resources/tools/registry.json              # 工具注册表
resources/tools/definitions/               # MD 规范文档
resources/tools/implementations/           # 工具代码 + .venv

# 前端
frontend/src/components/center-panel/ToolEditorView.tsx
frontend/src/stores/tool-editor-store.ts
frontend/src/services/api/tool.ts
frontend/src/stores/ui-store.ts
frontend/src/components/center-panel/CenterPanel.tsx
frontend/src/components/left-panel/ResourceBrowser.tsx
frontend/src/stores/resource-store.ts
```

---

## 十四、v1 → v2 关键变更

| 维度 | v1（旧） | v2（新） |
|------|---------|---------|
| 接口 | execute + 隐式约定 | **execute 唯一入口** |
| 代码生成 | 函数体 + 后处理清理 | **完整文件**，无后处理 |
| 沙箱测试 | 语法/接口/静态分析 + 自动数据 | **仅执行测试**，用户输入数据 |
| 测试数据 | 自动构造 | **弹窗用户输入**，文件支持上传 |
| 自动调试 | 5 轮，显示修改代码 | **50 轮**，显示输出和分析，代码静默更新 |
| 依赖管理 | 报错提示 | **自动安装到本地 .venv** |
| 停止机制 | 无 | **用户可随时停止** |
| 工具调用 | 无 | `【【工具名】】` → `_call_tool()` |
| 参数访问 | 未强制 | **强制 `kwargs.get`** |
| 返回值 | 隐式约定 | **显式 4 字段规范** |

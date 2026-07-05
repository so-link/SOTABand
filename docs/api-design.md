# API 子系统设计

> 核心层功能对外暴露的统一接口管理 — 注册、检索、调用。

---

## 一、核心理念

API 子系统是核心层对外的**统一接口抽象层**。将核心层中的资源管理、LLM 调用、算力调度等基础能力封装为标准 API，供工具代码和 Agent 代码调用。

每个 API 也遵循 **规范描述 → 注册登记 → 发现使用** 的管理模式。

---

## 二、API 定义

### 2.1 API MD 规范描述文档

```markdown
---
id: api-data-register
name: 数据集注册API
version: 1.0.0
category: resource
status: active
created: 2026-07-06
---

# 数据集注册API

## 1. 功能概述

将数据目录注册为数据集，写入 DataRegistry，生成 MD 规范文档，
使其在数据空间中可被发现和使用。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dataset_id | string | 是 | — | 数据集唯一标识 |
| dataset_name | string | 是 | — | 数据集名称 |
| data_path | string | 是 | — | 数据目录路径 |
| spec_md | string | 是 | — | MD 规范文档内容 |
| file_count | int | 否 | 0 | 文件数量 |
| total_size | int | 否 | 0 | 总大小(byte) |
| formats | list[string] | 否 | [] | 文件格式列表 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| dataset_id | string | 注册成功的数据集ID |
| status | string | 注册状态 (success/failed) |
| message | string | 结果说明 |

## 4. 调用示例

```python
from core.api import get_api

api = get_api("api-data-register")
result = api.call(
    dataset_id="my-dataset",
    dataset_name="我的数据集",
    data_path="/data/my-dataset/",
    spec_md="...",
    file_count=5,
    total_size=1048576,
    formats=["csv", "png"],
)
# result: {"dataset_id": "my-dataset", "status": "success"}
```

## 5. 实现依赖

- DataRegistry
- 无外部依赖

## 6. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-07-06 | 初始版本 |
```

### 2.2 API 类别

| 类别 | 说明 | 示例 |
|------|------|------|
| `resource` | 资源管理类 | 数据集注册/删除、工具注册/删除、Agent 注册/删除 |
| `llm` | LLM 调用类 | 文本生成、参数提取、语义匹配 |
| `scheduler` | 算力调度类 | GPU 分配、任务队列管理 |
| `system` | 系统管理类 | 健康检查、配置管理、日志查询 |

---

## 三、API 注册与管理

### 3.1 存储结构

API 子系统位于**核心层**中，注册信息、MD 描述文档、实现代码均在 core 目录下管理：

```
core/api/                             # 核心层 API 子系统（注册+发现，不存放实现）
├── __init__.py                       # get_api() 统一入口
├── registry.py                       # ApiRegistry — API 注册中心
├── discoverer.py                     # ApiDiscoverer — API 发现器
├── base.py                           # BaseApi — API 基类
├── schemas.py                        # Pydantic 模型
├── registry.json                     # API 注册表（记录每个API的实现位置和调用方法）
└── definitions/                      # MD 规范文档
    ├── api-data-register.md
    ├── api-data-delete.md
    ├── api-llm-chat.md
    └── ...
```

API 的**实际实现位于 core 层其他模块中**，不在 api 目录下：

```
core/
├── llm/client.py                     # → api-llm-chat 的实现
├── resource/registry/
│   ├── data_registry.py              # → api-data-register 的实现
│   ├── tool_registry.py              # → api-tool-register 的实现
│   └── agent_registry.py             # → api-agent-register 的实现
├── resource/discoverer/
│   └── tool_discoverer.py            # → api-tool-discover 的实现
├── agent/factory.py                  # → api-agent-start 的实现
└── ...                               # 未来更多模块
```

### 3.2 注册表结构

```json
{
  "id": "api-data-register",
  "name": "数据集注册API",
  "version": "1.0.0",
  "category": "resource",
  "status": "active",
  "spec_path": "definitions/api-data-register.md",
  "impl_module": "core.resource.registry.data_registry",
  "impl_class": "DataRegistry",
  "impl_method": "register",
  "is_async": true,
  "input_schema": {
    "dataset_id": {"type": "string", "required": true},
    "dataset_name": {"type": "string", "required": true},
    "data_path": {"type": "string", "required": true},
    "spec_md": {"type": "string", "required": true},
    "file_count": {"type": "int", "required": false},
    "total_size": {"type": "int", "required": false},
    "formats": {"type": "list", "required": false}
  },
  "output_schema": {
    "dataset_id": "string",
    "status": "string",
    "message": "string"
  },
  "tags": ["数据", "注册", "管理"],
  "created_at": "2026-07-06T00:00:00Z"
}
```

**关键字段说明：**

| 字段 | 说明 | 示例 |
|------|------|------|
| `impl_module` | Python 模块路径 | `core.resource.registry.data_registry` |
| `impl_class` | 类名 | `DataRegistry` |
| `impl_method` | 调用方法名 | `register` |
| `is_async` | 是否异步调用 | `true` |

`get_api("api-data-register")` 时，系统根据这些字段动态加载：

```python
# 等价于:
from core.resource.registry.data_registry import DataRegistry
result = await DataRegistry().register(**kwargs)
```

---

## 四、API 发现与检索

### 4.1 发现器 `core/api/discoverer.py`

```python
class ApiDiscoverer:
    """API 发现器 — 按类别/标签/功能检索"""
    
    async def search(self, query: str = None, category: str = None, tags: list = None) -> list[dict]:
        ...
    
    async def match_by_intent(self, description: str) -> list[dict]:
        """根据工具的功能描述，语义匹配需要的 API"""
        ...
    
    async def get_by_category(self, category: str) -> list[dict]:
        ...
```

---

## 五、API 与工具代码的集成

### 5.1 工具生成时匹配 API

在工具编辑器中，用户描述工具功能时：

```
用户: "生成数据分析报告，然后将报告文件注册为数据集"
  ↓
LLM 解析需求: 需要 api-data-register（数据集注册API）
  ↓
ApiDiscoverer.match_by_intent("将报告文件注册为数据集")
  → 返回: ["api-data-register"]
  ↓
工具 MD spec 中记录:
  ## 6. 使用的系统 API
  | API ID | 用途 |
  |--------|------|
  | api-data-register | 将生成的报告注册为数据集 |
  ↓
工具代码生成时:
  from core.api import get_api
  data_api = get_api("api-data-register")
  result = data_api.call(dataset_id=..., ...)
```

### 5.2 工具代码中的 API 调用

```python
def execute(**kwargs):
    # 1. 核心处理逻辑
    report_path = generate_report(kwargs["data_path"])
    
    # 2. 调用系统 API 注册数据集
    from core.api import get_api
    api = get_api("api-data-register")
    result = api.call(
        dataset_id=f"report-{int(time.time())}",
        dataset_name="分析报告",
        data_path=os.path.dirname(report_path),
        spec_md=generate_report_spec(report_path),
        file_count=1,
    )
    
    return {
        "status": "success",
        "output_format": "file",
        "message": "报告已生成并注册",
        "data": {"report_path": report_path, "dataset_id": result["dataset_id"]},
    }
```

---

## 六、API 列表（第一批实现）

### 6.1 资源管理类

| API ID | 名称 | 实现位置 | 调用方法 |
|--------|------|---------|---------|
| `api-data-register` | 数据集注册 | `core.resource.registry.data_registry.DataRegistry` | `register()` |
| `api-data-delete` | 数据集删除 | `core.resource.registry.data_registry.DataRegistry` | `unregister()` |
| `api-data-list` | 数据集列表 | `core.resource.registry.data_registry.DataRegistry` | `list_all()` |
| `api-tool-register` | 工具注册 | `core.resource.registry.tool_registry.ToolRegistry` | `register()` |
| `api-tool-delete` | 工具删除 | `core.resource.registry.tool_registry.ToolRegistry` | `unregister()` |
| `api-tool-list` | 工具列表 | `core.resource.registry.tool_registry.ToolRegistry` | `list_all()` |
| `api-agent-register` | Agent注册 | `core.resource.registry.agent_registry.AgentRegistry` | `register()` |
| `api-agent-start` | Agent启动 | `core.agent.factory.AgentFactory` | `start()` |
| `api-agent-stop` | Agent停止 | `core.agent.factory.AgentFactory` | `stop()` |

### 6.2 LLM 调用类

| API ID | 名称 | 实现位置 | 调用方法 |
|--------|------|---------|---------|
| `api-llm-chat` | LLM对话 | `core.llm.client.DeepSeekClient` | `chat()` |
| `api-llm-chat-stream` | LLM流式对话 | `core.llm.client.DeepSeekClient` | `chat_stream()` |
| `api-llm-extract-params` | 参数提取 | `core.resource.builder.tool_builder.ToolCodeBuilder` | `extract_param_metadata()` |

### 6.3 算力调度类（规划中）

| API ID | 名称 | 实现位置 | 调用方法 |
|--------|------|---------|---------|
| `api-gpu-allocate` | GPU分配 | `core.scheduler.allocator.ResourceAllocator` | `allocate()` |
| `api-task-submit` | 任务提交 | `core.scheduler.heterogeneous.HeterogeneousScheduler` | `submit()` |

---

## 七、API 子系统架构

API 子系统作为核心层的组成部分，与 `core/resource/`、`core/agent/`、`core/llm/` 等子系统并列：

```
core/
├── agent/                   # 多智能体协作子系统
├── engine/                   # 工作流引擎子系统
├── llm/                      # LLM 客户端
├── resource/                 # 资源管理子系统
│   ├── registry/             #   资源注册中心（数据/工具/Agent）
│   ├── discoverer/           #   资源发现器
│   └── builder/              #   资源构建器
├── api/                      # API 子系统（核心层功能对外接口）
│   ├── __init__.py           #   get_api() 统一入口
│   ├── registry.py           #   ApiRegistry
│   ├── discoverer.py         #   ApiDiscoverer
│   ├── base.py               #   BaseApi
│   ├── schemas.py            #   Pydantic 模型
│   ├── registry.json         #   API 注册表
│   ├── definitions/          #   MD 规范文档
│   └── implementations/      #   API 实现（核心层自己实现）
└── scheduler/                # 调度与算力管理子系统
```


### 7.1 API 基类

```python
class BaseApi(ABC):
    """API 基类"""
    
    def __init__(self, spec: dict):
        self.spec = spec
    
    @abstractmethod
    async def call(self, **kwargs) -> dict:
        """同步调用"""
        ...
    
    @abstractmethod
    async def call_async(self, **kwargs) -> AsyncGenerator:
        """异步流式调用"""
        ...
```

### 7.2 统一入口

```python
# core/api/__init__.py

def get_api(api_id: str) -> "BaseApi":
    """通过 API ID 获取 API 实例"""
    ...

def list_apis(category: str = None) -> list[dict]:
    """列出可用 API"""
    ...

def search_apis(query: str) -> list[dict]:
    """搜索 API"""
    ...

def register_api(api_spec: dict, impl_code: str) -> str:
    """注册新 API"""
    ...
```

---

## 八、实现顺序

```
Phase 1: API 基础设施
  ├── 1. core/api/base.py — BaseApi 基类
  ├── 2. core/api/registry.py — ApiRegistry
  ├── 3. core/api/discoverer.py — ApiDiscoverer
  └── 4. resources/apis/ 目录初始化

Phase 2: 第一批 API 实现
  ├── 5. core/api/implementations/data_register.py
  ├── 6. core/api/implementations/data_delete.py
  ├── 7. core/api/implementations/tool_register.py
  ├── 8. core/api/implementations/llm_chat.py
  └── 9. core/api/implementations/llm_extract_params.py

Phase 3: 工具生成集成
  ├── 10. 工具 MD spec 增加 API 引用字段
  ├── 11. 工具代码生成时检测并注入 API 调用
  └── 12. 端到端测试: 工具 → API → 资源注册
```

---

## 九、与 Tool/Agent/Data 管理的一致性

| 特性 | Tool | Agent | Data | **API** |
|------|------|-------|------|-----|
| MD 规范文档 | ✅ | ✅ | ✅ | ✅ |
| 注册表 JSON | ✅ | ✅ | ✅ | ✅ |
| 发现器 | ✅ | ✅ | ✅ | ✅ |
| 代码生成 | ✅ | ✅ | ❌ | ✅ |
| 调用方式 | execute() | execute() | — | **call()** |
| 调用方 | Agent | 编排/调度 | — | **Tool/Agent** |

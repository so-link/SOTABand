# API 子系统设计

> 核心层功能对外暴露的统一接口管理 — 注册、检索、调用。

---

## 一、核心理念

API 子系统是核心层对外的**统一接口抽象层**。将核心层中的资源管理、LLM 调用、Agent 管理等基础能力封装为标准 API，供工具代码通过 `_call_api()` 调用。

每个 API 遵循 **规范描述 → 注册登记 → 实现调用** 的管理模式。

---

## 二、当前目录结构

```
core/api/
├── __init__.py                       # get_api() 统一入口
├── registry.py                       # ApiRegistry — 注册中心
├── registry.json                     # 11个API注册表
├── discoverer.py                     # ApiDiscoverer — 发现器
├── base.py                           # BaseApi 基类
├── definitions/                      # MD 规范文档（11个）
│   ├── api-data-register.md
│   ├── api-data-delete.md
│   ├── api-data-list.md
│   ├── api-tool-register.md
│   ├── api-tool-list.md
│   ├── api-llm-chat.md
│   ├── api-llm-chat-stream.md
│   ├── api-llm-get-config.md
│   ├── api-agent-start.md
│   ├── api-agent-stop.md
│   └── api-doubao-get-key.md
└── implementations/                  # Python 实现（4个文件）
    ├── api_data.py                   # 数据集: register / delete / list
    ├── api_tool.py                   # 工具: register / list
    ├── api_llm.py                    # LLM: chat / chat_stream / get_config / doubao
    └── api_agent.py                  # Agent: start / stop
```

---

## 三、注册一个新 API 的完整流程

### 3.1 创建 MD 规范文档

在 `core/api/definitions/{api_id}.md` 中创建，格式如下：

```
# api-id

## 功能概述
用自然语言描述这个 API 做什么

## 输入规范
| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| param1 | string | 是 | — | 参数说明 |

## 输出规范
| 字段 | 类型 | 说明 |
|------|------|------|
| result_field | string | 结果说明 |

## 依赖环境
无外部依赖

## 实现
模块: core.api.implementations.xxx.ClassName
```

### 3.2 编写 Python 实现

在 `core/api/implementations/api_xxx.py` 中添加实现类：

```
class ApiXxx:
    """api-xxx: 功能说明"""

    @staticmethod
    def call(**kwargs) -> dict:
        # 从 kwargs 获取参数
        # 执行实际逻辑
        # 返回结果 dict
```

关键约束：
- 类名与 `registry.json` 中的 `impl_class` 一致
- 实现 `call(**kwargs) -> dict` 方法
- 方法可以是同步或异步（通过 `is_async` 字段指定）

### 3.3 注册到 registry.json

在 `core/api/registry.json` 中添加条目：

```json
{
  "id": "api-xxx",
  "name": "API中文名称",
  "version": "1.0.0",
  "category": "resource",
  "status": "active",
  "spec_path": "definitions/api-xxx.md",
  "impl_module": "core.api.implementations.api_xxx",
  "impl_class": "ApiXxx",
  "impl_method": "call",
  "is_async": false,
  "input_schema": {
    "param1": "string"
  },
  "output_schema": {
    "result_field": "string"
  },
  "tags": ["标签"],
  "created_at": "2026-07-25T00:00:00Z"
}
```

### 3.4 注册后可用

重启后端即可在工具代码中通过 `【API中文名称】` 引用该 API。

---

## 四、调用链路

工具代码中调用 `_call_api("api-id", param=value)` 时：

1. `_call_api` 函数（模板预定义）→
2. `core/api/__init__.py` 中的 `get_api("api-id")` →
3. 从 `registry.json` 查找匹配条目 →
4. 动态导入 `impl_module` →
5. 实例化 `impl_class` →
6. 调用 `impl_method`（即 `call(**kwargs)`）→
7. 返回结果 dict

### 4.1 工具代码中的调用方式

工具通过模板提供的 `_call_api()` 函数调用：

```
result = _call_api("api-data-register",
    id=kwargs.get("dataset_id", ""),
    name=kwargs.get("dataset_name", ""),
    ...
)
# 使用返回值
dataset_id = result["dataset_id"]
```

### 4.2 工具间调用

工具通过模板提供的 `_call_tool()` 函数调用其他工具：

```
result = _call_tool("tool-name", param1=value1, ...)
```

---

## 五、工具代码生成时的 API 信息注入

生成工具代码时，`_llm_generate` 方法会：

1. 从 MD 规范文档中提取 `【API名称】` 标记
2. 从 `registry.json` 查找匹配的 API
3. 读取 API 的 MD 定义文件，提取输入/输出参数表的描述
4. 构造详细的 API 信息注入 Prompt

### 5.1 Prompt 中的 API 信息格式

以 `【数据集注册API】` 为例：

```
=== SYSTEM API CALLS ===

  API: 数据集注册API (ID: api-data-register)
    调用: _call_api("api-data-register", id=<id>, name=<name>, raw_md=<raw_md>, data_path=<data_path>, file_count=<file_count>, total_size=<total_size>, formats=<formats>)
    输入参数:
      id (string, 必填): 数据集唯一标识
      name (string, 必填): 数据集名称
      raw_md (string, 必填): MD 规范文档
      data_path (string, 必填): 数据文件路径
      file_count (int, 必填): 文件数量
      total_size (int, 必填): 总大小(字节)
      formats (list, 必填): 数据格式列表
    返回值 (dict):
      dataset_id (string): 注册的数据集ID
    使用方式: result = _call_api("api-data-register", ...); # 然后从 result 中按字段名取值

=== END API CALLS ===
```

### 5.2 LLM 从这些信息中能获取

- **API ID**：`api-data-register`
- **参数名和类型**：`id`(string)、`name`(string) 等
- **必填/可选**：标注了必填
- **中文描述**：LLM 知道每个参数的含义
- **返回值结构**：字段名、类型、含义
- **使用方式**：如何取值

---

## 六、当前 API 列表

| API ID | 名称 | 分类 | 实现文件 |
|--------|------|------|---------|
| api-data-register | 数据集注册API | resource | api_data.py |
| api-data-delete | 数据集删除API | resource | api_data.py |
| api-data-list | 数据集列表API | resource | api_data.py |
| api-tool-register | 工具注册API | resource | api_tool.py |
| api-tool-list | 工具列表API | resource | api_tool.py |
| api-llm-chat | LLM对话API | llm | api_llm.py |
| api-llm-chat-stream | LLM流式对话API | llm | api_llm.py |
| api-llm-get-config | LLM配置获取API | llm | api_llm.py |
| api-doubao-get-key | 获取豆包API KEY | llm | api_llm.py |
| api-agent-start | Agent启动API | resource | api_agent.py |
| api-agent-stop | Agent停止API | resource | api_agent.py |

---

## 七、与 Tool/Agent/Data 管理的一致性

| 特性 | Tool | Agent | Data | API |
|------|------|-------|------|-----|
| MD 规范文档 | ✅ | ✅ | ✅ | ✅ |
| 注册表 JSON | ✅ | ✅ | ✅ | ✅ |
| 发现器 | ✅ | ✅ | ✅ | ✅ |
| 代码生成 | ✅ | ✅ | ❌ | 手动编写 |
| 调用方式 | execute() | execute() | — | call() |
| 调用方 | Agent/编排 | 编排/调度 | — | Tool/Agent |

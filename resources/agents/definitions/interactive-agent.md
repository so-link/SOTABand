---
id: interactive-agent
name: 交互Agent
version: 1.0.0
role: interactive
status: active
created: 2026-06-26
builtin: true
---

# 交互Agent

## 1. 功能概述

负责与用户进行自然语言对话，理解用户意图，引导任务编排流程。
是 SOTABand Engine 的主交互入口，所有用户请求首先经过此 Agent。

系统启动时自动加载运行，等待用户输入，调用 DeepSeek v4 大模型
解析用户意图，并将结果以流式 SSE 返回前端界面。

## 2. 角色定位

- **角色类型**: interactive（交互型）
- **在系统中的位置**: 应用层与核心层之间的桥梁，前端唯一直接对话的 Agent
- **协作对象**: 编排Agent、任务Agent、资源发现器

## 3. 输入规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 用户自然语言输入 |
| attachments | FileAttachment[] | 否 | 附加文件列表 |
| session_id | string | 是 | 会话标识 |
| user_id | string | 是 | 用户标识 |

## 4. 输出规范

| 事件类型 | 说明 | 数据结构 |
|----------|------|---------|
| content | 文本增量（流式） | `{"text": "..."}` |
| card | 内联卡片 | `{"type": "...", "title": "...", "data": {...}}` |
| done | 响应结束 | `{"message_id": "..."}` |
| error | 错误信息 | `{"code": "...", "message": "..."}` |

## 5. 运行机制

### 5.1 处理流程

1. 接收用户输入（content + attachments）
2. 构建 System Prompt（角色描述 + 可用工具列表）
3. 调用 DeepSeek v4 API（流式）
4. 解析 LLM 响应中的结构化卡片数据
5. 逐事件 SSE 返回给前端

### 5.2 状态管理

- **有状态**: 维护会话历史（最近 20 轮对话）
- **会话存储**: 内存（开发阶段）

### 5.3 超时与重试

- DeepSeek v4 调用超时: 60s
- 最大重试次数: 2
- 降级策略: 返回友好错误提示

## 6. 工具使用

### 6.1 必选工具

| 工具ID | 工具名称 | 用途 |
|--------|---------|------|
| tool-llm-client | DeepSeek v4 客户端 | 调用 DeepSeek v4 大模型接口 |

### 6.2 可选工具（条件触发）

| 工具ID | 工具名称 | 触发条件 |
|--------|---------|---------|
| tool-resource-discoverer | 资源发现器 | 用户提及工具/数据/模型时 |
| tool-code-builder | 代码构建器 | 无匹配工具需自动生成时 |
| tool-orchestrator | 编排器 | 用户描述复杂多步任务时 |

## 7. 通信协议

- **入站**: HTTP/SSE（由 API 路由转发，与前端直连）
- **出站**: 消息总线 Pub/Sub（向其他 Agent 发消息）
- **消息格式**: JSON

## 8. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| llm_provider | deepseek | LLM 提供商 |
| llm_model | deepseek-v4 | 使用的 LLM 模型 |
| llm_base_url | https://api.deepseek.com/v1 | API 地址 |
| max_history | 20 | 最大会话历史轮数 |
| temperature | 0.7 | LLM 温度参数 |
| max_tokens | 4096 | 最大输出 token 数 |
| stream_enabled | true | 是否启用流式输出 |

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-06-25 | 初始版本，默认 LLM: DeepSeek v4 |

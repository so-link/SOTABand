---
id: interactive-agent
name: 交互Agent
version: 1.0.0
role: interactive
builtin: true
---

# 系统默认交互 Agent

随核心源码分发，由 AgentFactory 自动启动独立子进程，不依赖个人资源文件。
默认入口不能被同 ID 的私有 Agent 替换；自定义 Agent 请使用其他 ID。

使用 core.llm.client 的统一 LLM 配置，支持会话历史、文件附件、工作区已加载
工具匹配、工具确认、模式选择、参数补全与工具创建引导。LLM API Key 由用户配置；
无需在工具库注册 tool-llm-client 等占位工具。

输入：content、attachments、workspace_tool_ids；session_id 和 user_id 由执行上下文提供。
空 workspace_tool_ids 表示工作区没有加载工具，不会自动使用整个工具库。

对前端仍输出 content、card、error、done 事件，done 数据含 messageId。
引擎与 Agent 之间保持 UTF-8 JSON 行协议，Agent 加载及 on_start 完成后发送
agent_ready；每个事件立即输出。退出时执行 on_stop，回收常驻工具。

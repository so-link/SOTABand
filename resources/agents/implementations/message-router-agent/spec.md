# 消息路由智能体

id: message-router-agent
name: 消息路由智能体
role: web-service
version: 0.1.0

## 简介

接收 Web 请求，解析意图，在工具空间中匹配合适的工具，调用执行并返回结果——
让外部系统以标准 API 方式调用 SOTABand 工具能力（todo 4.1）。

## 服务配置

```server
{
  "host": "127.0.0.1",
  "port": null
}
```

## API 定义

```apis
[
  {
    "name": "route",
    "method": "POST",
    "path": "/route",
    "description": "路由执行：显式指定 tool（id/名称）直接调用；或给出 message 由智能体在工具空间中匹配工具、抽取参数后执行",
    "params": [
      {"name": "tool", "type": "string", "required": false, "desc": "工具 id 或名称（显式路由）"},
      {"name": "message", "type": "string", "required": false, "desc": "意图描述（自然语言），与 tool 至少给一个"},
      {"name": "params", "type": "object", "required": false, "desc": "工具参数（配合 tool 使用；缺省时从 message 抽取）"}
    ]
  },
  {
    "name": "tools",
    "method": "GET",
    "path": "/tools",
    "description": "列出工具空间中可路由的活跃工具（id/名称/描述/标签/参数定义）",
    "params": []
  }
]
```

## 匹配语义

响应中的 `matched_by` 标注本次路由方式：

1. **explicit** — 请求显式给出 `tool`（按 id、再按名称，大小写不敏感）直接调用；同时给 `message` 且未给 `params` 时，由 LLM 从消息中抽取参数。
2. **llm** — 仅给 `message`：工具发现器按关键词初筛 top-8 候选，LLM 从中选定工具并抽取参数。
3. **keyword** — LLM 未配置或输出非法时，回退到关键词初筛的 top1。

`tool` 不存在时返回 `status: "not_found"` 与近似候选列表；`message` 无候选工具时同样返回 `not_found` 与可选清单，不抛 500。

## 安全边界

- 工具执行统一经 `ToolExecutor`（常驻 Worker 优先），工具侧静态安全审查闸门照常生效。
- 与所有 web-service Agent 同一信任模型：**仅监听 127.0.0.1、端口无鉴权**（见 docs/agent-types-design.md 遗留问题），不要将其端口直接暴露到外部网络。

## 示例

```bash
# 列出可路由工具
curl http://127.0.0.1:<port>/tools

# 显式路由
curl -X POST http://127.0.0.1:<port>/route \
  -H 'Content-Type: application/json' \
  -d '{"tool": "calc-square", "params": {"n": 5}}'

# 意图路由
curl -X POST http://127.0.0.1:<port>/route \
  -H 'Content-Type: application/json' \
  -d '{"message": "计算 5 的平方"}'
```

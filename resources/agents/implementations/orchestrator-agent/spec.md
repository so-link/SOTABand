# 任务编排智能体

id: orchestrator-agent
name: 任务编排智能体
role: web-service
version: 0.1.0

## 简介

接收复杂任务，按声明式步骤把每一步委派给 Agent 空间中运行中的子 Agent，
步骤间传递数据，聚合全部步骤结果返回——多 Agent 协作编排的首版落地（todo 4.2），
委派通道为本地通信总线（todo 4.5 `call_agent` RPC）。

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
    "name": "agents",
    "method": "GET",
    "path": "/agents",
    "description": "列出 Agent 空间中可委派的活跃 Agent（id/名称/角色/描述）",
    "params": []
  },
  {
    "name": "plan",
    "method": "POST",
    "path": "/plan",
    "description": "按步骤编排执行：steps 数组串行委派给各子 Agent，params 支持 {stepN.x} 引用前序输出、{param.x} 引用请求级参数",
    "params": [
      {"name": "steps", "type": "array", "required": true, "desc": "步骤数组，每步 {agent, method, params?, timeout?, required?}；required 默认 true，失败即中止"},
      {"name": "params", "type": "object", "required": false, "desc": "请求级参数，供 {param.x} 引用"}
    ]
  },
  {
    "name": "call",
    "method": "POST",
    "path": "/call",
    "description": "单跳委派：向指定 Agent 发起一次 RPC（调试与直连用）",
    "params": [
      {"name": "agent", "type": "string", "required": true, "desc": "目标 Agent id"},
      {"name": "method", "type": "string", "required": true, "desc": "目标 Agent 的方法名"},
      {"name": "params", "type": "object", "required": false, "desc": "方法参数"},
      {"name": "timeout", "type": "number", "required": false, "desc": "超时秒数（默认 60，上限 300）"}
    ]
  }
]
```

## 编排语义

`POST /plan` 请求体示例：

```json
{
  "params": {"query": "北京"},
  "steps": [
    {"agent": "worker-a", "method": "search", "params": {"q": "{param.query}"}},
    {"agent": "worker-b", "method": "summarize", "params": {"text": "{step1.data.text}"}},
    {"agent": "worker-c", "method": "notify", "params": {"msg": "{step2.text}"}, "required": false}
  ]
}
```

- 步骤按声明顺序**串行**执行（DAG 的线性特例；并行/分支 DAG 留待工作流引擎）。
- 参数引用复用 `BaseAgent.resolve_refs`：`{stepN.a.b}` 取第 N 步结果，
  `{param.x}` 取请求级参数；引用缺失解析为空串。
- 每步 `required`（默认 true）：required 步骤失败即中止，响应
  `status: "failed"` 且 `failed_step` 标注步号与错误；非 required 步骤失败记录后继续。
- 响应 `results` 逐步列出 `{step, agent, method, ok, result, error}`。
- 目标 Agent 未运行 / 超时 / 崩溃均得该步的结构化错误（总线契约，不抛异常），
  编排本身不会 500。

## 安全边界

- 委派唯一出口是 4.5 本地总线 `call_agent`（子进程 → 主进程路由 → 目标子进程），
  本 Agent 不直接执行工具、不开网络出口。
- 被调 Agent 以其自身 `execute` 语义执行委派（`bus_method`/`bus_params` 入参），
  各自受自身代码的安全审查约束。
- 与所有 web-service Agent 同一信任模型：**仅监听 127.0.0.1、端口无鉴权**
  （遗留问题登记于 docs/agent-types-design.md），不要将其端口暴露到外部网络。
- 本 Agent 作为被调方（被其他 Agent RPC）暂未定义方法，收到委派会返回未定义 API 错误。

## 示例

```bash
# 列出可委派 Agent
curl http://127.0.0.1:<port>/agents

# 单跳委派
curl -X POST http://127.0.0.1:<port>/call \
  -H 'Content-Type: application/json' \
  -d '{"agent": "worker-a", "method": "echo", "params": {"v": 1}}'

# 两步编排（第二步引用第一步输出）
curl -X POST http://127.0.0.1:<port>/plan \
  -H 'Content-Type: application/json' \
  -d '{"steps": [
        {"agent": "worker-a", "method": "echo", "params": {"v": 1}},
        {"agent": "worker-b", "method": "twice", "params": {"n": "{step1.v}"}}
      ]}'
```

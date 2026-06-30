"""Agent 管理路由 — 规格生成、代码生成、注册、运行时管理"""

import asyncio
import time
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.schemas.agent_schemas import (
    GenerateSpecRequest,
    GenerateCodeRequest,
    RegisterAgentRequest,
    AgentExecuteRequest,
)
from core.llm.client import create_llm_client
from core.resource.builder.agent_builder import AgentCodeBuilder
from core.resource.registry.agent_registry import AgentRegistry
from core.agent.factory import agent_factory

router = APIRouter()
builder = AgentCodeBuilder()
registry = AgentRegistry()
llm = create_llm_client()

SPEC_GENERATION_PROMPT = """你是一个 Agent 规格文档生成器。根据用户的自然语言描述，生成标准化的 Agent MD 规范文档。

## 输出格式

严格按照以下 Markdown 模板输出：

---
id: {agent-id}
name: {Agent名称}
version: 0.1.0
role: {interactive|task|orchestrator|observer}
status: active
created: {今天日期}
---

# {Agent名称}

## 1. 功能概述

{用自然语言描述 Agent 做什么}

## 2. 角色定位

- **角色类型**: {interactive|task|orchestrator|observer}
- **在系统中的位置**: {描述}
- **协作对象**: {列出协作的其他 Agent}

## 3. 输入规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 输入内容 |

## 4. 输出规范

| 事件类型 | 说明 | 数据结构 |
|----------|------|---------|
| content | 文本输出 | `{"text": "..."}` |
| done | 结束 | `{"messageId": "..."}` |

## 5. 运行机制

### 5.1 处理流程

1. {步骤1}
2. {步骤2}
3. {步骤3}

### 5.2 状态管理

- {描述状态}

### 5.3 超时与重试

- 超时: 60s
- 最大重试次数: 2

## 6. 工具使用

### 6.1 必选工具

| 工具ID | 工具名称 | 用途 |
|--------|---------|------|

### 6.2 可选工具

| 工具ID | 工具名称 | 触发条件 |
|--------|---------|---------|

## 7. 通信协议

- **入站**: {描述入站方式}
- **出站**: {描述出站方式}
- **消息格式**: JSON

## 8. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | {今天日期} | 初始版本 |

## 规则

1. 根据用户描述合理推断 role 类型
2. 如果用户未提及某些字段，合理填充默认值
3. agent-id 使用小写字母和连字符，如 "eeg-analysis-agent"
4. 只输出 Markdown，不要额外解释
5. frontmatter 中的 created 使用实际日期"""


@router.post("/generate-spec")
async def generate_spec(req: GenerateSpecRequest):
    """自然语言描述 → Agent MD 规范文档"""
    if not req.description.strip():
        raise HTTPException(400, "description 不能为空")

    response = await llm.chat(
        messages=[
            {"role": "system", "content": SPEC_GENERATION_PROMPT},
            {"role": "user", "content": req.description},
        ],
        temperature=0.5,
        max_tokens=100000,
    )

    return {"spec_md": response.strip()}


@router.post("/generate-code")
async def generate_code(req: GenerateCodeRequest):
    """MD 规范文档 → Python Agent 代码"""
    if not req.spec_md.strip():
        raise HTTPException(400, "specMd 不能为空")

    spec = {
        "raw_md": req.spec_md,
        "id": req.agent_id,
        "name": req.agent_name,
        "role": req.role,
    }

    valid = await builder.validate_spec(spec)
    if not valid:
        raise HTTPException(400, "MD 规范文档不完整，缺少必需段落")

    code = await builder.build(spec)

    # 沙箱测试
    sandbox = await builder.dry_run(code)

    return {"code": code, "sandbox_results": sandbox}


@router.post("/register")
async def register_agent(req: RegisterAgentRequest):
    """注册 Agent 到资源空间"""
    if not req.spec_md.strip():
        raise HTTPException(400, "specMd 不能为空")

    agent_id = req.agent_id or "custom-agent"

    # 从 MD 中提取 agent-id
    for line in req.spec_md.split("\n"):
        if line.startswith("id:") and "---" not in line:
            agent_id = line.split(":", 1)[1].strip()
            break

    resource = {
        "id": agent_id,
        "name": req.agent_name,
        "version": req.version,
        "role": req.role,
        "raw_md": req.spec_md,
        "tags": req.tags,
    }

    registered_id = await registry.register(resource)
    entry = await registry.get(registered_id)

    # 保存生成的代码到 implementations 目录
    if req.code.strip():
        impl_dir = registry._get_impl_dir() / registered_id
        impl_dir.mkdir(parents=True, exist_ok=True)
        (impl_dir / "agent.py").write_text(req.code)
        (impl_dir / "spec.md").write_text(req.spec_md)
        (impl_dir / "config.yaml").write_text(
            f"# {req.agent_name} 运行时配置\n"
            f"role: {req.role}\n"
            f"version: {req.version}\n"
        )

    return {"agent_id": registered_id, "entry": entry}


@router.get("/list")
async def list_agents():
    """列出所有已注册的 Agent"""
    agents = await registry.list_all()
    return {"agents": agents}


@router.get("/{agent_id}/status")
async def agent_status(agent_id: str):
    """查询 Agent 状态"""
    entry = await registry.get(agent_id)
    if not entry:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    running = agent_factory.is_running(agent_id)
    return {**entry, "running": running}


# ═══════════════════════════════════════════════════════════════
# Agent 运行时管理 — 子进程启动 / 执行 / 停止 / 重启
# ═══════════════════════════════════════════════════════════════

@router.post("/{agent_id}/start")
async def start_agent(agent_id: str):
    """启动 Agent 子进程"""
    entry = await registry.get(agent_id)
    if not entry:
        raise HTTPException(404, f"Agent '{agent_id}' not found")

    # registry 中 impl_path 是相对于 resources/agents/ 的路径
    # 实际实现在 core/agent/implementations/
    impl_path = f"resources/agents/implementations/{agent_id}"
    return await agent_factory.start(agent_id, impl_path)


@router.post("/{agent_id}/execute")
async def execute_agent(agent_id: str, req: AgentExecuteRequest):
    """向 Agent 子进程发送输入，SSE 流式返回输出"""
    if not agent_factory.is_running(agent_id):
        raise HTTPException(400, f"Agent '{agent_id}' 未启动，请先启动")

    import json as _json

    async def event_gen():
        async for event in agent_factory.execute(agent_id, req.content):
            yield {
                "event": event.get("event", "content"),
                "data": _json.dumps(event.get("data", {}), ensure_ascii=False),
            }

    return EventSourceResponse(event_gen())


@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str):
    """停止 Agent 子进程"""
    return await agent_factory.stop(agent_id)


@router.post("/{agent_id}/restart")
async def restart_agent(agent_id: str):
    """重启 Agent 子进程"""
    return await agent_factory.restart(agent_id)

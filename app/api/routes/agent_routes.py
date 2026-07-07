"""Agent 管理路由 — 规格生成、代码生成、注册、运行时管理"""

import asyncio
import re
import time
from pathlib import Path
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

Agent 是工具和 API 的编排者——其核心职责是接收输入，按流程调用工具（【【工具名】】）和系统 API（【API名】），处理中间结果，最终汇总输出。

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

{用 2-3 句话描述 Agent 做什么：接收什么输入，通过哪些关键步骤，输出什么结果}

## 2. 角色定位

- **角色类型**: {interactive|task|orchestrator|observer}
- **在系统中的位置**: {描述在四层架构中的位置及与交互 Agent 的关系}
- **协作对象**: {列出协作的其他 Agent 或组件}

## 3. 输入规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 用户输入的原始文本，赋值给变量 {变量名} |

{如用户描述中有 "输入：{变量名}"，则列出该变量；如有多个变量，逐行列出；说明中注明变量用途}

## 4. 输出规范

| 事件类型 | 说明 | 数据结构 |
|----------|------|---------|
| content | 文本输出（流式） | `{"text": "..."}` |
| card | 结构化卡片（结果摘要/数据预览等） | `{"type": "...", "title": "...", "data": {...}}` |
| done | 执行结束 | `{"messageId": "..."}` |
| error | 错误信息 | `{"code": "...", "message": "..."}` |

## 5. API 调用

{如果用户描述中包含【xxx】标记，说明调用了系统 API。每个 API 独立一节}

### 5.1 【API名称】

- **调用时机**: {在处理流程的哪个阶段调用}
- **输入参数**:

| 参数名 | 来源 | 说明 |
|--------|------|------|
| {参数1} | {来自Agent输入 / 来自上一步输出} | {说明} |

- **输出**:

| 字段 | 类型 | 说明 |
|------|------|------|
| {字段1} | {类型} | {说明} |

- **异常处理**: {调用失败时的处理策略}

{如果有多个 API，重复以上结构}

## 6. 工具调用

{如果用户描述中包含【【xxx】】标记，说明调用了注册工具。每个工具独立一节}

### 6.1 【【工具名称】】

- **调用时机**: {在处理流程的哪个阶段调用}
- **输入参数**:

| 参数名 | 来源 | 说明 |
|--------|------|------|
| {参数1} | {来自Agent输入 / 来自上一步输出 / 固定值} | {说明} |

- **输出**:

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | 执行状态 (success/failed) |
| output_format | string | text / image / table / file |
| message | string | 结果说明 |
| data | dict | 输出数据 |

- **异常处理**: {工具返回 failed 时的处理策略}

{如果有多个工具，重复以上结构}

## 7. 执行流程

{用编号步骤描述完整的数据处理管线，明确每一步的输入←来源和输出→去向}

1. **接收输入**: 解析 Agent 输入中的 content，赋值给变量 {变量名}
2. **{步骤名称}**: 调用 {API/工具名}，传入 {变量名}，得到 {输出描述}，存入 {结果变量}
3. **{步骤名称}**: 将上一步的 {结果变量.字段} 作为输入，调用 {API/工具名}
4. **结果汇总**: 整合各步骤输出，生成最终响应

{如有分支逻辑用 if/else 描述}

## 8. 错误处理

| 异常场景 | 处理方式 |
|----------|---------|
| API 调用失败 | {重试/降级/返回错误} |
| 工具执行失败 | {检查 status=failed，返回错误信息} |
| 输入参数缺失 | {引导用户补充 / 使用默认值} |
| 超时 | {60s 超时，返回部分结果} |

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | {今天日期} | 初始版本 |

## 规则

1. agent-id 用英文小写+连字符，如 "eeg-analysis-agent"
2. role 根据用户描述推断：简单处理→task, 多步管线→task, 需要协调多个Agent→orchestrator
3. 【重要】用户描述中的【API名称】和【【工具名称】】标记必须原封不动保留
4. 【重要】用户描述中的 {变量名} 是变量引用，需在 MD 中保留变量名并在执行流程中体现数据传递
5. 【重要】"agent 输入：{变量名}" 或 "输入：{变量名}" 表示将用户输入的内容赋值给该变量；输入规范中应列出该变量，执行流程第一步应写 "解析 Agent 输入，赋值给 {变量名}"
6. API 调用参数的"来源"列必须明确：来自Agent输入变量 {变量名} / 来自第N步输出.字段名 / 固定值
7. 执行流程中每一步的输入和输出要可追溯，形成清晰的数据流，用 {变量名} 标注数据在步骤间的传递
8. 如果用户未描述完整的输入/输出/异常处理，根据上下文合理推断填充
9. 只输出 Markdown，不要额外解释
10. frontmatter 中的 created 使用实际日期"""


@router.post("/generate-spec")
async def generate_spec(req: GenerateSpecRequest):
    """自然语言描述 → Agent MD 规范文档（含工具/API 参数参考）"""
    if not req.description.strip():
        raise HTTPException(400, "description 不能为空")

    # ── 解析用户描述中的【【工具名】】和【API名】引用，查询参数信息 ──
    tool_refs = AgentCodeBuilder._extract_tool_refs(req.description)
    api_refs = AgentCodeBuilder._extract_api_refs(req.description)

    context_parts = []

    if tool_refs:
        context_parts.append("## 引用的工具参数要求\n")
        for tr in tool_refs:
            params_desc = ""
            for p in tr.get("params", []):
                req_mark = "（必填）" if p.get("required") else "（可选）"
                default = f"，默认值: {p['default']}" if p.get("default") else ""
                params_desc += f"- {p['name']} ({p.get('type', 'string')}) {req_mark}: {p.get('desc', '')}{default}\n"
            if not params_desc:
                params_desc = "（无参数定义）\n"

            # 尝试从工具 MD spec 读取输出格式说明
            tools_def_dir = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "tools" / "definitions"
            spec_file = tools_def_dir / f"{tr['id']}.md"
            output_info = ""
            if spec_file.exists():
                spec_text = spec_file.read_text()
                # 提取输出规范段落
                out_match = re.search(r'## 3\.\s*输出规范.*?(?=## \d\.|\Z)', spec_text, re.DOTALL)
                if out_match:
                    out_section = out_match.group(0)
                    # 提取 output_format
                    fmt_match = re.search(r'output_format\s*\|\s*(\w+)', out_section)
                    if fmt_match:
                        output_info = f"，输出格式: {fmt_match.group(1)}"

            context_parts.append(
                f"### 【【{tr['name']}】】（tool_id: {tr['id']}）\n"
                f"**输入参数**:\n{params_desc}"
                f"**工具返回**: dict，包含 status (success/failed), message (结果说明), data (输出数据){output_info}\n"
            )

    if api_refs:
        context_parts.append("\n## 引用的 API 参数要求\n")
        for ar in api_refs:
            params_desc = ""
            for k, v in ar.get("params", {}).items():
                params_desc += f"- {k}: {v}\n"
            if not params_desc:
                params_desc = "（无参数定义）\n"
            context_parts.append(
                f"### 【{ar['name']}】（api_id: {ar['id']}）\n"
                f"**输入参数**:\n{params_desc}\n"
            )

    tool_context = "\n".join(context_parts) if context_parts else ""

    # ── 构建增强的 user prompt ──
    user_prompt = req.description
    if tool_context:
        user_prompt += f"\n\n---\n## 系统提供的工具/API 参考信息（请据此填充 MD 中的参数和输出）\n\n{tool_context}"

    response = await llm.chat(
        messages=[
            {"role": "system", "content": SPEC_GENERATION_PROMPT},
            {"role": "user", "content": user_prompt},
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

    # 保存用户需求描述
    if req.demand_desc.strip():
        demand_path = registry._get_spec_dir() / f"{registered_id}-demand.md"
        demand_path.write_text(req.demand_desc)

    return {"agent_id": registered_id, "entry": entry}


@router.get("/list")
async def list_agents():
    """列出所有已注册的 Agent"""
    agents = await registry.list_all()
    return {"agents": agents}


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Agent 详情（含 MD spec + 源代码 + 需求描述）"""
    entry = await registry.get(agent_id)
    if not entry:
        raise HTTPException(404, f"Agent '{agent_id}' not found")

    spec_path = registry._get_spec_dir() / f"{agent_id}.md"
    spec_md = spec_path.read_text() if spec_path.exists() else ""

    code_path = registry._get_impl_dir() / agent_id / "agent.py"
    code = code_path.read_text() if code_path.exists() else ""

    demand_path = registry._get_spec_dir() / f"{agent_id}-demand.md"
    has_demand = demand_path.exists()
    demand_md = demand_path.read_text() if has_demand else ""

    return {**entry, "spec_md": spec_md, "code": code, "has_demand": has_demand, "demand_md": demand_md}


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
        try:
            async for event in agent_factory.execute(agent_id, req.content):
                yield {
                    "event": event.get("event", "content"),
                    "data": _json.dumps(event.get("data", {}), ensure_ascii=False),
                }
        except Exception as e:
            import traceback, logging
            logging.getLogger("sotaband").error(f"Agent {agent_id} 执行异常: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": _json.dumps({"message": f"Agent 执行异常: {str(e)[:200]}"}, ensure_ascii=False),
            }
        finally:
            yield {
                "event": "done",
                "data": _json.dumps({"messageId": f"msg-{int(time.time() * 1000)}"}, ensure_ascii=False),
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


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """删除 Agent（registry + code + 停止进程）"""
    entry = await registry.get(agent_id)
    if not entry:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    import shutil
    # 停止进程
    await agent_factory.stop(agent_id)
    # 删除代码
    impl_dir = registry._get_impl_dir() / agent_id
    if impl_dir.exists():
        shutil.rmtree(impl_dir, ignore_errors=True)
    # 删除 MD
    spec_path = registry._get_spec_dir() / f"{agent_id}.md"
    if spec_path.exists():
        spec_path.unlink()
    await registry.unregister(agent_id)
    return {"deleted": agent_id}

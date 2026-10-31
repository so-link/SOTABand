"""Agent 管理路由 — 规格生成、代码生成、注册、运行时管理"""

import asyncio
import json
import re
import time
from pathlib import Path
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
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


async def _stream_with_heartbeat(coro, *, interval: float = 12.0):
    """把一个长时间运行的协程包装成「心跳 + 最终 JSON」的流式响应。

    生成规范 / 生成代码这类接口会等 LLM 跑完才能返回，中间代理（IDE 预览、
    Nginx 等）常在 60s 无字节时主动断开并回 504。本生成器在协程运行期间
    每隔 ``interval`` 秒下发一个换行（合法 JSON 前导空白，``JSON.parse``
    会忽略），保持连接活跃；协程结束后再一次性输出 JSON 结果。
    协程抛异常时输出 ``{"detail": "..."}``，前端按错误统一处理。
    """
    task = asyncio.create_task(coro)
    # 用轮询而非 wait_for(shield(task))：后者会把 task 的异常直接重抛，
    # 导致 HTTPException 逃出 StreamingResponse，触发 "response already started"。
    elapsed = 0.0
    granularity = 1.0
    while not task.done():
        await asyncio.sleep(granularity)
        elapsed += granularity
        if elapsed >= interval:
            yield b"\n"
            elapsed = 0.0
    exc = task.exception()
    if exc is not None:
        # HTTPException 本身携带 status_code，但流式响应头已发出，只能把
        # 信息塞进 detail 由前端展示；非 4xx 错误保留异常类型便于排障。
        if isinstance(exc, HTTPException):
            detail = exc.detail
        else:
            detail = f"{type(exc).__name__}: {exc}"
        payload = {"detail": detail}
    else:
        payload = task.result()
        if not isinstance(payload, dict):
            payload = {"result": payload}
    yield json.dumps(payload, ensure_ascii=False, default=str).encode()


SPEC_GENERATION_PROMPT = """你是一个 Agent 规格文档生成器。根据用户的自然语言描述，生成标准化的 Agent MD 规范文档。

Agent 是工具和 API 的编排者——其核心职责是接收输入，按流程调用工具（【【工具名】】）和系统 API（【API名】），处理中间结果，最终汇总输出。

## 输出格式

严格按照以下 Markdown 模板输出：

---
id: {agent-id}
name: {Agent名称}
version: 0.1.0
role: {interactive|task|web-service}
status: active
created: {今天日期}
---

# {Agent名称}

## 1. 功能概述

{用 2-3 句话描述 Agent 做什么：接收什么输入，通过哪些关键步骤，输出什么结果}

## 2. 角色定位

- **角色类型**: {interactive|task|web-service}
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

    async def _do():
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

                # 尝试从工具 MD spec 读取输出格式说明（内置 + 私有目录检索）
                from core.resource import paths as _paths
                spec_file = None
                for _root in _paths.resource_roots():
                    _cand = _root / "tools" / "definitions" / f"{tr['id']}.md"
                    if _cand.exists():
                        spec_file = _cand
                        break
                output_info = ""
                if spec_file is not None and spec_file.exists():
                    spec_text = spec_file.read_text(encoding="utf-8")
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

        # ── 按 Agent 类型注入专属结构约束（见 docs/agent-types-design.md） ──
        role_guidance = _role_spec_guidance(req.role)
        if role_guidance:
            user_prompt += f"\n\n---\n## Agent 类型约束（必须严格遵守）\n\n{role_guidance}"

        response = await llm.chat(
            messages=[
                {"role": "system", "content": SPEC_GENERATION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=100000,
        )

        return {"spec_md": response.strip()}

    return StreamingResponse(
        _stream_with_heartbeat(_do()),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# 三类 Agent 的 MD 结构约束（role → 提示词片段）
_ROLE_SPEC_GUIDANCE = {
    "interactive": (
        "- role 字段必须为 `interactive`。\n"
        "- 该 Agent 通过多轮对话理解用户意图并调用合适的工具完成任务。\n"
        "- 输入规范应包含 `content`（用户消息）。\n"
        "- 无需声明执行流程或对外 API。"
    ),
    "task": (
        "- role 字段必须为 `task`。\n"
        "- 该 Agent 按**既定流程**依次调用工具完成任务（不由 LLM 动态决策）。\n"
        "- **必须在 MD 中包含名为「## 输入」的段落**，用 `- 参数名: 说明（类型，必填/可选）` 列出启动参数。\n"
        "- **必须包含一个 ```flow 代码块**（JSON 数组），逐步声明流程，格式：\n"
        "```flow\n"
        "[\n"
        "  {\"step\": 1, \"tool\": \"<tool-id>\", \"params\": {\"参数\": \"{{input.启动参数名}}\"}, \"output_bind\": \"step1\"},\n"
        "  {\"step\": 2, \"tool\": \"<tool-id>\", \"params\": {\"参数\": \"{{step1.data.字段}}\"}, \"output_bind\": \"step2\"}\n"
        "]\n"
        "```\n"
        "- 参数引用语法：`{{input.xxx}}` 引用启动参数；`{{stepN.data.xxx}}` 引用第 N 步输出。\n"
        "- tool 的值必须是真实存在的工具 id（参考上文工具信息）。"
    ),
    "web-service": (
        "- role 字段必须为 `web-service`。\n"
        "- 该 Agent 以 HTTP API 形式对外提供服务，对外暴露**一个或多个 API**。\n"
        "- **必须包含一个 ```server 代码块**（JSON 对象），如 `{\"host\": \"127.0.0.1\", \"port\": 0}`（port=0 表示自动分配）。\n"
        "- **必须包含一个 ```apis 代码块**（JSON 数组），每个 API 声明 name/method/path/params/flow/response，格式：\n"
        "```apis\n"
        "[\n"
        "  {\n"
        "    \"name\": \"api名称\", \"method\": \"POST\", \"path\": \"/路径\",\n"
        "    \"params\": [{\"name\": \"参数名\", \"type\": \"string\", \"required\": true, \"desc\": \"说明\"}],\n"
        "    \"flow\": [{\"tool\": \"<tool-id>\", \"params\": {\"参数\": \"{{param.参数名}}\"}}],\n"
        "    \"response\": [{\"name\": \"返回字段\", \"from\": \"{{step1.data.字段}}\"}]\n"
        "  }\n"
        "]\n"
        "```\n"
        "- 参数引用语法：`{{param.xxx}}` 引用请求参数；`{{stepN.data.xxx}}` 引用第 N 步输出。\n"
        "- tool 的值必须是真实存在的工具 id（参考上文工具信息）。"
    ),
}


def _role_spec_guidance(role: str | None) -> str:
    """返回指定 Agent 类型的 MD 结构约束提示词片段。"""
    if not role:
        return ""
    key = (role or "").strip().lower()
    if key in ("web_service", "webservice"):
        key = "web-service"
    return _ROLE_SPEC_GUIDANCE.get(key, "")


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

    async def _do():
        valid = await builder.validate_spec(spec)
        if not valid:
            raise HTTPException(400, "MD 规范文档不完整，缺少必需段落")

        code = await builder.build(spec)

        # 沙箱测试
        sandbox = await builder.dry_run(code)

        return {"code": code, "sandbox_results": sandbox}

    return StreamingResponse(
        _stream_with_heartbeat(_do()),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── 自动调试（todo 4.3）──

@router.post("/auto-debug")
async def agent_auto_debug(
    spec_md: str = Form(""),
    agent_id: str = Form(""),
    agent_name: str = Form(""),
    role: str = Form("task"),
    code: str = Form(""),
    test_input_json: str = Form("{}"),
):
    """Agent 代码自动调试：SSE 流式返回每轮试运行结果和 LLM 分析。

    循环：沙箱试运行（entrypoint 子进程，按角色语义验证）→ LLM 分析错误 →
    整文件重写 → 每轮重写后重跑静态安全审查与试运行，最多 max_rounds 轮。
    用户关闭 SSE 连接（前端 AbortController.abort()）时自动停止。
    """
    import json as _json

    try:
        test_input = _json.loads(test_input_json or "{}")
    except _json.JSONDecodeError:
        raise HTTPException(400, "test_input_json 不是合法 JSON")
    if not isinstance(test_input, dict):
        raise HTTPException(400, "test_input_json 必须是 JSON 对象")

    agent_id = (agent_id or "").strip() or "custom-agent"
    role = (role or "task").strip().lower()
    if role in ("web_service", "webservice"):
        role = "web-service"

    if not code.strip():
        if not spec_md.strip():
            raise HTTPException(400, "code 与 spec_md 不能同时为空")
        spec = {"raw_md": spec_md, "id": agent_id, "name": agent_name, "role": role}
        code = await builder.build(spec)

    from core.resource.builder.agent_builder import stop_debug

    stop_event = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()

    async def _producer():
        try:
            async for event in builder.auto_debug_stream(
                spec_md, code, test_input, agent_id, role=role, stop_event=stop_event
            ):
                await queue.put(event)
        except asyncio.CancelledError:
            stop_event.set()
        finally:
            await queue.put(None)

    producer_task = asyncio.create_task(_producer())

    async def event_stream():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {"event": event["event"], "data": _json.dumps(event, ensure_ascii=False)}
        except asyncio.CancelledError:
            stop_event.set()
            producer_task.cancel()
            try:
                await asyncio.wait_for(producer_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    return EventSourceResponse(event_stream())


@router.post("/auto-debug/stop")
async def agent_stop_auto_debug(req: dict):
    """停止指定 Agent 的自动调试。

    前端点击停止按钮、页面关闭、网络断开时都应调用此端点；
    即使 SSE 连接已断开，也能通过全局标志可靠停止后台调试。
    """
    from core.resource.builder.agent_builder import stop_debug

    agent_id = (req.get("agent_id") or "").strip()
    if not agent_id:
        raise HTTPException(400, "缺少 agent_id")
    stop_debug(agent_id)
    return {"status": "ok", "message": f"已请求停止 Agent {agent_id} 的调试"}


# ═══════════════════════════════════════════════════════════════
#  Agent 间通信总线（todo 4.5）— 主进程路由中枢的对外入口
#  注意：必须注册在 /{agent_id}/... 动态路由之前（静态路径优先匹配）
# ═══════════════════════════════════════════════════════════════

@router.post("/bus/publish")
async def bus_publish(req: dict):
    """外部系统向总线频道发布消息，投递给所有订阅该频道的运行中 Agent。"""
    channel = str(req.get("channel") or "").strip()
    if not channel:
        raise HTTPException(400, "缺少 channel")
    return await agent_factory.bus_publish(
        channel, req.get("message") if isinstance(req.get("message"), dict) else {},
        src=str(req.get("src") or "main"))


@router.post("/bus/call")
async def bus_call(req: dict):
    """外部系统以 RPC 方式调用一个运行中的 Agent（被调方 execute 聚合返回）。"""
    to = str(req.get("to") or "").strip()
    method = str(req.get("method") or "").strip()
    if not to or not method:
        raise HTTPException(400, "缺少 to 或 method")
    params = req.get("params") if isinstance(req.get("params"), dict) else {}
    try:
        timeout = float(req.get("timeout") or 60)
    except (TypeError, ValueError):
        timeout = 60.0
    result = await agent_factory.call_agent(
        str(req.get("from") or "main"), to, method, params, timeout=timeout)
    return result


@router.get("/bus/status")
async def bus_status():
    """总线可观测快照：各频道订阅者与悬挂 RPC 数。"""
    return agent_factory.bus_status()


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

    if req.code.strip():
        from core.security.code_scan import scan_code
        scan = scan_code(req.code)
        if not scan.ok:
            raise HTTPException(400, "静态安全审查未通过：\n" + "\n".join(scan.errors))

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

    # 保存生成的代码到 implementations 目录（已在注册前通过静态安全审查）
    if req.code.strip():
        impl_dir = registry._get_impl_dir() / registered_id
        impl_dir.mkdir(parents=True, exist_ok=True)
        (impl_dir / "agent.py").write_text(req.code, encoding="utf-8")
        (impl_dir / "spec.md").write_text(req.spec_md, encoding="utf-8")
        (impl_dir / "config.yaml").write_text(
            f"# {req.agent_name} 运行时配置\n"
            f"role: {req.role}\n"
            f"version: {req.version}\n",
            encoding="utf-8",
        )

    # 保存用户需求描述（中文内容，Windows 默认 GBK，必须显式 utf-8）
    if req.demand_desc.strip():
        demand_path = registry._get_spec_dir() / f"{registered_id}-demand.md"
        demand_path.write_text(req.demand_desc, encoding="utf-8")

    # Web 服务式 Agent：生成 API 说明文档（api.md），随 Agent 一并产出
    _maybe_generate_api_doc(registered_id)

    return {"agent_id": registered_id, "entry": entry}


def _locate_agent_spec_path(agent_id: str) -> Path | None:
    """在「内置 + 私有」资源根中定位已有规范文档；不存在返回 None。"""
    from core.resource import paths as _paths

    for root in _paths.resource_roots():
        f = root / "agents" / "definitions" / f"{agent_id}.md"
        if f.exists():
            return f
    return None


def _locate_agent_impl_dir(agent_id: str) -> Path | None:
    """在「内置 + 私有」资源根中定位已有实现目录；不存在返回 None。"""
    from core.resource import paths as _paths

    for root in _paths.resource_roots():
        d = root / "agents" / "implementations" / agent_id
        if d.exists():
            return d
    return None


def _maybe_generate_api_doc(agent_id: str) -> None:
    """若该 Agent 为 Web 服务式且声明了 API，则生成 api.md 说明文档。"""
    try:
        from core.agent.base import AgentSpec
        from core.agent.entrypoint import _parse_role_from_md
        from core.resource.builder.api_doc_builder import build_api_doc_markdown

        base = registry._get_spec_dir()
        spec_file = base / f"{agent_id}.md"
        if not spec_file.exists():
            return
        raw = spec_file.read_text(encoding="utf-8")
        spec = AgentSpec(id=agent_id, name=agent_id, role=_parse_role_from_md(raw), raw_md=raw)
        spec.parse_structured_fields()
        if not spec.apis:
            return
        md = build_api_doc_markdown(agent_id, spec.name or agent_id, spec)
        impl_dir = registry._get_impl_dir() / agent_id
        impl_dir.mkdir(parents=True, exist_ok=True)
        (impl_dir / "api.md").write_text(md, encoding="utf-8")
    except Exception as e:
        import logging
        logging.getLogger("sotaband").warning(f"生成 API 文档失败 ({agent_id}): {e}")


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

    # 在「内置 + 私有」资源根中检索规范 / 实现 / 需求文档
    from core.resource import paths as _paths

    spec_path = _locate_agent_spec_path(agent_id)
    spec_md = spec_path.read_text(encoding="utf-8") if spec_path else ""

    impl_dir = _locate_agent_impl_dir(agent_id)
    code = ""
    if impl_dir and (impl_dir / "agent.py").exists():
        code = (impl_dir / "agent.py").read_text(encoding="utf-8")

    demand_path = None
    for root in _paths.resource_roots():
        cand = root / "agents" / "definitions" / f"{agent_id}-demand.md"
        if cand.exists():
            demand_path = cand
            break
    has_demand = demand_path is not None
    demand_md = demand_path.read_text(encoding="utf-8") if has_demand else ""

    return {**entry, "spec_md": spec_md, "code": code, "has_demand": has_demand, "demand_md": demand_md}


class SaveSpecRequest(BaseModel):
    """保存 Agent MD 规范文档请求"""
    spec_md: str


@router.post("/{agent_id}/save-spec")
async def save_agent_spec(agent_id: str, req: SaveSpecRequest):
    """保存手工编辑后的 MD 规范文档"""
    entry = await registry.get(agent_id)
    if not entry:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    if not req.spec_md.strip():
        raise HTTPException(400, "文档内容不能为空")

    await registry.update(agent_id, {"raw_md": req.spec_md})
    # 优先写回原位置（内置资源就地更新）；不存在则写私有目录
    spec_path = _locate_agent_spec_path(agent_id) or (registry._get_spec_dir() / f"{agent_id}.md")
    impl_dir = _locate_agent_impl_dir(agent_id) or (registry._get_impl_dir() / agent_id)
    impl_dir.mkdir(parents=True, exist_ok=True)
    for p in (spec_path, impl_dir / "spec.md"):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(req.spec_md, encoding="utf-8")
        except Exception:
            pass
    return {"saved": agent_id}


class SaveCodeRequest(BaseModel):
    """保存 Agent 代码请求"""
    code: str


@router.post("/{agent_id}/save-code")
async def save_agent_code(agent_id: str, req: SaveCodeRequest):
    """保存手工微调后的 Agent 代码"""
    entry = await registry.get(agent_id)
    if not entry:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    if not req.code.strip():
        raise HTTPException(400, "代码不能为空")

    from core.security.code_scan import scan_code
    scan = scan_code(req.code)
    if not scan.ok:
        raise HTTPException(400, "静态安全审查未通过：\n" + "\n".join(scan.errors))

    # 优先写回原位置（内置资源就地更新）；不存在则写私有目录
    impl_dir = _locate_agent_impl_dir(agent_id) or (registry._get_impl_dir() / agent_id)
    impl_dir.mkdir(parents=True, exist_ok=True)
    (impl_dir / "agent.py").write_text(req.code, encoding="utf-8")
    return {"saved": agent_id}


class SyncSpecRequest(BaseModel):
    """代码 → 文档反向同步请求"""
    agent_id: str
    code: str
    original_code: str = ""
    current_spec: str = ""


@router.post("/{agent_id}/sync-spec-from-code")
async def sync_spec_from_code(agent_id: str, req: SyncSpecRequest):
    """依据使用者微调后的代码，反向更新 Agent 的 MD 规范文档。

    打通「文档 → 代码 → 文档」的双向闭环，避免文档与代码脱节。
    """
    entry = await registry.get(agent_id)
    if not entry:
        raise HTTPException(404, f"Agent '{agent_id}' not found")

    code = req.code or ""
    if not code.strip():
        raise HTTPException(400, "代码不能为空")

    current_spec = req.current_spec or ""
    if not current_spec.strip():
        current_spec = f"# {entry.get('name', agent_id)}\n\n（暂无历史规范文档，以下由代码反推生成）\n"

    MAX_CHARS = 24000

    def _clip(text: str) -> str:
        if not text:
            return "(空)"
        return text if len(text) <= MAX_CHARS else (
            text[:MAX_CHARS] + f"\n...[内容过长已截断，共 {len(text)} 字符]..."
        )

    system_prompt = (
        "你是 SOTABand Agent 规范文档的维护专家。使用者手工修改了一个 Agent 的代码，"
        "请你据此更新该 Agent 的 MD 规范文档。\n\n"
        "【核心原则】\n"
        "1. 只更新受本次代码改动实际影响的段落。\n"
        "2. 未受影响的段落必须原样保留，不要改写。\n"
        "3. 严格保持原文档的 Markdown 结构、标题层级与表格格式，"
        "并保留 frontmatter（--- 之间的 id/name/version/role/status/created）。\n"
        "4. 若改动引入了新的输入参数或输出字段，必须同步对应表格。\n"
        "5. Agent 是工具与 API 的编排者：若改动了调用的工具（【【工具名】】）"
        "或系统 API（【API名】），必须在文档中同步更新。\n"
        "6. 只输出更新后的完整 MD 文档，不要任何解释性文字，不要 markdown 代码块包裹。"
    )

    user_prompt = f"""Agent 名称：{entry.get('name', agent_id)}

=== 当前 MD 规范文档 ===
{_clip(current_spec)}

=== 修改前的代码 ===
```python
{_clip(req.original_code)}
```

=== 修改后的代码（使用者手工微调的结果）===
```python
{_clip(code)}
```

请输出更新后的完整 MD 规范文档："""

    updated = await llm.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=100000,
    )

    cleaned = (updated or "").strip()
    for marker in ("```markdown", "```md", "```"):
        if cleaned.startswith(marker):
            cleaned = cleaned[len(marker):].strip()
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3].rstrip()
            break

    if not cleaned:
        raise HTTPException(500, "同步失败：模型未返回有效文档")

    # 落盘：更新 registry 的 raw_md 与 spec 目录下的两份文档
    await registry.update(agent_id, {"raw_md": cleaned})
    for p in (registry._get_spec_dir() / f"{agent_id}.md",
              registry._get_impl_dir() / agent_id / "spec.md"):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(cleaned, encoding="utf-8")
        except Exception:
            pass

    return {"spec_md": cleaned}


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

    # 实现路径交由 agent_factory 在「内置 + 私有」资源根中自动检索，
    # 不在此硬编码（否则私有目录中的 Agent 无法启动）。
    return await agent_factory.start(agent_id)


@router.post("/{agent_id}/execute")
async def execute_agent(agent_id: str, req: AgentExecuteRequest):
    """向 Agent 子进程发送输入，SSE 流式返回输出"""
    if not agent_factory.is_running(agent_id):
        raise HTTPException(400, f"Agent '{agent_id}' 未启动，请先启动")

    import json as _json

    async def event_gen():
        seen_done = False
        try:
            async for event in agent_factory.execute(agent_id, req.content):
                ev_type = event.get("event", "content")
                if ev_type == "done":
                    seen_done = True
                yield {
                    "event": ev_type,
                    "data": _json.dumps(event.get("data", {}), ensure_ascii=False),
                }
        except Exception as e:
            import traceback, logging
            logging.getLogger("sotaband").error(f"Agent {agent_id} 执行异常: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": _json.dumps({"message": f"Agent 执行异常: {str(e)[:200]}"}, ensure_ascii=False),
            }
        # 仅在 Agent 未自行发送 done 时补发，避免前端收到两个 done 导致消息重复展示。
        if not seen_done:
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

# ══════════════════════════════════════════════════════════════════════
#  Web 服务式 Agent 支持（见 docs/agent-types-design.md）
# ══════════════════════════════════════════════════════════════════════

@router.get("/{agent_id}/service")
async def get_agent_service(agent_id: str):
    """返回 web-service Agent 的 HTTP 服务信息（host / port / apis）。

    apis 始终返回**完整定义**（含 name/method/path/params），
    以便前端渲染参数输入框——无论服务是否已启动。
    运行状态（running/host/port）来自工厂。
    """
    # 始终从 MD 规范解析完整 API 定义（含 params）
    spec = _load_agent_spec(agent_id)
    apis: list[dict] = []
    if spec is not None:
        apis = [
            {
                "name": a.get("name"),
                "method": a.get("method", "POST"),
                "path": a.get("path"),
                "description": a.get("description", "") or a.get("desc", ""),
                "params": a.get("params", []) or [],
            }
            for a in (spec.apis or [])
        ]

    svc = agent_factory.get_service(agent_id)
    if svc:
        return {
            "agent_id": agent_id,
            "running": True,
            "host": svc.get("host"),
            "port": svc.get("port"),
            "apis": apis,
        }

    return {"agent_id": agent_id, "running": False, "host": None, "port": None, "apis": apis}


class TestApiRequest(BaseModel):
    api_name: str
    params: dict = {}


@router.post("/{agent_id}/test-api")
async def test_agent_api(agent_id: str, req: TestApiRequest):
    """API 测试：由后端内部代理请求到该 Agent 的 HTTP 服务，返回响应结果。

    走本机回环（127.0.0.1），避免前端跨域与外部网络问题。
    """
    try:
        import httpx
    except ModuleNotFoundError:  # 部分环境仅提供 httpx2（同 API 的延续包）
        import httpx2 as httpx

    svc = agent_factory.get_service(agent_id)
    if not svc or not svc.get("port"):
        raise HTTPException(400, f"Agent '{agent_id}' 的 HTTP 服务未运行，请先启动")

    # 在 API 定义中查找路径与方法（复用双根检索，内置 Agent 的规范同样可见）
    spec = _load_agent_spec(agent_id)
    api_def = None
    for a in (spec.apis or []) if spec else []:
        if a.get("name") == req.api_name:
            api_def = a
            break

    if api_def is None:
        raise HTTPException(404, f"未找到 API '{req.api_name}'")

    host = svc.get("host") or "127.0.0.1"
    port = svc["port"]
    path = api_def.get("path", "/")
    method = api_def.get("method", "POST").upper()
    url = f"http://{host}:{port}{path}"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            if method == "GET":
                resp = await client.get(url, params=req.params)
            else:
                resp = await client.request(method, url, json=req.params)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return {"status_code": resp.status_code, "result": body}
    except Exception as e:
        raise HTTPException(500, f"测试请求失败: {e}")


# ══════════════════════════════════════════════════════════════════════
#  Web 服务式 Agent —— API 说明文档（生成 / 浏览 / 下载）
# ══════════════════════════════════════════════════════════════════════

def _load_agent_spec(agent_id: str):
    """从注册表与 MD 规范加载 Agent 的 spec（含 server/apis 解析）。

    在「内置 + 私有」资源根中检索规范文档。
    """
    entry = _read_registry_entry(agent_id)
    spec_name = entry.get("spec_path") if entry else None
    agent_name = (entry or {}).get("name") or agent_id

    from core.resource import paths as _paths

    candidates = []
    for root in _paths.resource_roots():
        d = root / "agents" / "definitions"
        if spec_name:
            candidates.append(d / Path(spec_name).name)
        candidates.append(d / f"{agent_id}.md")

    for cand in candidates:
        if cand and cand.exists():
            from core.agent.base import AgentSpec
            from core.agent.entrypoint import _parse_role_from_md

            raw = cand.read_text(encoding="utf-8")
            spec = AgentSpec(id=agent_id, name=agent_name,
                             role=_parse_role_from_md(raw), raw_md=raw)
            spec.parse_structured_fields()
            return spec
    return None


def _read_registry_entry(agent_id: str) -> dict | None:
    """同步读取注册表中的 Agent 条目（不依赖事件循环）。"""
    try:
        import json as _json

        from core.resource.registry import agent_registry as _ar

        reg_file = _ar.REGISTRY_FILE
        if not reg_file.exists():
            return None
        for a in _json.loads(reg_file.read_text(encoding="utf-8")):
            if a.get("id") == agent_id:
                return a
    except Exception:
        pass
    return None


@router.get("/{agent_id}/api-doc")
async def get_agent_api_doc(agent_id: str):
    """返回该 Web 服务式 Agent 的 API 说明文档（Markdown 文本）。"""
    spec = _load_agent_spec(agent_id)
    if spec is None:
        raise HTTPException(404, f"未找到 Agent '{agent_id}' 的规范文档")
    if not spec.apis:
        raise HTTPException(400, f"Agent '{agent_id}' 不是 Web 服务式 Agent 或未声明 API")

    from core.resource.builder.api_doc_builder import build_api_doc_markdown

    md = build_api_doc_markdown(agent_id, spec.name or agent_id, spec)
    return {"agent_id": agent_id, "markdown": md}


@router.get("/{agent_id}/api-doc/download")
async def download_agent_api_doc(agent_id: str):
    """下载该 Web 服务式 Agent 的 API 说明文档（.md 文件）。"""
    from fastapi.responses import PlainTextResponse

    spec = _load_agent_spec(agent_id)
    if spec is None:
        raise HTTPException(404, f"未找到 Agent '{agent_id}' 的规范文档")

    from core.resource.builder.api_doc_builder import build_api_doc_markdown

    md = build_api_doc_markdown(agent_id, spec.name or agent_id, spec)
    filename = f"{agent_id}-api.md"
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

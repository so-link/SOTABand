"""Agent 代码生成的角色契约测试。

覆盖历史 bug：web-service 类型的 Agent 被按 task 模板生成，代码读
``kwargs["content"]``，而 HTTP 宿主只传 ``api_name`` / ``params``，
导致所有请求都返回 ``Missing required parameter: xxx``。
"""

import pytest

from core.agent.base import AgentContext, AgentRole, AgentSpec, BaseAgent
from core.resource.builder.agent_builder import AgentCodeBuilder

WS_MD = """---
id: greeting-web-service-agent
name: 问候 Web 服务 Agent
version: 0.1.0
role: web-service
---

# 问候 Web 服务 Agent

## 1. 功能概述

接收 name，调用【【问候测试】】生成问候语。

## 2. 角色定位

- **角色类型**: web-service

## 3. 输入规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | 是 | 用户名字 |

## 4. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| greeting | string | 问候语 |

## 5. 运行机制

```server
{"host": "127.0.0.1", "port": 0}
```

```apis
[
  {
    "name": "greet",
    "method": "POST",
    "path": "/greet",
    "params": [{"name": "name", "type": "string", "required": true}],
    "flow": [{"tool": "test-greeting", "params": {"command": "{{param.name}}"}}],
    "response": [{"name": "greeting", "from": "{{step1.data.text}}"}]
  }
]
```

## 6. 错误处理

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-10-31 | 初始版本 |
"""

TASK_MD = """---
id: greet-task-agent
name: 问候任务 Agent
version: 0.1.0
role: task
---

# 问候任务 Agent

## 1. 功能概述

按流程生成问候语。

## 2. 角色定位

- **角色类型**: task

## 3. 输入规范

- name: 用户名字（string，必填）

## 4. 输出规范

| 事件类型 | 说明 |
|----------|------|
| content | 文本 |

## 5. 执行流程

```flow
[{"step": 1, "tool": "test-greeting", "params": {"command": "{{input.name}}"}, "output_bind": "step1"}]
```

## 6. 错误处理

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-10-31 | 初始版本 |
"""


async def _fake_call_tool(tool_id, params=None):
    return {"status": "success", "message": "ok",
            "data": {"text": f"{(params or {}).get('command')}, hello"}}


def _load_agent_class(code: str):
    ns: dict = {}
    exec(compile(code, "<generated-agent>", "exec"), ns)
    for obj in ns.values():
        if isinstance(obj, type) and issubclass(obj, BaseAgent) and obj is not BaseAgent:
            return obj
    raise AssertionError("生成的代码中未找到 BaseAgent 子类")


async def _run(agent, **kwargs):
    ctx = AgentContext(agent_id=agent.spec.id, session_id="s1")
    return [ev async for ev in agent.execute(ctx, **kwargs)]


def _make_agent(md: str, agent_id: str, role: AgentRole, code: str):
    spec = AgentSpec(id=agent_id, name=agent_id, role=role)
    spec.raw_md = md
    spec.parse_structured_fields()
    agent = _load_agent_class(code)(spec)
    agent.call_tool = _fake_call_tool
    return agent


def test_extract_role_keeps_hyphen():
    """role: web-service 不能被正则截断成 'web'。"""
    b = AgentCodeBuilder(llm_client=None)
    assert b._extract_role(WS_MD) == "web-service"
    assert b._extract_role("role: web_service") == "web-service"
    assert b._extract_role("role: interactive") == "interactive"
    assert b._extract_role("") == ""


def test_parse_spec_inputs_list_and_table():
    b = AgentCodeBuilder(llm_client=None)
    assert b._parse_spec_inputs(WS_MD) == [
        {"name": "name", "type": "string", "required": True, "desc": "用户名字"}
    ]
    parsed = b._parse_spec_inputs(TASK_MD)
    assert parsed[0]["name"] == "name" and parsed[0]["required"] is True


async def test_web_service_contract():
    """web-service 必须：入口 api_name/params、出口 result 事件、不读 content。"""
    b = AgentCodeBuilder(llm_client=None)
    code = await b.build({"raw_md": WS_MD, "id": "greeting-web-service-agent",
                          "name": "问候 Web 服务 Agent"})
    assert 'kwargs.get("content"' not in code
    assert "api_name" in code
    assert '"event": "result"' in code

    sandbox = await b.dry_run(code, role="web-service")
    assert sandbox["failed"] == []

    agent = _make_agent(WS_MD, "greeting-web-service-agent",
                        AgentRole.WEB_SERVICE, code)
    events = await _run(agent, api_name="greet", params={"name": "张三"})
    results = [e for e in events if e["event"] == "result"]
    assert results and results[0]["data"]["response"] == {"greeting": "张三, hello"}

    events = await _run(agent, api_name="greet", params={})
    errors = [e for e in events if e["event"] == "error"]
    assert errors and "缺少必填参数: name" in errors[0]["data"]["message"]


async def test_task_contract():
    """task 必须按 MD 声明的启动参数名取值，而不是猜 content。"""
    b = AgentCodeBuilder(llm_client=None)
    code = await b.build({"raw_md": TASK_MD, "id": "greet-task-agent",
                          "name": "问候任务 Agent"})
    assert "_declared" in code

    sandbox = await b.dry_run(code, role="task")
    assert sandbox["failed"] == []

    agent = _make_agent(TASK_MD, "greet-task-agent", AgentRole.TASK, code)
    events = await _run(agent, name="张三")
    assert events[-1]["event"] == "done"

    events = await _run(agent)
    errors = [e for e in events if e["event"] == "error"]
    assert errors and "缺少必填参数: name" in errors[0]["data"]["message"]


def test_resolve_refs_supports_double_braces():
    """MD 里统一写 {{...}}，必须与 {...} 一样可解析。"""
    scope = {"param": {"name": "张三"}, "input": {"n": 3}, "step1": {"data": {"text": "hi"}}}
    assert BaseAgent.resolve_refs("{{param.name}}", scope) == "张三"
    assert BaseAgent.resolve_refs("{{step1.data.text}}", scope) == "hi"
    assert BaseAgent.resolve_refs("{input.n}", scope) == 3
    assert BaseAgent.resolve_refs("{param.name}-{{input.n}}", scope) == "张三-3"


# ══════════════════════════════════════════════════════════════════════
# 工具引用解析：MD 里写【【问候程序】】，实现目录叫 greet-tool
# （历史 bug：把显示名当 id → 工具代码不存在 .../implementations/问候程序/tool.py）
# ══════════════════════════════════════════════════════════════════════

# 模拟用户真实 MD：正文用【【显示名】】，flow 里也写成显示名
WS_MD_ZH = (
    WS_MD.replace("接收 name，调用【【问候测试】】生成问候语。",
                  "接收 name，调用【【问候程序】】生成问候语。")
         .replace('"tool": "test-greeting"', '"tool": "问候程序"')
)


def _make_tool_space(tmp_path, monkeypatch):
    """构造一个只含「显示名 ≠ id」的临时工具资源根。"""
    import json as _json

    root = tmp_path / "resources"
    impl = root / "tools" / "implementations" / "greet-tool"
    impl.mkdir(parents=True)
    (impl / "tool.py").write_text("def execute(**kwargs):\n    return {}\n", encoding="utf-8")
    (root / "tools" / "registry.json").write_text(
        _json.dumps([{
            "id": "greet-tool",
            "name": "问候程序",
            # 与 WS_MD 的 flow 保持一致：该 flow 传给工具的是 command（不是 name）。
            # 平台已开始按 param_meta 做必填兜底校验，fixture 自身不能自相矛盾。
            "param_meta": [{"name": "command", "type": "string", "required": True}],
        }], ensure_ascii=False),
        encoding="utf-8",
    )

    from core.resource import paths as _paths

    monkeypatch.setattr(_paths, "resource_roots", lambda: [root])
    return root


def test_tool_display_name_resolves_to_id(tmp_path, monkeypatch):
    """ToolRegistry 是 id↔name 的权威：解析统一走它（不另建翻译模块）。"""
    _make_tool_space(tmp_path, monkeypatch)
    from core.resource.registry.tool_registry import resolve_tool_id

    assert resolve_tool_id("【【问候程序】】") == "greet-tool"
    assert resolve_tool_id("问候程序") == "greet-tool"
    assert resolve_tool_id(" greet-tool ") == "greet-tool"
    # 未注册且无实现的引用原样返回（由执行层报错，不在这里吞掉）
    assert resolve_tool_id("不存在的工具") == "不存在的工具"

    # 代码生成阶段的工具查询也必须走 registry（含私有根）
    b = AgentCodeBuilder(llm_client=None)
    found = b._lookup_tool("问候程序")
    assert found["id"] == "greet-tool"
    assert found["params"][0]["name"] == "command"


def test_md_tool_refs_normalized_on_write(tmp_path, monkeypatch):
    """MD 写入边界：flow/apis 里的显示名应被改写成真实 id。"""
    _make_tool_space(tmp_path, monkeypatch)
    b = AgentCodeBuilder(llm_client=None)

    fixed = b.normalize_tool_refs(WS_MD_ZH)
    assert '"tool": "greet-tool"' in fixed
    assert '"tool": "问候程序"' not in fixed
    # 正文里的【【显示名】】保持不动（那是给人看的）
    assert "【【问候程序】】" in fixed
    # 非 tool 字段不受影响
    assert '"name": "greet"' in fixed


def test_md_flow_tool_name_normalized(tmp_path, monkeypatch):
    """parse_structured_fields 应把 flow / apis 里的显示名换成 id（兼容存量 MD）。"""
    _make_tool_space(tmp_path, monkeypatch)

    spec = AgentSpec(id="greeting-web-service-agent", name="x", role=AgentRole.WEB_SERVICE)
    spec.raw_md = WS_MD_ZH
    spec.parse_structured_fields()
    assert spec.apis[0]["flow"][0]["tool"] == "greet-tool"

    task_spec = AgentSpec(id="greet-task-agent", name="x", role=AgentRole.TASK)
    task_spec.raw_md = TASK_MD.replace('"tool": "test-greeting"', '"tool": "问候程序"')
    task_spec.parse_structured_fields()
    assert task_spec.flow[0]["tool"] == "greet-tool"


async def test_web_service_md_with_display_name_end_to_end(tmp_path, monkeypatch):
    """端到端：MD 与 flow 都写显示名，生成代码执行时仍调用真实工具 id。"""
    _make_tool_space(tmp_path, monkeypatch)

    from core.executor.tool_executor import ToolExecutor

    captured: list[str] = []

    async def _fake_execute(tool_id, params, timeout=None, code=None):
        captured.append(tool_id)
        # MD 的 response 声明取 {{step1.data.text}}，这里按该字段返回
        who = params.get("name") or params.get("command")
        return {"status": "success", "message": "ok",
                "data": {"text": f"hello, {who}"}}

    monkeypatch.setattr(ToolExecutor, "execute", staticmethod(_fake_execute))

    b = AgentCodeBuilder(llm_client=None)
    code = await b.build({"raw_md": WS_MD_ZH, "id": "greeting-web-service-agent",
                          "name": "问候 Web 服务 Agent"})
    # 生成的辅助方法必须落到真实 id（docstring 里保留显示名便于阅读，不算问题）
    assert "_call_tool_greet_tool" in code
    assert '"问候程序"' not in code      # 任何以显示名当 id/目录名的地方都不该出现

    agent = _make_agent(WS_MD_ZH, "greeting-web-service-agent",
                        AgentRole.WEB_SERVICE, code)
    agent.call_tool = BaseAgent.call_tool.__get__(agent)   # 走真实解析逻辑
    events = await _run(agent, api_name="greet", params={"name": "张三"})

    assert captured == ["greet-tool"]
    results = [e for e in events if e["event"] == "result"]
    assert results and results[0]["data"]["response"] == {"greeting": "hello, 张三"}

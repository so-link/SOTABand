"""工具管理路由 v2 — 规格生成、代码生成、沙箱执行、自动调试、注册"""

import json as _json
import os as _os
import shutil
from datetime import datetime
from pathlib import Path as _Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from sse_starlette.sse import EventSourceResponse

from app.api.schemas.tool_schemas import (
    GenerateToolSpecRequest, GenerateToolCodeRequest, RegisterToolRequest,
    ExecuteToolRequest, ModifyCodeRequest,
)
from core.llm.client import create_llm_client
from core.resource.builder.tool_builder import ToolCodeBuilder, TOOL_TEMPLATE, stop_debug
from core.resource.registry.tool_registry import ToolRegistry
from core.resource.discoverer.tool_discoverer import ToolDiscoverer

router = APIRouter()
builder = ToolCodeBuilder()
registry = ToolRegistry()
discoverer = ToolDiscoverer()
llm = create_llm_client()

SPEC_PROMPT = """你是一个工具规格文档生成器。根据用户的自然语言描述，生成标准化的 Tool MD 规范文档。

严格按照以下 Markdown 模板输出：

---
id: {tool-id}
name: {工具名称}
version: 0.1.0
type: {function|script|api-wrapper}
language: python
status: active
created: {日期}
---

# {工具名称}

## 1. 功能概述

{描述}

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | text / image / table / file |
| data | dict/list | 输出数据 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `text` | `{"text":"..."}` | 纯文本 |
| `image` | `{"image_path":"/path/to/file.png"}` | 直接绘制图片 |
| `table` | `{"columns":[...], "rows":[[...]]}` | 渲染表格 |
| `file` | `{"file_path":"/path/to/result.csv"}` | 下载链接 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|

## 5. 运行机制

### 5.1 执行流程
1. 读取输入数据
2. 校验参数
3. 执行核心逻辑
4. 返回结果

### 5.2 错误处理
- 文件不存在 → 返回错误信息
- 参数无效 → 返回验证错误
- 处理异常 → 捕获并返回详细错误

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | {日期} | 初始版本 |

规则：
1. tool-id 使用小写字母+连字符
2. type 根据描述推断：function / script / api-wrapper
3. 合理推断输入参数和输出格式
4. 建议合适的依赖库和版本
5. 用户描述中的【xxx】表示系统API，【【xxx】】表示工具调用，原样保留
6. 除非用户明确写了标记，否则不添加系统API或工具引用"""

UPLOAD_DIR = _Path("/tmp/sotaband-uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── 规格生成 & 代码生成 ──

@router.post("/generate-spec")
async def generate_spec(req: GenerateToolSpecRequest):
    """NL → MD 规范文档。如有 reference_code，完整复制到执行流程部分。"""
    if not req.description.strip():
        raise HTTPException(400, "描述不能为空")

    user_content = req.description
    if req.reference_code.strip():
        user_content += f"""

---
以下为用户提供的参考代码，请完整复制到生成的 MD 文档中「5. 运行机制」部分（用 ```python 代码块包裹）：

```python
{req.reference_code.strip()}
```
"""

    today = datetime.now().strftime("%Y-%m-%d")
    # 动态注入当前日期，替换模板中的 {日期} 占位符
    system_prompt = SPEC_PROMPT.replace("{日期}", today)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    spec_md = await llm.chat(messages=messages, temperature=0.3, max_tokens=4000)
    return {"spec_md": spec_md}


@router.post("/generate-code")
async def generate_code(req: GenerateToolCodeRequest):
    """MD → 完整 Python 代码（LLM 生成，无后处理）"""
    spec = {"raw_md": req.spec_md, "id": req.tool_id, "name": req.tool_name}
    if not req.spec_md.strip():
        raise HTTPException(400, "MD 规范文档不能为空")
    if not await builder.validate_spec(spec):
        raise HTTPException(400, "MD 规范文档不完整，缺少必需段落")
    code = await builder.build(spec)
    # 提取参数 schema 供前端生成输入表单
    params = builder._parse_spec_inputs(req.spec_md)
    return {"code": code, "params": params}


# ── 沙箱测试 v2 — 用户输入测试数据 ──

@router.post("/test")
async def test_tool(
    spec_md: str = Form(""),
    tool_id: str = Form(""),
    tool_name: str = Form(""),
    code: str = Form(""),
    test_input_json: str = Form("{}"),
    files: list[UploadFile] = File([]),
):
    """沙箱测试：SSE 流式返回执行过程，支持客户端断开时强行终止子进程"""
    import asyncio as _asyncio

    # 处理上传文件
    test_input = _json.loads(test_input_json)
    for f in files:
        if f.filename:
            save_path = UPLOAD_DIR / f.filename
            content = await f.read()
            save_path.write_bytes(content)
            for key in test_input:
                if key == f.filename or test_input[key] == f.filename or not test_input.get(key):
                    test_input[key] = str(save_path)

    # 如果没有代码，先生成
    if not code.strip():
        spec = {"raw_md": spec_md, "id": tool_id, "name": tool_name}
        code = await builder.build(spec)

    from core.executor.tool_executor import ToolExecutor

    queue: _asyncio.Queue = _asyncio.Queue()

    async def _run():
        """在后台执行工具，结果放入队列"""
        try:
            result = await ToolExecutor.execute(
                tool_id=tool_id or "test",
                params=test_input,
                code=code,
                timeout=None,
            )
            await queue.put({
                "exit_code": 0 if result.get("status") == "success" else 1,
                "stdout": _json.dumps(result, ensure_ascii=False),
                "stderr": result.get("stderr", ""),
                "success": result.get("status") == "success",
            })
        except _asyncio.CancelledError:
            pass  # 被取消，不推送结果
        finally:
            await queue.put(None)  # 哨兵

    runner_task = _asyncio.create_task(_run())

    async def event_stream():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {"event": "result", "data": _json.dumps(event, ensure_ascii=False)}
        except _asyncio.CancelledError:
            # 客户端断开 → 取消执行任务
            runner_task.cancel()
            try:
                await _asyncio.wait_for(runner_task, timeout=3.0)
            except (_asyncio.TimeoutError, _asyncio.CancelledError):
                pass

    return EventSourceResponse(event_stream())


# ── 文件上传 ──

@router.post("/upload-test-file")
async def upload_test_file(file: UploadFile = File(...)):
    """上传测试文件，返回临时路径"""
    save_path = UPLOAD_DIR / file.filename
    content = await file.read()
    save_path.write_bytes(content)
    return {"file_path": str(save_path), "file_name": file.filename}


# ── 依赖安装 ──

@router.post("/{tool_id}/install-deps")
async def install_deps(tool_id: str, req: dict):
    """安装依赖到工具本地 .venv"""
    deps = req.get("dependencies", [])
    if not deps:
        raise HTTPException(400, "dependencies 不能为空")
    result = await builder.install_deps(tool_id, deps)
    return result


# ── 自动调试 v2 ──

@router.post("/auto-debug")
async def auto_debug(
    spec_md: str = Form(""),
    tool_id: str = Form(""),
    tool_name: str = Form(""),
    code: str = Form(""),
    test_input_json: str = Form("{}"),
    files: list[UploadFile] = File([]),
):
    """v2 自动调试：SSE 流式返回每轮执行结果和 LLM 分析。
    用户关闭 SSE 连接（前端 AbortController.abort()）时自动停止。
    """
    import asyncio

    test_input = _json.loads(test_input_json)
    for f in files:
        if f.filename:
            save_path = UPLOAD_DIR / f.filename
            content = await f.read()
            save_path.write_bytes(content)
            for key in list(test_input.keys()):
                if not test_input.get(key) or test_input[key] == f.filename:
                    test_input[key] = str(save_path)

    if not code.strip():
        spec = {"raw_md": spec_md, "id": tool_id, "name": tool_name}
        code = await builder.build(spec)

    import asyncio as _asyncio

    stop_event = _asyncio.Event()
    queue: _asyncio.Queue = _asyncio.Queue()

    async def _producer():
        try:
            async for event in builder.auto_debug_stream(spec_md, code, test_input, tool_id, stop_event=stop_event):
                await queue.put(event)
        except _asyncio.CancelledError:
            stop_event.set()
        finally:
            await queue.put(None)  # 哨兵

    producer_task = _asyncio.create_task(_producer())

    async def event_stream():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {"event": event["event"], "data": _json.dumps(event, ensure_ascii=False)}
        except _asyncio.CancelledError:
            # 客户端断开 → 设置停止信号 + 取消 producer
            stop_event.set()
            producer_task.cancel()
            # 等待 producer 结束
            try:
                await _asyncio.wait_for(producer_task, timeout=3.0)
            except (_asyncio.TimeoutError, _asyncio.CancelledError):
                pass

    return EventSourceResponse(event_stream())


# ── 停止自动调试 ──

@router.post("/auto-debug/stop")
async def stop_auto_debug(req: dict):
    """停止指定工具的自动调试。
    前端点击停止按钮、页面关闭、网络断开时都应调用此端点。
    即使 SSE 连接已断开，此端点也能通过全局标志可靠地停止后台调试。
    """
    tool_id = req.get("tool_id", "")
    if not tool_id:
        raise HTTPException(status_code=400, detail="缺少 tool_id")
    stop_debug(tool_id)
    return {"status": "ok", "message": f"已请求停止工具 {tool_id} 的调试"}


# ── 注册 v2 — 不再强制沙箱测试 ──

@router.post("/register")
async def register_tool(req: RegisterToolRequest):
    """注册工具"""
    spec = {"raw_md": req.spec_md, "id": req.tool_id, "name": req.tool_name}
    code = req.code or (await builder.build(spec))

    param_meta = []
    try:
        param_meta = await builder.extract_param_metadata(req.spec_md)
    except Exception:
        pass
    if not param_meta:
        param_meta = builder._parse_spec_inputs(req.spec_md)

    resource = {
        "id": req.tool_id, "name": req.tool_name, "version": req.version,
        "raw_md": req.spec_md, "code": code, "tags": req.tags,
        "param_meta": param_meta,
    }
    tool_id = await registry.register(resource)

    if hasattr(req, 'demand_desc') and req.demand_desc:
        (registry._get_def_dir() / f"{tool_id}-demand.md").write_text(req.demand_desc)

    # 保存参考代码为独立文件
    if hasattr(req, 'reference_code') and req.reference_code:
        (registry._get_def_dir() / f"{tool_id}-reference.md").write_text(req.reference_code)

    entry = await registry.get(tool_id)
    return {"tool_id": tool_id, "entry": entry}


# ── 列表 / 详情 / 调用 / 搜索 / 删除 ──

@router.get("/list")
async def list_tools():
    tools = await registry.list_all()
    return {"tools": tools}


@router.get("/repository")
async def repository():
    """工具仓库 — 返回所有工具及其标签统计"""
    tools = await registry.list_all()
    # 聚合标签统计
    tag_stats: dict[str, int] = {}
    for t in tools:
        for tag in (t.get("tags") or []):
            tag_stats[tag] = tag_stats.get(tag, 0) + 1
    # 按数量降序排列
    sorted_tags = dict(sorted(tag_stats.items(), key=lambda x: -x[1]))
    return {"tools": tools, "tag_stats": sorted_tags, "total": len(tools)}


@router.get("/{tool_id}")
async def get_tool(tool_id: str):
    entry = await registry.get(tool_id)
    if not entry: raise HTTPException(404, f"Tool '{tool_id}' not found")
    spec_path = registry._get_def_dir() / f"{tool_id}.md"
    spec_md = spec_path.read_text() if spec_path.exists() else ""
    code_path = registry._get_impl_dir() / tool_id / "tool.py"
    code = code_path.read_text() if code_path.exists() else ""
    demand_path = registry._get_def_dir() / f"{tool_id}-demand.md"
    has_demand = demand_path.exists()
    demand_md = demand_path.read_text() if has_demand else ""
    reference_path = registry._get_def_dir() / f"{tool_id}-reference.md"
    has_reference = reference_path.exists()
    reference_code = reference_path.read_text() if has_reference else ""
    return {**entry, "spec_md": spec_md, "code": code, "has_demand": has_demand, "demand_md": demand_md, "has_reference": has_reference, "reference_code": reference_code}


@router.post("/{tool_id}/modify-code")
async def modify_code(tool_id: str, req: "ModifyCodeRequest"):
    entry = await registry.get(tool_id)
    if not entry: raise HTTPException(404, f"Tool '{tool_id}' not found")
    current_code = req.current_code or ""
    if not current_code.strip():
        code_path = registry._get_impl_dir() / tool_id / "tool.py"
        current_code = code_path.read_text() if code_path.exists() else ""
    if not req.request.strip():
        raise HTTPException(400, "修改描述不能为空")

    prompt = f"""Modify the following Python tool code according to the user's request.
IMPORTANT: Keep execute(**kwargs)->dict signature and return format. Return ONLY the complete modified code.

Current code:
```python
{current_code}
```

User's request:
{req.request}

Modified code:"""

    response = await llm.chat(messages=[{"role":"user","content":prompt}], temperature=0.3, max_tokens=100000)
    modified_code = response
    for marker in ("```python", "```"):
        if marker in modified_code:
            parts = modified_code.split(marker)
            if len(parts) > 1: modified_code = parts[1]
            break
    return {"modified_code": modified_code.strip(), "original_code": current_code}


@router.post("/{tool_id}/save-code")
async def save_code(tool_id: str, req: dict):
    """保存工具代码到文件系统"""
    entry = await registry.get(tool_id)
    if not entry: raise HTTPException(404, f"Tool '{tool_id}' not found")
    code = req.get("code", "")
    if not code.strip():
        raise HTTPException(400, "代码不能为空")
    impl_dir = registry._get_impl_dir() / tool_id
    impl_dir.mkdir(parents=True, exist_ok=True)
    (impl_dir / "tool.py").write_text(code)
    return {"saved": tool_id}


@router.post("/{tool_id}/execute")
async def execute_tool(tool_id: str, req: ExecuteToolRequest):
    entry = await registry.get(tool_id)
    if not entry: raise HTTPException(404, f"Tool '{tool_id}' not found")

    from core.executor.tool_executor import ToolExecutor
    result = await ToolExecutor.execute(
        tool_id=tool_id,
        params=req.params,
        timeout=None,
    )
    return {"status": "success", "result": result}


@router.get("/search/find")
async def search_tools(q: str = "", tags: str = ""):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    results = await discoverer.search(query=q, tags=tag_list)
    return {"tools": results}


@router.delete("/{tool_id}")
async def delete_tool(tool_id: str):
    entry = await registry.get(tool_id)
    if not entry: raise HTTPException(404, f"Tool '{tool_id}' not found")
    impl_dir = registry._get_impl_dir() / tool_id
    if impl_dir.exists(): shutil.rmtree(impl_dir, ignore_errors=True)
    spec_path = registry._get_def_dir() / f"{tool_id}.md"
    if spec_path.exists(): spec_path.unlink()
    await registry.unregister(tool_id)
    return {"deleted": tool_id}

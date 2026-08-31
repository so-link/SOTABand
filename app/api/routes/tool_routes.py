"""工具管理路由 v2 — 规格生成、代码生成、沙箱执行、自动调试、注册"""

import hashlib
import json as _json
import os as _os
import re
import shutil
import time as _time
from datetime import datetime
from pathlib import Path as _Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.api.schemas.tool_schemas import (
    GenerateToolSpecRequest, GenerateToolCodeRequest, RegisterToolRequest,
    ExecuteToolRequest, ModifyCodeRequest, ModifyAndDebugRequest,
)
from core.llm.client import create_llm_client
from core.resource.builder.tool_builder import ToolCodeBuilder, TOOL_TEMPLATE, stop_debug
from core.resource.spec_outline import (
    parse_markdown_outline, replace_node_content, outline_context_for_prompt,
    parse_table, update_table_cell, replace_table_in_node,
)
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
    spec_md = await llm.chat(messages=messages, temperature=0.3, max_tokens=100000)

    # 同时生成标签
    tags: list[str] = []
    try:
        tag_prompt = f"""根据以下工具信息，生成3-5个简短的中文标签（每个2-4字），用于工具分类和检索。直接返回 JSON 数组。

工具描述: {req.description[:500]}
生成的 MD 规范: {spec_md[:300]}

返回格式: ["标签1", "标签2", "标签3"]"""
        tag_response = await llm.chat(
            messages=[{"role": "user", "content": tag_prompt}],
            temperature=0.3, max_tokens=200,
        )
        tags = _extract_tags_json(tag_response) or []
        if not tags:
            import re
            matches = re.findall(r'[\u4e00-\u9fff]{2,4}', tag_response)
            tags = list(dict.fromkeys(matches))[:5]
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[generate-spec] 标签生成失败: {e}")

    return {"spec_md": spec_md, "tags": tags}


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

    # 异步 LLM 自动生成标签（不阻塞注册响应）
    if not req.tags:
        import asyncio
        asyncio.create_task(_auto_generate_tags(tool_id, req.tool_name, req.spec_md))

    return {"tool_id": tool_id, "entry": entry}


async def _auto_generate_tags(tool_id: str, name: str, spec_md: str):
    """LLM 自动生成工具标签，更新到 registry"""
    try:
        from core.llm.client import get_llm_client
        llm = get_llm_client()
        prompt = f"""根据以下工具信息，生成3-5个简短的中文标签（每个2-4字），用于工具分类和检索。直接返回 JSON 数组。

工具名称: {name}
工具描述: {spec_md[:500]}

返回格式: ["标签1", "标签2", "标签3"]"""
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200, timeout=30,
        )
        print(f"[_auto_generate_tags] LLM返回 tool_id={tool_id}: {repr(response[:200])}")

        # 多策略 JSON 解析
        tags = _extract_tags_json(response)
        if not tags:
            # 回退：用简单的正则提取所有引号内中文
            import re
            matches = re.findall(r'[\u4e00-\u9fff]{2,4}', response)
            tags = list(dict.fromkeys(matches))[:5]  # 去重，最多5个
            if tags:
                print(f"[_auto_generate_tags] 回退正则提取 tool_id={tool_id}: {tags}")

        if tags:
            entry = await registry.get(tool_id)
            if entry:
                entry["tags"] = tags
                await registry._save()
                print(f"[_auto_generate_tags] 标签已更新 tool_id={tool_id}: {tags}")
        else:
            print(f"[_auto_generate_tags] 无法提取标签 tool_id={tool_id}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[_auto_generate_tags] 异常 tool_id={tool_id}: {e}")


def _extract_tags_json(text: str) -> list[str] | None:
    """多策略从 LLM 返回中提取 JSON 标签数组"""
    import re
    # 策略1：直接解析整个响应（去掉可能的 markdown 标记）
    clean = text.strip()
    for prefix in ['```json', '```', '`']:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
    for suffix in ['```', '`']:
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)].strip()
    if clean.startswith('[') and ']' in clean:
        try:
            end = clean.rindex(']') + 1
            result = _json.loads(clean[:end])
            if isinstance(result, list) and all(isinstance(t, str) for t in result):
                return result
        except Exception:
            pass
    # 策略2：正则找 [...]
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            result = _json.loads(match.group())
            if isinstance(result, list) and all(isinstance(t, str) for t in result):
                return result
        except Exception:
            pass
    # 策略3：找 [[...]] 嵌套格式
    match = re.search(r'\[\[.*?\]\]', text, re.DOTALL)
    if match:
        try:
            result = _json.loads(match.group())
            if isinstance(result, list):
                return result
        except Exception:
            pass
    return None


# ── 工具空间（已加载清单）—— 后端持久化 ──
# "工具空间"显示哪些工具属于引擎状态而非浏览器状态：以前清单只存前端
# localStorage，换浏览器 / 无痕窗口 / 清缓存后工作空间就全空了。
# 现在状态源是 storage/workspace_tools.json，localStorage 仅作降级缓存。
_PROJECT_ROOT = _Path(__file__).resolve().parent.parent.parent.parent
_WORKSPACE_FILE = _PROJECT_ROOT / "storage" / "workspace_tools.json"


class WorkspaceToolItem(BaseModel):
    id: str
    name: str
    tags: list[str] = []


def _read_workspace() -> list[dict]:
    try:
        if _WORKSPACE_FILE.exists():
            return _json.loads(_WORKSPACE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _write_workspace(items: list[dict]) -> None:
    _WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WORKSPACE_FILE.write_text(
        _json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/workspace")
async def get_workspace():
    return {"tools": _read_workspace()}


@router.post("/workspace")
async def add_workspace_tool(item: WorkspaceToolItem):
    items = _read_workspace()
    if not any(t.get("id") == item.id for t in items):
        items.append({
            "id": item.id,
            "name": item.name,
            "tags": item.tags,
            "loadedAt": datetime.now().isoformat(),
        })
        _write_workspace(items)
    return {"ok": True, "tools": items}


@router.delete("/workspace/{tool_id}")
async def remove_workspace_tool(tool_id: str):
    items = [t for t in _read_workspace() if t.get("id") != tool_id]
    _write_workspace(items)
    return {"ok": True, "tools": items}


# ── 列表 / 详情 / 调用 / 搜索 / 删除 ──

@router.get("/list")
async def list_tools():
    tools = await registry.list_all()
    # is_user_generated：是否为使用者在本地生成的工具。
    # registry 中的内置示例工具没有 owner 字段；用户通过编辑器生成/注册的
    # 工具会带 owner（注册时由 user_context 注入）。前端据此显示
    # 「⭐ 用户本地工具」标记，而不是把所有工具都当作本地工具。
    from core.user_context import get_current_user_id
    me = get_current_user_id()
    for t in tools:
        t["is_user_generated"] = bool(t.get("owner"))
    return {"tools": tools, "current_user": me}


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


@router.post("/{tool_id}/tags")
async def update_tool_tags(tool_id: str, req: dict):
    """更新工具标签"""
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")
    tags = req.get("tags", [])
    if not isinstance(tags, list):
        raise HTTPException(400, "tags 必须是数组")
    entry["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    await registry._save()
    return {"status": "ok", "tags": entry["tags"]}


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


class SpecOutlineRequest(BaseModel):
    """解析文档结构请求"""
    spec_md: str = ""          # 留空则用该工具已保存的文档
    with_summary: bool = False  # 是否同时生成人话摘要（需调用 LLM）


@router.post("/{tool_id}/spec-outline")
async def get_spec_outline(tool_id: str, req: SpecOutlineRequest):
    """解析工具规范文档，返回节点树 + 可选的人话摘要。

    支撑「分层摘要呈现」与「节点级精化」两个低门槛能力：
    - 节点树让使用者能看到文档结构，而非通读 87 行表格
    - 摘要用非技术语言描述每个节点，便于领域专家判断对错

    with_summary=true 时会调用 LLM 生成各节点摘要（有 token 开销）；
    仅需要结构时传 false。

    tool_id 可以是占位值（如 `_draft_`）表示"尚未注册的新建文档"。
    概览与摘要本身不依赖注册信息——给了 spec_md 就能解析，
    因此新建工具流程同样可用，不必先注册。
    """
    spec_md = req.spec_md
    is_draft = tool_id in ("", "_draft_", "draft", "@new")
    if not spec_md.strip() and not is_draft:
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        if not spec_path.exists():
            raise HTTPException(404, "该工具尚无规范文档")
        spec_md = spec_path.read_text(encoding="utf-8")

    if not spec_md.strip():
        raise HTTPException(400, "缺少 spec_md：新建文档请直接传入文档内容")

    outline = parse_markdown_outline(spec_md, tool_id=tool_id)

    # 文档无标题时（罕见），给出明确提示而非返回空树
    if not outline.nodes:
        return {
            **outline.to_dict(),
            "warning": "文档未解析出任何标题，请确认文档使用了 Markdown 标题（## / ###）",
        }

    if req.with_summary:
        key = _summary_key(spec_md)

        # 1) 命中缓存 → 直接复用，毫秒级返回
        cached = _summary_cache_get(key)
        if cached is not None:
            _apply_summaries(outline, cached)
            outline.cached = True
            return outline.to_dict()

        # 2) 正在生成中 → 告知前端轮询，避免并发重复调用模型
        if key in _SUMMARY_PENDING:
            return {**outline.to_dict(), "summarizing": True}

        # 3) 未缓存 → 生成并写入缓存
        _SUMMARY_PENDING.add(key)
        try:
            await _fill_node_summaries(outline)

            # 模型偶尔返回的数组项数不足（尤其长文档），导致部分节点缺失摘要。
            # 对缺失项做一次定向补全 —— 只补缺失的，避免重跑全部（每次约 30 秒）。
            missing = [n for n in outline.nodes if not n.summary]
            if missing and len(missing) < len(outline.nodes):
                await _fill_missing_summaries(outline, missing)

            # 全部为空说明失败，不缓存以免把失败结果固化
            if any(n.summary for n in outline.nodes):
                _summary_cache_put(key, _collect_summaries(outline))
        finally:
            _SUMMARY_PENDING.discard(key)

    return outline.to_dict()


# 正在生成中的文档，避免并发重复调用模型（30 秒/次，重复很浪费）
_SUMMARY_PENDING: set[str] = set()

# ── 摘要缓存 ──────────────────────────────────────────────
# 生成一次摘要需调用推理模型，实测约 30 秒；而同一份文档的摘要是确定的。
# 若不缓存，使用者每次点开概览都要等 30 秒，体验极差。
# 因此按「文档内容哈希」缓存，文档没变就直接复用。
_SUMMARY_CACHE: dict[str, tuple[float, list[str]]] = {}
_SUMMARY_CACHE_TTL = 3600        # 1 小时
_SUMMARY_CACHE_MAX = 64          # 上限，防止无限增长


def _summary_key(md: str) -> str:
    """缓存 key：文档内容的哈希。

    用内容而非 tool_id —— 新建阶段文档尚未落盘、没有稳定 id，
    且同一文档在编辑过程中内容会变，内容哈希天然反映"摘要是否还适用"。
    """
    return hashlib.md5((md or "").encode("utf-8")).hexdigest()


def _summary_cache_get(key: str) -> list[str] | None:
    hit = _SUMMARY_CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if _time.time() - ts > _SUMMARY_CACHE_TTL:
        _SUMMARY_CACHE.pop(key, None)
        return None
    return val


def _summary_cache_put(key: str, val: list[str]) -> None:
    # 超限时清掉最旧的一半
    if len(_SUMMARY_CACHE) >= _SUMMARY_CACHE_MAX:
        for k in sorted(_SUMMARY_CACHE, key=lambda k: _SUMMARY_CACHE[k][0])[:_SUMMARY_CACHE_MAX // 2]:
            _SUMMARY_CACHE.pop(k, None)
    _SUMMARY_CACHE[key] = (_time.time(), val)


async def _fill_missing_summaries(outline, missing: list) -> None:
    """为缺失摘要的节点做一次定向补全（原地修改）。

    批量摘要时模型可能返回的数组短于节点数（长文档尤其常见），
    导致部分节点摘要为空。这里只对缺失项重新请求，
    而不是重跑全部——每次调用约 30 秒，重跑代价太高。
    """
    if not missing:
        return

    bullets = "\n".join(
        f"[{i+1}] {n.title}\n{n.content_md[:400]}"
        for i, n in enumerate(missing)
    )
    system_prompt = (
        "你是工具规范文档的解读助手。请用非技术人员能理解的话，"
        "把每个段落概括成一句话（15~40字），避免参数类型、字段名、库名等术语。\n"
        "直接给出结果，不要长篇分析思考。\n"
        "按编号顺序输出JSON字符串数组，只输出JSON。"
    )
    user_prompt = f"""段落：

{bullets}

请按编号顺序输出JSON数组（共 {len(missing)} 项）："""

    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=8000,
        )
        cleaned = (raw or "").strip()
        for marker in ("```json", "```"):
            if cleaned.startswith(marker):
                cleaned = cleaned[len(marker):].strip()
                if cleaned.rstrip().endswith("```"):
                    cleaned = cleaned.rstrip()[:-3].strip()
                break
        if "[WARNING]" in cleaned:
            return
        data = _json.loads(cleaned)
        if isinstance(data, list):
            for i, n in enumerate(missing):
                if i < len(data) and isinstance(data[i], str) and data[i].strip():
                    n.summary = data[i].strip()
    except Exception:
        # 补全失败不影响已有摘要
        pass


def _collect_summaries(outline) -> list[str]:
    return [n.summary for n in outline.nodes]


def _apply_summaries(outline, summaries: list[str]) -> None:
    for i, n in enumerate(outline.nodes):
        if i < len(summaries) and isinstance(summaries[i], str):
            n.summary = summaries[i]


async def _fill_node_summaries(outline) -> None:
    """为各节点生成「人话摘要」（原地修改）。

    摘要面向非专业使用者：避免术语，用业务语言描述该段落在决定什么。

    两个关键设计：
    1. **让模型按节点顺序输出数组，而不是输出 id→摘要的对象**。
       节点 id 是系统生成的（如 `h2-gong-neng-gai-shu`），要求模型准确
       复现容易写错，导致所有摘要匹配不上而全部为空。用数组按序对应
       更稳，且输出更短。
    2. **max_tokens 必须给足**。该模型是推理模型，会先输出大量思考 token；
       额度不足时会在思考阶段就截断，返回空且只带一句 WARNING，
       ——这不是异常，静默失败极难排查。

    失败时把原因记到 outline.warning，而非静默吞掉。
    """
    if not outline.nodes:
        return

    # 每个节点只取前 400 字符：上下文越长，推理模型思考越久，越容易截断
    bullets = "\n".join(
        f"[{i+1}] {n.title}\n{n.content_md[:400]}"
        for i, n in enumerate(outline.nodes)
    )

    system_prompt = (
        "你是工具规范文档的解读助手。使用者是各领域的专业人员，不是程序员。\n"
        "请把工具规范文档的每个段落，用**非技术人员能理解的话**概括成一句话。\n\n"
        "要求：\n"
        "1. 避免出现参数类型、字段名、库名等技术术语；说清楚「这段在决定什么」\n"
        "2. 每条约 15~40 字\n"
        "3. 按下面的段落编号顺序，输出一个 JSON 字符串数组，不要输出编号\n"
        "4. 直接给出结果，不要长篇分析思考（实测可显著降低推理耗时）\n"
        "5. 只输出 JSON，不要解释，不要 markdown 代码块"
    )
    user_prompt = f"""文档各段落：

{bullets}

请按编号顺序输出 JSON 数组，例如：["说明这个工具是干什么的", "说明需要你提供哪些信息"]"""

    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            # 推理模型会先输出大量思考 token，额度必须给足，否则静默返回空
            max_tokens=8000,
        )
        cleaned = (raw or "").strip()

        # 推理模型可能在正文前后留下思考残留或 markdown 围栏
        for marker in ("```json", "```"):
            if cleaned.startswith(marker):
                cleaned = cleaned[len(marker):].strip()
                if cleaned.rstrip().endswith("```"):
                    cleaned = cleaned.rstrip()[:-3].strip()
                break

        # 截断保护：模型若被截断，输出可能不是合法 JSON
        if "[WARNING]" in cleaned:
            outline.warning = "摘要生成被截断（模型思考超长），可重试或关闭摘要"
            return

        data = _json.loads(cleaned)
        if isinstance(data, list):
            for i, n in enumerate(outline.nodes):
                if i < len(data):
                    v = data[i]
                    if isinstance(v, str) and v.strip():
                        n.summary = v.strip()
        elif isinstance(data, dict):
            # 兼容模型仍返回对象的情况：按 id 或标题匹配
            for n in outline.nodes:
                v = data.get(n.id) or data.get(n.title)
                if isinstance(v, str) and v.strip():
                    n.summary = v.strip()
    except Exception as e:
        # 不静默：把原因暴露出来，否则前端只会看到一片空白
        outline.warning = f"摘要生成失败：{type(e).__name__}: {str(e)[:120]}"


class UpdateTableCellRequest(BaseModel):
    """修改文档表格中的某个单元格（零延迟，不调用 LLM）"""
    node_id: str
    row_index: int                 # 数据行下标（0-based，不含表头）
    column: str                    # 列名（须在表头中存在）
    value: str                     # 新值
    spec_md: str = ""              # 留空则用已保存的文档
    save: bool = False


@router.post("/{tool_id}/update-table-cell")
async def update_table_cell_api(tool_id: str, req: UpdateTableCellRequest):
    """修改表格中的单个单元格，由系统确定性应用，**不调用 LLM**。

    解决的问题：使用者最常见的修改是"把并发数默认值从 8 改成 4"这类
    **确定性改动**。这类改动走 LLM 有两个问题：
    1. 慢 —— 推理模型思考开销导致每次 ~11 秒
    2. 不精确 —— 模型会顺带改同义词、调语序（业界共识：LLM 重写整段
       是危险的，即使只让它改一个值）

    表格本身是结构化的，可程序化解析 → 改一个单元格 → 重渲染，
    全程毫秒级、100% 精确，只改目标单元格。

    典型场景：
    - 改参数默认值、类型、必填
    - 改输出字段名、说明
    - 改依赖版本

    若需**语义层面**的修改（如"补充说明小于 1 时怎么处理"），
    应改用 refine-spec-node。
    """
    spec_md = req.spec_md
    is_draft = tool_id in ("", "_draft_", "draft", "@new")
    if not spec_md.strip() and not is_draft:
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        if not spec_path.exists():
            raise HTTPException(404, "该工具尚无规范文档")
        spec_md = spec_path.read_text(encoding="utf-8")

    if not spec_md.strip():
        raise HTTPException(400, "缺少 spec_md")

    outline = parse_markdown_outline(spec_md, tool_id=tool_id)
    node = outline.get(req.node_id)
    if node is None:
        available = ", ".join(n.id for n in outline.nodes)
        raise HTTPException(404, f"未找到节点 '{req.node_id}'。可用节点：{available}")

    try:
        new_table = update_table_cell(node, req.row_index, req.column, req.value)
    except ValueError as e:
        # 列名不存在、行号越界、节点无表格 —— 都是可预期的用户输入错误
        raise HTTPException(400, str(e))

    new_node_md = replace_table_in_node(node, new_table)
    updated_md = replace_node_content(spec_md, node, new_node_md)
    new_outline = parse_markdown_outline(updated_md, tool_id=tool_id)
    new_outline.version = outline.version + 1

    if req.save and not is_draft:
        await registry.update(tool_id, {"raw_md": updated_md})
        try:
            spec_path = registry._get_def_dir() / f"{tool_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(updated_md, encoding="utf-8")
        except Exception:
            pass

    return {
        "node_id": req.node_id,
        "node_md": new_node_md,
        "updated_md": updated_md,
        "outline": new_outline.to_dict(),
        "saved": bool(req.save and not is_draft),
    }


@router.post("/{tool_id}/spec-table")
async def get_spec_table(tool_id: str, req: SpecOutlineRequest):
    """读取某节点内的表格结构，供前端渲染可编辑表单。

    返回表头、各行数据、对齐方式；无表格时返回 has_table=False。
    """
    spec_md = req.spec_md
    is_draft = tool_id in ("", "_draft_", "draft", "@new")
    if not spec_md.strip() and not is_draft:
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        if not spec_path.exists():
            raise HTTPException(404, "该工具尚无规范文档")
        spec_md = spec_path.read_text(encoding="utf-8")

    if not spec_md.strip():
        raise HTTPException(400, "缺少 spec_md")

    node_id = (req.spec_md or "").strip() and ""  # 占位，保持结构清晰
    outline = parse_markdown_outline(spec_md, tool_id=tool_id)
    # 找第一个含表格的节点（通常是输入规范）
    for n in outline.nodes:
        block = parse_table(n)
        if block is not None:
            return {
                "has_table": True,
                "node_id": n.id,
                "node_title": n.title,
                "header": block.header,
                "rows": block.rows,
                "align": block.align,
            }
    return {"has_table": False}


class RefineSpecNodeRequest(BaseModel):
    """精化单个文档节点"""
    node_id: str
    feedback: str                 # 使用者描述的问题
    spec_md: str = ""             # 完整文档；留空则用已保存的文档
    save: bool = False            # 是否直接写回


@router.post("/{tool_id}/refine-spec-node")
async def refine_spec_node(tool_id: str, req: RefineSpecNodeRequest):
    """只精化文档中的**一个节点**，而非重新生成整篇。

    解决的问题：使用者只想改一处（如"并发数默认改成 4"），
    但当前只能手动编辑或整体重生成——后者Token 消耗高，且可能
    顺带改动使用者没提到的段落。

    Args:
        node_id:  要修改的节点 id（来自 spec-outline）
        feedback: 使用者描述的问题，自然语言
        spec_md:  当前完整文档
        save:     是否直接落盘

    Returns:
        node_md     修改后的该段落
        updated_md  替换后的完整文档
        outline     重建后的节点树
        diff        改前/改后对比
        impact_hint 若改动可能影响其他段落，LLM 会在此说明

    同样支持占位 tool_id（`_draft_`）：新建工具未注册时也能精化，
    只是 save 必须为 False（无注册项可写）。
    """
    spec_md = req.spec_md
    is_draft = tool_id in ("", "_draft_", "draft", "@new")
    if not spec_md.strip() and not is_draft:
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        if not spec_path.exists():
            raise HTTPException(404, "该工具尚无规范文档")
        spec_md = spec_path.read_text(encoding="utf-8")

    if not spec_md.strip():
        raise HTTPException(400, "缺少 spec_md：新建文档请直接传入文档内容")

    # 未注册文档不允许落盘
    if is_draft:
        req.save = False

    outline = parse_markdown_outline(spec_md, tool_id=tool_id)
    node = outline.get(req.node_id)
    if node is None:
        available = ", ".join(n.id for n in outline.nodes)
        raise HTTPException(
            404, f"未找到节点 '{req.node_id}'。可用节点：{available}"
        )

    feedback = (req.feedback or "").strip()
    if not feedback:
        raise HTTPException(400, "请说明这个节点有什么问题")

    # ── 分级策略（参照 Claude Code 的 effort 分级哲学）──
    # 实测：fast(精简prompt) 约 11 秒，full(含few-shot+上下文) 约 21 秒。
    # 默认走 fast 追求速度；仅当校验失败（越界/哈希不一致/空返回）
    # 才升级到 full 重试 —— 既快又不牺牲可靠性。
    used_mode = "fast"
    system_prompt, user_prompt, _ = _build_refine_prompt(outline, node, feedback, "fast")

    raw = await llm.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # 文档编辑要求确定性：业界实践明确要求 temperature=0
        temperature=0.0,
        max_tokens=4000,
    )

    new_node_md, updated_md, impact_hint, new_outline, err = _postprocess_refined(
        raw, outline, node, spec_md, ""
    )

    # fast 档失败 → 用 full 档（更强的保守性约束）重试一次
    if err:
        used_mode = "full"
        system_prompt, user_prompt, _ = _build_refine_prompt(outline, node, feedback, "full")
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=4000,
        )
        new_node_md, updated_md, impact_hint, new_outline, err = _postprocess_refined(
            raw, outline, node, spec_md, ""
        )

    if err:
        # 区分"模型没返回内容"（500）与"校验拦截"（422）
        code = 422 if ("阻止替换" in err or "检测到未请求" in err) else 500
        raise HTTPException(code, err)

    if req.save:
        await registry.update(tool_id, {"raw_md": updated_md})
        try:
            spec_path = registry._get_def_dir() / f"{tool_id}.md"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(updated_md, encoding="utf-8")
        except Exception:
            pass

    return {
        "node_id": req.node_id,
        "node_md": new_node_md,
        "updated_md": updated_md,
        "outline": new_outline.to_dict(),
        "diff": {"before": node.content_md, "after": new_node_md},
        "impact_hint": impact_hint,
        "saved": req.save,
        # fast=精简prompt(快) / full=含few-shot+上下文(慢但更保守)
        "mode": used_mode,
    }


def _check_value_consistency(old_md: str, new_md: str) -> str:
    """检测"改了值但说明文字仍引用旧值"的不一致。

    「最小必要改动」策略下，模型严格只改被要求之处，不会顺带同步
    段落内其他位置对同一值的引用。例如把默认值从 8 改成 4 后，
    说明文字里可能还写着"小于1时使用默认值8"。

    这不算错误（使用者可能本就只想改一处），但值得提醒，
    否则文档内部会自相矛盾。

    Returns:
        提醒文本；无不一致则返回空串。
    """
    if not old_md or not new_md or old_md == new_md:
        return ""

    old_lines = old_md.split("\n")
    new_lines = new_md.split("\n")

    hints: list[str] = []

    for i, ol in enumerate(old_lines):
        if i >= len(new_lines):
            break
        nl = new_lines[i]
        if ol == nl:
            continue

        # 只对表格行做检测（规范文档的值引用大多同行）
        if not (ol.strip().startswith("|") and nl.strip().startswith("|")):
            continue

        oc = [c.strip() for c in ol.strip().strip("|").split("|")]
        nc = [c.strip() for c in nl.strip().strip("|").split("|")]
        if len(oc) != len(nc):
            continue

        for j, (a, b) in enumerate(zip(oc, nc)):
            if a == b or not a or not b or len(a) > 16:
                continue
            # 检查同行其他单元格是否仍引用旧值。
            # 典型场景：默认值 8→4，但「说明」列仍写着"小于1时使用默认值8"。
            for k, cell in enumerate(nc):
                if k == j or not cell:
                    continue
                if a in cell:
                    col = oc[k]
                    snippet = col if len(col) <= 12 else col[:12] + "…"
                    hints.append(
                        f"「{a}」已改为「{b}」，但同行「{snippet}」中仍出现「{a}」"
                    )
                    break

    if not hints:
        return ""

    # 去重后最多提示两条，避免刷屏
    seen: set[str] = set()
    uniq = [h for h in hints if not (h in seen or seen.add(h))]
    return "注意：" + "；".join(uniq[:2]) + "，如需同步请再次说明"


def _check_untouched_intact(old_outline, new_outline, modified_id: str) -> list[str]:
    """校验：除被修改的节点外，其余节点内容必须逐字未变。

    业界（受监管行业的 surgical editing 实践）明确要求：
    未被请求修改的部分，改前改后哈希必须一致。

    LLM 重写段落时有"改写欲"，即使只让它改一个值，也可能顺带改同义词、
    调语序。行范围替换本身不会波及其他节点，本校验用于兜住解析/替换
    环节的意外，并让每次改动的范围可审计。

    Returns:
        被意外改动的节点标题列表；全部一致则返回空列表。
    """
    violations: list[str] = []

    old_map = {n.id: n.content_md for n in old_outline.nodes}
    new_map = {n.id: n.content_md for n in new_outline.nodes}

    for nid, old_content in old_map.items():
        if nid == modified_id:
            continue          # 目标节点本来就该变
        new_content = new_map.get(nid)
        # 节点消失（标题被改）也算异常，由调用方决定是否容忍
        if new_content is None:
            violations.append(f"段落缺失({nid})")
            continue
        if new_content != old_content:
            title = next((n.title for n in old_outline.nodes if n.id == nid), nid)
            violations.append(title)

    return violations


def _detect_overflow(new_md: str, outline, node_id: str) -> str | None:
    """检测模型是否越界返回了其他节点的内容。

    若返回片段中出现了其他节点的标题，说明它不只改了目标段落——
    此时拒绝替换，避免污染文档。
    """
    # 目标节点自身的标题允许出现
    target = outline.get(node_id)
    for n in outline.nodes:
        if n.id == node_id:
            continue
        # 用完整标题行匹配，避免误判
        for lvl in range(1, 7):
            if f"{'#' * lvl} {n.title}" in new_md:
                # 排除标题文字恰好是目标标题子串的情况
                if target and n.title not in (target.title or ""):
                    return f"{n.title}（{n.id}）"
                break
    return None


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


@router.post("/{tool_id}/modify-and-debug")
async def modify_and_debug(tool_id: str, req: "ModifyAndDebugRequest"):
    """AI 辅助修改 + 自动调试（SSE 流式）"""
    from app.api.schemas.tool_schemas import ModifyAndDebugRequest
    from sse_starlette.sse import EventSourceResponse
    from core.executor.tool_executor import ToolExecutor

    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")
    current_code = req.current_code or ""
    if not current_code.strip():
        code_path = registry._get_impl_dir() / tool_id / "tool.py"
        current_code = code_path.read_text() if code_path.exists() else ""
    if not req.request.strip():
        raise HTTPException(400, "修改描述不能为空")

    spec_md = req.spec_md or ""
    test_params = req.test_params or {}
    max_rounds = 50
    stop_key = f"modify_debug_{tool_id}"

    builder = ToolCodeBuilder()
    project_root = str(_Path(__file__).resolve().parent.parent.parent.parent)

    # 调试日志目录
    _log_dir = _Path(project_root) / "logs" / "modify_debug"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = _log_dir / f"{tool_id}_{int(datetime.now().timestamp())}.log"

    async def event_generator():
        yield {"event": "debug_start", "data": _json.dumps({"max_rounds": max_rounds, "log_file": str(_log_file)})}

        # 写入初始日志
        with open(_log_file, "a", encoding="utf-8") as lf:
            lf.write(f"=== AI 辅助修改调试日志 ===\n")
            lf.write(f"工具ID: {tool_id}\n修改建议: {req.request}\n最大轮次: {max_rounds}\n\n")

        base_prompt = f"""你是一个 Python 代码修改专家。根据用户的修改建议调整工具代码。

## 工具接口约束（必须严格遵守）
- 入口函数签名必须是: def execute(**kwargs) -> dict
- 参数访问使用 kwargs.get("参数名", 默认值)
- 返回值格式: {{"status":"success"|"failed","output_format":"text"|"image"|"table"|"file","message":"结果描述","data":{{}}}}
- 不改变现有参数的名称和类型，不删除现有功能
- 异常处理完善（try/except），失败时返回 status=failed

## 工具规范文档
{spec_md if spec_md else "（无）"}

## 当前代码
```python
{current_code}
```

## 用户修改建议
{req.request}

## 要求
根据修改建议调整代码，保持接口签名和参数不变。**必须进行实质性修改**，不能原样返回当前代码。直接返回完整修改后的 Python 代码，不要任何解释和markdown标记。"""

        code = current_code
        for round_num in range(1, max_rounds + 1):
            # 检查停止标志
            if hasattr(ToolExecutor, '_debug_states') and ToolExecutor._debug_states.get(stop_key, False):
                ToolExecutor._debug_states.pop(stop_key, None)
                yield {"event": "stopped", "data": _json.dumps({"code": code})}
                return

            yield {"event": "round_start", "data": _json.dumps({"round": round_num})}

            with open(_log_file, "a", encoding="utf-8") as lf:
                lf.write(f"\n{'─'*50}\n[第{round_num}轮]\n{'─'*50}\n")

            # ── 步骤 1：LLM 输出原因分析和修改计划（流式显示在调试日志）──
            analysis_text = ""
            try:
                # 分析用的 prompt：第一轮基于修改建议分析，后续轮次基于错误信息分析
                if round_num == 1:
                    analysis_prompt = f"根据用户的修改建议，简要分析需要如何修改代码（50字内，不要输出代码）：\n修改建议: {req.request}"
                else:
                    analysis_prompt = f"根据执行错误，简要分析失败原因和修复计划（50字内，不要输出代码）：\nstdout: {stdout[:300]}\nstderr: {stderr[:300]}"
                analysis_stream = llm.chat_stream(
                    messages=[{"role": "user", "content": analysis_prompt}],
                    temperature=0.3, max_tokens=300,
                )
                async for token in analysis_stream:
                    analysis_text += token
                    yield {"event": "thinking_stream", "data": _json.dumps({"token": token})}
            except Exception:
                yield {"event": "thinking_stream", "data": _json.dumps({"token": "（正在分析...）"})}

            with open(_log_file, "a", encoding="utf-8") as lf:
                lf.write(f"分析: {analysis_text}\n\n")

            # ── 步骤 2：LLM 生成完整代码（只更新代码面板，不显示在日志）──
            try:
                response = await llm.chat(
                    messages=[{"role": "user", "content": base_prompt}],
                    temperature=0.3, max_tokens=100000, timeout=60,
                )
            except Exception as e:
                with open(_log_file, "a", encoding="utf-8") as lf:
                    lf.write(f"❌ LLM调用失败: {e}\n")
                yield {"event": "error", "data": _json.dumps({"message": f"LLM调用失败: {e}"})}
                return

            with open(_log_file, "a", encoding="utf-8") as lf:
                lf.write(f"LLM返回代码 ({len(response)}字符)\n")

            # 提取代码
            new_code = response
            if "```" in new_code:
                start_marker = "```python" if "```python" in new_code else "```"
                parts = new_code.split(start_marker, 1)
                if len(parts) > 1:
                    after = parts[1]
                    if "```" in after:
                        after = after.split("```", 1)[0]
                    new_code = after
            new_code = new_code.strip()
            lines = new_code.split("\n")
            for i, l in enumerate(lines):
                if l.strip() and (l.startswith(("import ", "def ", "from ", "#", "try:", "if ")) or l.strip() == ""):
                    break
                if i > 5:
                    break
            else:
                i = 0
            new_code = "\n".join(lines[i:]).strip()
            if new_code.endswith("```"):
                new_code = new_code[:-3].strip()

            code = new_code
            yield {"event": "code_updated", "data": _json.dumps({"code": code})}

            with open(_log_file, "a", encoding="utf-8") as lf:
                lf.write(f"代码 ({len(code)}字符)\n")

            # 沙箱执行
            try:
                result = await builder.sandbox_execute(code, test_params, tool_id, exec_timeout=30)
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                exit_code = result.get("exit_code", -1)
                success = exit_code == 0 and not stderr.strip()

                with open(_log_file, "a", encoding="utf-8") as lf:
                    lf.write(f"执行: exit_code={exit_code}, success={success}\n")
                    lf.write(f"stdout: {stdout[:500]}\nstderr: {stderr[:500]}\n")

                yield {"event": "exec_result", "data": _json.dumps({
                    "stdout": stdout[:1000], "stderr": stderr[:1000], "success": success
                })}

                if success:
                    with open(_log_file, "a", encoding="utf-8") as lf:
                        lf.write(f"✅ 调试成功！总轮次: {round_num}\n")
                    yield {"event": "done", "data": _json.dumps({"code": code, "rounds": round_num, "success": True})}
                    return

                # 失败 → 更新 prompt 用于下一轮
                base_prompt = f"""上一轮代码执行失败。

## 错误信息
stdout: {stdout[:500]}
stderr: {stderr[:500]}

## 用户修改建议
{req.request}

## 失败代码
```python
{code}
```

先简要分析失败原因和修复计划（50字内），然后输出完整修复后的 Python 代码。"""

            except Exception as e:
                with open(_log_file, "a", encoding="utf-8") as lf:
                    lf.write(f"❌ 沙箱执行异常: {e}\n")
                yield {"event": "error", "data": _json.dumps({"message": f"沙箱执行异常: {e}"})}
                return

        with open(_log_file, "a", encoding="utf-8") as lf:
            lf.write(f"⚠ 达到最大轮次 {max_rounds}，调试未通过\n")
        yield {"event": "done", "data": _json.dumps({"code": code, "rounds": max_rounds, "success": False})}

    return EventSourceResponse(event_generator())


def _build_refine_prompt(outline, node, feedback: str,
                         mode: str = "fast") -> tuple[str, str, str]:
    """构造精化的 system/user prompt（流式与非流式共用，避免两份逻辑漂移）

    Args:
        mode:
          - "fast"：精简 prompt。实测约 11 秒。
          - "full"：含 few-shot 示例与其他节点上下文。实测约 21 秒（**慢 2 倍**）。

    为什么分档（参照 Claude Code 的 effort 分级哲学）：
    实测三档耗时 ——
        A 短prompt+短内容(改代码)   2,394 ms (1.0x)
        B 短prompt+文档段          10,896 ms (4.6x)
        C 完整prompt+文档段        21,295 ms (8.9x)
    few-shot 与其他节点上下文让耗时翻倍，但能提升改动的保守性。
    因此默认用 fast，仅在校验失败时升级到 full 重试 ——
    既快，又不牺牲可靠性。
    """
    heading_prefix = "#" * node.level

    if mode == "fast":
        system_prompt = (
            "你是保守的编辑，不是作者。只改动使用者要求的地方，其余内容原样保留。\n"
            "禁止改同义词、调语序、润色措辞、改动其他单元格。\n"
            "只输出修改后的这一个段落，保留原标题行与层级。不要解释，不要 markdown 代码块。"
        )
        user_prompt = f"""【当前要修改的段落】
{node.content_md}

【使用者反馈】
{feedback}

请输出修改后的这一个段落（标题行必须是 `{heading_prefix} {node.title}`）："""
        return system_prompt, user_prompt, heading_prefix

    context = outline_context_for_prompt(outline, exclude_id=node.id)
    system_prompt = (
        "你是**保守的编辑**，不是作者。修改工具规范文档的一个段落。\n\n"
        "【核心原则：最小必要改动】\n"
        "只改动「使用者反馈」直接要求的那几处，其余文字必须原样保留。\n"
        "禁止的行为：\n"
        "  - 改同义词（如 必须→应当、使用→采用）\n"
        "  - 调整语序或句式\n"
        "  - 补充过渡句、润色措辞\n"
        "  - 修改表格中未提及的单元格\n"
        "  - 调整格式（列宽、空行、标点）\n"
        "即使原文表达不完美，也不要顺手改进 —— 使用者没有要求。\n\n"
        "【坏例子 vs 好例子】\n"
        "反馈：「并发数默认值改成 4」\n"
        "  坏：重写整段说明文字，把「并发线程数」改成「并行处理线程数量」，\n"
        "      并给其他参数也补了说明  ← 改动远超要求\n"
        "  好：只把默认值那一格从 8 改成 4，其余一字不动\n\n"
        "【输出要求】\n"
        "1. 只输出修改后的**这一个段落**，不要输出整篇文档\n"
        "2. 必须保留原标题行，标题文字与层级不变\n"
        "3. 保持原有 Markdown 结构（表格仍是表格，行列数不变）\n"
        "4. 若该改动会影响文档其他段落，末尾另起一行写：\n"
        "   <!-- 影响提示: 说明影响 -->\n"
        "5. 不要输出任何解释，不要 markdown 代码块包裹"
    )
    user_prompt = f"""【文档其他段落】（仅供参考，绝对不要修改它们）
{context}

【当前要修改的段落】
{node.content_md}

【使用者反馈】
{feedback}

请输出修改后的这一个段落（标题行必须是 `{heading_prefix} {node.title}`，仅改动上述反馈要求之处）："""
    return system_prompt, user_prompt, heading_prefix


def _postprocess_refined(
    raw: str, outline, node, spec_md: str, impact_hint: str
) -> tuple[str, str, str, object, str]:
    """对模型返回做清洗、越界检测、一致性提醒，返回
    (new_node_md, updated_md, impact_hint, new_outline, error)

    error 非空表示应阻止替换。
    """
    new_node_md = (raw or "").strip()
    for marker in ("```markdown", "```md", "```"):
        if new_node_md.startswith(marker):
            new_node_md = new_node_md[len(marker):].strip()
            if new_node_md.rstrip().endswith("```"):
                new_node_md = new_node_md.rstrip()[:-3].strip()
            break

    if not new_node_md:
        return "", "", impact_hint, None, "模型未返回有效内容，请重试或换一种描述方式"

    overflow = _detect_overflow(new_node_md, outline, node.id)
    if overflow:
        return "", "", impact_hint, None, (
            f"模型返回的内容包含了其他段落（{overflow}），已阻止替换。"
            f"请缩小描述范围后重试。"
        )

    m = re.search(r"<!--\s*影响提示[:：]\s*(.*?)\s*-->", new_node_md, re.S)
    if m:
        hint = m.group(1).strip()
        impact_hint = f"{impact_hint}；{hint}" if impact_hint else hint
        new_node_md = re.sub(
            r"<!--\s*影响提示[:：].*?-->", "", new_node_md, flags=re.S
        ).rstrip()

    updated_md = replace_node_content(spec_md, node, new_node_md)
    new_outline = parse_markdown_outline(updated_md, tool_id=outline.tool_id)
    new_outline.version = outline.version + 1

    violations = _check_untouched_intact(outline, new_outline, node.id)
    if violations:
        return "", "", impact_hint, None, (
            f"检测到未请求的段落被改动：{', '.join(violations[:3])}。"
            f"已阻止替换以保证文档其余部分不受影响，请缩小描述范围后重试。"
        )

    consistency_hint = _check_value_consistency(node.content_md, new_node_md)
    if consistency_hint:
        impact_hint = (
            f"{impact_hint}；{consistency_hint}" if impact_hint else consistency_hint
        )

    return new_node_md, updated_md, impact_hint, new_outline, ""


@router.post("/{tool_id}/refine-spec-node-stream")
async def refine_spec_node_stream(tool_id: str, req: RefineSpecNodeRequest):
    """流式精化单个节点（SSE）。

    与 refine-spec-node 逻辑一致，但边生成边推送，让使用者看到进展。
    实测该模型思考阶段很长（TTFT 可达 10~16 秒），期间界面若无任何反馈
    会被认为"卡住"。流式至少能在生成阶段实时显示内容。

    事件类型：
    - token : 增量文本
    - done  : 完成，data 含完整结果（与 refine-spec-node 响应一致）
    - error : 失败
    """
    spec_md = req.spec_md
    is_draft = tool_id in ("", "_draft_", "draft", "@new")
    if not spec_md.strip() and not is_draft:
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        if not spec_path.exists():
            raise HTTPException(404, "该工具尚无规范文档")
        spec_md = spec_path.read_text(encoding="utf-8")

    if not spec_md.strip():
        raise HTTPException(400, "缺少 spec_md：新建文档请直接传入文档内容")
    if is_draft:
        req.save = False

    feedback = (req.feedback or "").strip()
    if not feedback:
        raise HTTPException(400, "请说明这个节点有什么问题")

    outline = parse_markdown_outline(spec_md, tool_id=tool_id)
    node = outline.get(req.node_id)
    if node is None:
        raise HTTPException(404, f"未找到节点 '{req.node_id}'")

    # 默认 fast（约 11 秒）；校验失败才升级 full（约 21 秒）
    used_mode = "fast"
    system_prompt, user_prompt, _ = _build_refine_prompt(outline, node, feedback, "fast")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    async def event_generator():
        nonlocal used_mode
        buf: list[str] = []
        try:
            # 先推送一个 begin 事件，前端可据此显示"模型已开始思考"
            yield {"event": "begin", "data": _json.dumps({"node_id": node.id})}

            async for tok in llm.chat_stream(
                messages=messages, temperature=0.0, max_tokens=4000
            ):
                buf.append(tok)
                yield {"event": "token", "data": _json.dumps({"t": tok}, ensure_ascii=False)}

            raw = "".join(buf)
            new_node_md, updated_md, impact_hint, new_outline, err = _postprocess_refined(
                raw, outline, node, spec_md, ""
            )

            # fast 档校验失败 → 升级 full 档重试一次
            if err:
                used_mode = "full"
                yield {"event": "retry", "data": _json.dumps(
                    {"reason": err[:160], "mode": "full"}, ensure_ascii=False)}
                s2, u2, _ = _build_refine_prompt(outline, node, feedback, "full")
                buf2: list[str] = []
                async for tok in llm.chat_stream(
                    messages=[{"role": "system", "content": s2},
                              {"role": "user", "content": u2}],
                    temperature=0.0, max_tokens=4000
                ):
                    buf2.append(tok)
                    yield {"event": "token", "data": _json.dumps({"t": tok}, ensure_ascii=False)}
                raw = "".join(buf2)
                new_node_md, updated_md, impact_hint, new_outline, err = _postprocess_refined(
                    raw, outline, node, spec_md, ""
                )

            if err:
                yield {"event": "error", "data": _json.dumps({"message": err}, ensure_ascii=False)}
                return

            saved = False
            if req.save and not is_draft:
                await registry.update(tool_id, {"raw_md": updated_md})
                try:
                    spec_path = registry._get_def_dir() / f"{tool_id}.md"
                    spec_path.parent.mkdir(parents=True, exist_ok=True)
                    spec_path.write_text(updated_md, encoding="utf-8")
                    saved = True
                except Exception:
                    pass

            yield {"event": "done", "data": _json.dumps({
                "node_id": req.node_id,
                "node_md": new_node_md,
                "updated_md": updated_md,
                "outline": new_outline.to_dict(),
                "diff": {"before": node.content_md, "after": new_node_md},
                "impact_hint": impact_hint,
                "saved": saved,
            }, ensure_ascii=False)}
        except Exception as e:
            yield {"event": "error", "data": _json.dumps(
                {"message": f"精化失败: {str(e)[:300]}"}, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


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


class SaveSpecRequest(BaseModel):
    """保存工具 MD 规范文档请求"""
    spec_md: str


@router.post("/{tool_id}/save-spec")
async def save_spec(tool_id: str, req: SaveSpecRequest):
    """保存手工编辑后的 MD 规范文档"""
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")
    if not req.spec_md.strip():
        raise HTTPException(400, "文档内容不能为空")

    await registry.update(tool_id, {"raw_md": req.spec_md})
    try:
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(req.spec_md, encoding="utf-8")
    except Exception:
        pass
    return {"saved": tool_id}


class SyncSpecRequest(BaseModel):
    """代码 → 文档反向同步请求"""
    tool_id: str
    code: str
    original_code: str = ""
    current_spec: str = ""


@router.post("/{tool_id}/sync-spec-from-code")
async def sync_spec_from_code(tool_id: str, req: SyncSpecRequest):
    """依据使用者微调后的代码，反向更新 MD 规范文档。

    打通「文档 → 代码 → 文档」的双向闭环：AI 生成的文档产出代码，
    使用者手工微调代码后，再由 AI 把改动同步回文档，避免文档与代码脱节。
    """
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")

    code = req.code or ""
    if not code.strip():
        raise HTTPException(400, "代码不能为空")

    current_spec = req.current_spec or ""
    if not current_spec.strip():
        # 无历史文档时退化为「由代码反推文档」，保证接口始终可用
        current_spec = f"# {entry.get('name', tool_id)}\n\n（暂无历史规范文档，以下由代码反推生成）\n"

    # 截断超长代码，避免超出模型上下文；优先保留改动前后的关键片段
    MAX_CHARS = 24000
    def _clip(text: str) -> str:
        if not text:
            return "(空)"
        return text if len(text) <= MAX_CHARS else (
            text[:MAX_CHARS] + f"\n...[内容过长已截断，共 {len(text)} 字符]..."
        )

    system_prompt = (
        "你是 SOTABand 工具规范文档的维护专家。使用者手工修改了一个工具的代码，"
        "请你据此更新该工具的 MD 规范文档。\n\n"
        "【核心原则】\n"
        "1. 只更新受本次代码改动实际影响的段落（通常是：输入规范、输出规范、"
        "依赖环境、运行机制）。\n"
        "2. 未受影响的段落（功能概述、版本历史等）必须原样保留，不要改写。\n"
        "3. 严格保持原文档的 Markdown 结构、标题层级与表格格式。\n"
        "4. 若改动引入了新的输入参数，必须在「输入规范」表格中补充对应行。\n"
        "5. 若改动改变了返回值结构，必须同步「输出规范」表格。\n"
        "6. 只输出更新后的完整 MD 文档，不要任何解释性文字，不要 markdown 代码块包裹。"
    )

    user_prompt = f"""工具名称：{entry.get('name', tool_id)}

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

    # 清理模型可能附带的代码块标记
    cleaned = (updated or "").strip()
    for marker in ("```markdown", "```md", "```"):
        if cleaned.startswith(marker):
            cleaned = cleaned[len(marker):].strip()
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3].rstrip()
            break

    if not cleaned:
        raise HTTPException(500, "同步失败：模型未返回有效文档")

    # 落盘：更新 registry 中的 raw_md 与 definitions 下的 spec 文件
    await registry.update(tool_id, {"raw_md": cleaned})
    try:
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(cleaned, encoding="utf-8")
    except Exception:
        pass  # 文档落盘失败不影响返回，registry 已更新

    return {"spec_md": cleaned}


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
    def_dir = registry._get_def_dir()
    impl_dir = registry._get_impl_dir() / tool_id

    # 删除实现目录
    if impl_dir.exists():
        shutil.rmtree(impl_dir, ignore_errors=True)

    # 删除所有关联文件：spec.md, demand.md, reference.md 等
    for suffix in [".md", "-demand.md", "-reference.md"]:
        p = def_dir / f"{tool_id}{suffix}"
        if p.exists():
            p.unlink()

    # 从 registry 中移除
    await registry.unregister(tool_id)
    return {"deleted": tool_id}

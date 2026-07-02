"""工具管理路由 — 规格生成、代码生成、沙箱测试、注册、调用"""

from fastapi import APIRouter, HTTPException

from app.api.schemas.tool_schemas import (
    GenerateToolSpecRequest,
    GenerateToolCodeRequest,
    RegisterToolRequest,
    ExecuteToolRequest,
    ModifyCodeRequest,
)
from core.llm.client import create_llm_client
from core.resource.builder.tool_builder import ToolCodeBuilder
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
| status | string | 执行状态 (success/failed) |
| message | string | 结果说明 |
| output_format | string | **必须指定** — text / image / table / file |
| data | dict | 输出数据，格式由 output_format 决定 |

### 3.2 output_format 说明
- `text`: data 含 `{"text": "..."}` — 纯文本展示
- `image`: data 含 `{"image_path": "/path/to/result.png"}` — 界面直接绘制图片（仅支持路径方式，不支持 base64）
- `table`: data 含 `{"columns":["c1","c2"], "rows":[[...],...]}` — 渲染为表格
- `file`: data 含 `{"file_path": "/path/to/result.csv"}` — 文件下载链接

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|

## 5. 运行机制

### 5.1 执行流程

1.
2.
3.

### 5.2 性能指标

- 预期执行时间: < 5s
- 内存占用: < 500MB

### 5.3 错误处理

- 参数无效 → 返回验证错误
- 执行异常 → 捕获并返回详细错误

## 6. 测试用例

### 6.1 测试数据描述

```json
{{}}
```

### 6.2 边界条件

## 7. 调用示例

```python
result = execute(...)
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | {日期} | 初始版本 |

规则：
1. tool-id 使用小写字母+连字符
2. type 推断为 function/script/api-wrapper
3. **output_format 必须根据用户需求推断：**
   - 图片生成/绘制/可视化 → image
   - 数据处理/统计/查询返回结构化数据 → table
   - 文件转换/下载 → file
   - 一般计算/文本回复 → text
4. 合理填充所有字段
5. 只输出 Markdown"""


@router.post("/generate-spec")
async def generate_spec(req: GenerateToolSpecRequest):
    """NL → MD 工具描述文档"""
    if not req.description.strip():
        raise HTTPException(400, "description 不能为空")

    response = await llm.chat(
        messages=[
            {"role": "system", "content": SPEC_PROMPT},
            {"role": "user", "content": req.description},
        ],
        temperature=0.5, max_tokens=100000,
    )
    return {"spec_md": response.strip()}


@router.post("/generate-code")
async def generate_code(req: GenerateToolCodeRequest):
    """MD → 工具代码 + 测试数据"""
    if not req.spec_md.strip():
        raise HTTPException(400, "specMd 不能为空")

    spec = {"raw_md": req.spec_md, "id": req.tool_id, "name": req.tool_name}
    valid = await builder.validate_spec(spec)
    if not valid:
        raise HTTPException(400, "MD 规范文档不完整，缺少必需段落")

    code = await builder.build(spec)
    test_data = await builder.generate_test_data(spec)

    return {"code": code, "test_data": test_data}


@router.post("/test")
async def test_tool(req: GenerateToolCodeRequest):
    """沙箱测试工具代码"""
    spec = {"raw_md": req.spec_md, "id": req.tool_id, "name": req.tool_name}

    # 如果前端传了已有代码，直接使用；否则调用 LLM 生成
    if req.code.strip():
        code = req.code
    else:
        code = await builder.build(spec)

    test_data = await builder.generate_test_data(spec)
    normal_input = test_data.get("normal", {}).get("input", {})

    results = await builder.dry_run(code, normal_input)
    return {"code": code, "sandbox_results": results, "test_data": test_data}


@router.post("/register")
async def register_tool(req: RegisterToolRequest):
    """注册工具"""
    spec = {"raw_md": req.spec_md, "id": req.tool_id, "name": req.tool_name}

    # 先做沙箱测试
    code = req.code or (await builder.build(spec))
    test_data = req.test_data or await builder.generate_test_data(spec)
    normal_input = test_data.get("normal", {}).get("input", {})
    sandbox = await builder.dry_run(code, normal_input)

    if sandbox["failed"]:
        raise HTTPException(400, f"沙箱测试未通过: {sandbox['failed']}")

    # 提取参数元数据
    param_meta = []
    try:
        param_meta = await builder.extract_param_metadata(req.spec_md)
    except Exception:
        pass

    resource = {
        "id": req.tool_id, "name": req.tool_name, "version": req.version,
        "raw_md": req.spec_md, "code": code, "tags": req.tags,
        "test_data": test_data, "param_meta": param_meta,
    }
    tool_id = await registry.register(resource)
    entry = await registry.get(tool_id)

    # 创建工具独立虚拟环境并安装依赖
    deps = []
    if req.spec_md:
        import re as _re
        dep_section = _re.search(r'## 4\.\s*依赖环境\n(.*?)(?=\n##|\Z)', req.spec_md, _re.DOTALL)
        if dep_section:
            for line in dep_section.group(1).strip().split("\n"):
                if line.startswith("|") and "---" not in line and "依赖" not in line:
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if parts and parts[0] and not parts[0].startswith("--"):
                        deps.append(parts[0])
    if deps:
        try:
            await builder.setup_venv(tool_id, deps)
            entry["venv"] = True
        except Exception:
            pass

    return {"tool_id": tool_id, "entry": entry, "sandbox_results": sandbox}


@router.get("/list")
async def list_tools():
    """列出所有工具"""
    tools = await registry.list_all()
    return {"tools": tools}


@router.get("/{tool_id}")
async def get_tool(tool_id: str):
    """工具详情（含 MD spec + 源代码）"""
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")

    spec_path = registry._get_def_dir() / f"{tool_id}.md"
    spec_md = spec_path.read_text() if spec_path.exists() else ""

    code_path = registry._get_impl_dir() / tool_id / "tool.py"
    code = code_path.read_text() if code_path.exists() else ""

    return {**entry, "spec_md": spec_md, "code": code}


@router.post("/{tool_id}/update-code")
async def update_tool_code(tool_id: str, req: RegisterToolRequest):
    """更新工具代码（需重新沙箱测试通过）"""
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")

    code = req.code
    if not code.strip():
        raise HTTPException(400, "code 不能为空")

    # 沙箱测试
    spec = {"raw_md": req.spec_md or "", "id": tool_id, "name": entry["name"]}
    test_data = req.test_data or await builder.generate_test_data(spec)
    normal_input = test_data.get("normal", {}).get("input", {})
    # 如果测试输入为空，至少传一个空 dict 验证代码能跑
    sandbox = await builder.dry_run(code, normal_input or {})

    if sandbox["failed"]:
        raise HTTPException(400, f"沙箱测试未通过: {sandbox['failed']}")

    # 更新代码文件
    code_path = registry._get_impl_dir() / tool_id / "tool.py"
    code_path.write_text(code)

    if req.spec_md.strip():
        spec_path = registry._get_def_dir() / f"{tool_id}.md"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(req.spec_md)

    return {"tool_id": tool_id, "status": "updated", "sandbox_results": sandbox}


@router.post("/{tool_id}/modify-code")
async def modify_code(tool_id: str, req: "ModifyCodeRequest"):
    """AI 辅助修改代码：保持接口不变，根据用户描述修改代码"""
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")

    current_code = req.current_code
    user_request = req.request
    if not user_request.strip():
        raise HTTPException(400, "修改描述不能为空")

    if not current_code.strip():
        code_path = registry._get_impl_dir() / tool_id / "tool.py"
        current_code = code_path.read_text() if code_path.exists() else ""

    prompt = f"""Modify the following Python tool code according to the user's request.

IMPORTANT RULES:
1. DO NOT change the function signature: def execute(**kwargs) -> dict[str, Any]:
2. DO NOT change the return format: {{"status": "success"|"failed", "message": "...", ...}}
3. Keep all existing imports unless they become unused
4. Only change the logic as described by the user
5. Return ONLY the complete modified code, no explanations

Current code:
```python
{current_code}
```

User's modification request:
{user_request}

Modified code:"""

    response = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=100000,
    )

    modified_code = response
    if "```python" in modified_code:
        modified_code = modified_code.split("```python")[1].split("```")[0]
    elif "```" in modified_code:
        modified_code = modified_code.split("```")[1].split("```")[0]

    return {"modified_code": modified_code.strip(), "original_code": current_code}


@router.post("/{tool_id}/execute")
async def execute_tool(tool_id: str, req: ExecuteToolRequest):
    """调用工具"""
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")

    impl_dir = registry._get_impl_dir() / tool_id / "tool.py"
    if not impl_dir.exists():
        raise HTTPException(400, f"工具代码不存在: {impl_dir}")

    import importlib.util
    spec = importlib.util.spec_from_file_location(f"tool_{tool_id}", str(impl_dir))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "execute"):
        raise HTTPException(500, "工具代码未实现 execute() 函数")

    try:
        result = module.execute(**req.params)
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@router.get("/search/find")
async def search_tools(q: str = "", tags: str = ""):
    """搜索工具"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    results = await discoverer.search(query=q, tags=tag_list)
    return {"tools": results}


@router.delete("/{tool_id}")
async def delete_tool(tool_id: str):
    """删除工具（registry + code + venv）"""
    entry = await registry.get(tool_id)
    if not entry:
        raise HTTPException(404, f"Tool '{tool_id}' not found")
    import shutil
    impl_dir = registry._get_impl_dir() / tool_id
    if impl_dir.exists():
        shutil.rmtree(impl_dir, ignore_errors=True)
    spec_path = registry._get_def_dir() / f"{tool_id}.md"
    if spec_path.exists():
        spec_path.unlink()
    await registry.unregister(tool_id)
    return {"deleted": tool_id}

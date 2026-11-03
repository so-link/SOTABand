"""工具「编辑后重新注册」的更新语义测试。

覆盖历史缺陷：详情页点「编辑」→ 改 MD/代码 → 重新注册，会新增一条
重复工具，且版本号被重置回 0.1.0。

根因是注册身份只从 MD 正文推导（``extractIdName``），MD 里 ``id:`` 一变
就 append 新条目。修复后注册以 ``baseToolId``（前端编辑态原工具 id）为
权威身份，命中已有工具即原地更新，并把版本号 patch+1。
"""

import pytest
from fastapi import HTTPException

from app.api.routes import tool_routes
from app.api.schemas.tool_schemas import RegisterToolRequest
from core.resource import paths

SPEC_MD = """---
id: weather-query
name: 天气查询
version: 0.3.0
type: function
language: python
status: active
created: 2026-01-01
---

# 天气查询

## 1. 功能概述

按城市名查询天气。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| city | string | 是 | 无 | 城市名 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| data | dict | 天气数据 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|

## 5. 运行机制

1. 读取参数
2. 返回结果

## 6. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-01-01 | 初始版本 |
"""

CODE = "def execute(**kwargs):\n    return {'status': 'success'}\n"


@pytest.fixture()
def isolated_registry(tmp_path, monkeypatch):
    """把私有资源根指向 tmp，避免污染真实 resources-private。

    同时把「从 MD 抽取参数元数据」打桩为空列表——否则会真的调用 LLM。
    """
    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "resources-private")

    async def _no_param_meta(spec_md: str) -> list:
        return []

    monkeypatch.setattr(tool_routes.builder, "extract_param_metadata", _no_param_meta)
    return tool_routes.registry


def _req(**kwargs) -> RegisterToolRequest:
    payload = {
        "specMd": SPEC_MD,
        "code": CODE,
        "toolId": "weather-query",
        "toolName": "天气查询",
        "tags": ["天气"],
    }
    payload.update(kwargs)
    return RegisterToolRequest(**payload)


@pytest.mark.asyncio
async def test_register_new_tool_starts_at_0_1_0(isolated_registry):
    result = await tool_routes.register_tool(_req())

    assert result["tool_id"] == "weather-query"
    assert result["updated"] is False
    assert result["version"] == "0.1.0"
    assert result["entry"]["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_reregister_updates_in_place_instead_of_duplicating(isolated_registry):
    await tool_routes.register_tool(_req())

    # 模拟「返回修改需求 → 重新生成文档」：MD 里的 id 被模型改写
    changed_md = SPEC_MD.replace("id: weather-query", "id: weather-query-v2")
    result = await tool_routes.register_tool(
        _req(specMd=changed_md, toolId="weather-query-v2", baseToolId="weather-query")
    )

    assert result["tool_id"] == "weather-query", "编辑态必须以 baseToolId 为权威身份"
    assert result["updated"] is True
    ids = [t["id"] for t in await isolated_registry.list_all()]
    assert ids.count("weather-query") == 1
    assert "weather-query-v2" not in ids, "不得因为 MD 里的 id 变化而新增工具"


@pytest.mark.asyncio
async def test_reregister_bumps_patch_version(isolated_registry):
    await tool_routes.register_tool(_req())
    assert (await isolated_registry.get("weather-query"))["version"] == "0.1.0"

    result = await tool_routes.register_tool(_req(baseToolId="weather-query"))
    assert result["version"] == "0.1.1"
    assert result["previous_version"] == "0.1.0"
    assert (await isolated_registry.get("weather-query"))["version"] == "0.1.1"

    # 连续编辑：0.1.1 → 0.1.2
    result = await tool_routes.register_tool(_req(baseToolId="weather-query"))
    assert result["version"] == "0.1.2"


@pytest.mark.asyncio
async def test_reregister_preserves_created_metadata(isolated_registry):
    await tool_routes.register_tool(_req())
    await isolated_registry.update(
        "weather-query",
        {"created_at": "2020-01-01T00:00:00Z", "usage_count": 7, "owner": "someone"},
    )

    await tool_routes.register_tool(_req(baseToolId="weather-query"))

    entry = await isolated_registry.get("weather-query")
    assert entry["created_at"] == "2020-01-01T00:00:00Z"
    assert entry["usage_count"] == 7
    assert entry["owner"] == "someone"
    assert entry["updated_at"], "更新时应记录 updated_at"


@pytest.mark.asyncio
async def test_reregister_keeps_existing_tags_when_not_supplied(isolated_registry):
    await tool_routes.register_tool(_req())
    await tool_routes.register_tool(_req(baseToolId="weather-query", tags=[]))

    assert (await isolated_registry.get("weather-query"))["tags"] == ["天气"]


@pytest.mark.asyncio
async def test_explicit_version_wins(isolated_registry):
    await tool_routes.register_tool(_req())
    result = await tool_routes.register_tool(_req(baseToolId="weather-query", version="2.0.0"))

    assert result["version"] == "2.0.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["", "../evil", "a/b", "a b"])
async def test_illegal_base_id_rejected(isolated_registry, bad_id):
    with pytest.raises(HTTPException) as exc:
        await tool_routes.register_tool(_req(baseToolId=bad_id, toolId=bad_id))
    assert exc.value.status_code == 400

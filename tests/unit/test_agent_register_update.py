"""Agent「编辑后重新注册」与双根查找优先级的测试。

与工具侧同构的两个缺陷：
1. 注册身份只从 MD 正文推导（``agent_routes.register_agent`` 会扫 ``id:`` 行），
   MD 里的 id 一变就新增一个重复 Agent；版本恒被重置为 0.1.0。
2. 读取/执行按「内置优先」定位，写入却落私有根 —— 「同 id 同时存在」的
   Agent 永远读到内置旧文档 / 旧代码（编辑后重新打开还是旧的）。
"""

import json

import pytest
from fastapi import HTTPException

from app.api.routes import agent_routes
from app.api.schemas.agent_schemas import RegisterAgentRequest
from core.resource import paths
from core.resource.registry.agent_registry import AgentRegistry

AGENT_MD = """---
id: report-agent
name: 报表 Agent
version: 0.3.0
role: task
status: active
created: 2026-01-01
---

# 报表 Agent

## 1. 功能概述

汇总数据并生成报表。

## 2. 输入规范

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| dataset | string | 是 | 数据集名 |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| report | string | 报表内容 |

## 4. 运行机制

1. 读取数据集
2. 输出报表
"""

CODE = "def run(**kwargs):\n    return {'status': 'success'}\n"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """私有根指向 tmp，避免污染真实 resources-private。"""
    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "resources-private")
    return agent_routes.registry


def _req(**kwargs) -> RegisterAgentRequest:
    payload = {
        "specMd": AGENT_MD,
        "code": CODE,
        "agentId": "report-agent",
        "agentName": "报表 Agent",
        "role": "task",
        "tags": ["报表"],
    }
    payload.update(kwargs)
    return RegisterAgentRequest(**payload)


@pytest.mark.asyncio
async def test_register_new_agent_starts_at_0_1_0(isolated):
    result = await agent_routes.register_agent(_req())

    assert result["agent_id"] == "report-agent"
    assert result["updated"] is False
    assert result["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_reregister_updates_in_place_instead_of_duplicating(isolated):
    await agent_routes.register_agent(_req())

    # 模拟「返回修改需求 → 重新生成文档」：MD 里的 id 被模型改写
    changed_md = AGENT_MD.replace("id: report-agent", "id: report-agent-v2")
    result = await agent_routes.register_agent(
        _req(specMd=changed_md, agentId="report-agent-v2", baseAgentId="report-agent")
    )

    assert result["agent_id"] == "report-agent", "编辑态必须以 baseAgentId 为权威身份"
    assert result["updated"] is True
    ids = [a["id"] for a in await isolated.list_all()]
    assert ids.count("report-agent") == 1
    assert "report-agent-v2" not in ids


@pytest.mark.asyncio
async def test_reregister_bumps_patch_version(isolated):
    await agent_routes.register_agent(_req())
    result = await agent_routes.register_agent(_req(baseAgentId="report-agent"))

    assert result["version"] == "0.1.1"
    assert result["previous_version"] == "0.1.0"
    assert (await isolated.get("report-agent"))["version"] == "0.1.1"


@pytest.mark.asyncio
async def test_reregister_preserves_created_metadata(isolated):
    await agent_routes.register_agent(_req())
    await isolated.update(
        "report-agent",
        {"created_at": "2020-01-01T00:00:00Z", "owner": "someone", "health": "degraded"},
    )

    await agent_routes.register_agent(_req(baseAgentId="report-agent"))

    entry = await isolated.get("report-agent")
    assert entry["created_at"] == "2020-01-01T00:00:00Z"
    assert entry["owner"] == "someone"
    assert entry["health"] == "degraded"
    assert entry["updated_at"]


@pytest.mark.asyncio
async def test_reregister_keeps_role_and_tags_when_md_omits_them(isolated):
    md_ws = AGENT_MD.replace("role: task", "role: web-service")
    await agent_routes.register_agent(_req(specMd=md_ws, role="web-service"))
    assert (await isolated.get("report-agent"))["role"] == "web-service"

    # MD 里没有 role 行 → 更新时不得被前端兜底的 task 降级
    md_no_role = AGENT_MD.replace("role: task\n", "")
    await agent_routes.register_agent(
        _req(specMd=md_no_role, baseAgentId="report-agent", role="task", tags=[])
    )

    entry = await isolated.get("report-agent")
    assert entry["role"] == "web-service"
    assert entry["tags"] == ["报表"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["", "../evil", "a/b", "a b"])
async def test_illegal_agent_id_rejected(isolated, bad_id):
    with pytest.raises(HTTPException) as exc:
        await agent_routes.register_agent(_req(baseAgentId=bad_id, agentId=bad_id))
    assert exc.value.status_code == 400


def test_search_dirs_prefer_private(tmp_path, monkeypatch):
    builtin = tmp_path / "resources"
    private = tmp_path / "resources-private"
    monkeypatch.setattr(paths, "BUILTIN_ROOT", builtin)
    monkeypatch.setattr(paths, "PRIVATE_ROOT", private)

    reg = AgentRegistry()
    assert reg.get_spec_search_dirs() == [private / "agents" / "definitions", builtin / "agents" / "definitions"]
    assert reg.get_impl_search_dirs() == [private / "agents" / "implementations", builtin / "agents" / "implementations"]


def test_registry_merge_still_private_overrides_builtin(tmp_path, monkeypatch):
    builtin = tmp_path / "resources"
    private = tmp_path / "resources-private"
    monkeypatch.setattr(paths, "BUILTIN_ROOT", builtin)
    monkeypatch.setattr(paths, "PRIVATE_ROOT", private)

    for root, version in ((builtin, "0.1.0"), (private, "0.2.0")):
        (root / "agents").mkdir(parents=True, exist_ok=True)
        (root / "agents" / "registry.json").write_text(
            json.dumps([{"id": "report-agent", "name": "x", "version": version}]),
            encoding="utf-8",
        )

    merged = {e["id"]: e for e in AgentRegistry()._read()}
    assert merged["report-agent"]["version"] == "0.2.0"

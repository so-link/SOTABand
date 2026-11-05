"""数据集仓库的「可用性标记」+ 归属过滤测试。

背景：数据集注册表 ``data/registry.json`` 随项目目录走，而数据本身不随
（``data_path`` 常是原机器的绝对路径，如 ``/Users/leo/...``）。从别人环境复制
项目后，条目能看到但文件打不开 —— 前端文件选择框需要 ``is_local`` 才能区分
"可选" 与 "不可选"，并给出具体原因（而不是笼统的"暂无数据集"）。

2026-11-04 起仓库默认**只返回本人**数据集（D3/P1-3）：他人条目的名称与路径
也是信息泄露面。``include_others=true`` 仅租户 Owner/Admin 可用。
"""

import pytest

from app.api.routes import data_routes as dr


class _FakeContext:
    def __init__(self, user_id: str, tenant_role: str = "member"):
        self.user_id = user_id
        self.tenant_role = tenant_role


class _FakeRegistry:
    def __init__(self, entries):
        self._entries = entries

    async def list_all(self, owner=None):
        rows = [dict(e) for e in self._entries]
        if owner is None:
            return rows
        return [
            e
            for e in rows
            if (e.get("owner") == owner)
            or (not e.get("owner") and self.is_present_locally(e))
        ]

    def is_present_locally(self, entry):
        return bool(entry.get("_local"))


@pytest.fixture()
def fake_registry(monkeypatch):
    entries = [
        {"id": "local-ds", "name": "本机数据", "data_path": "/tmp/local", "_local": True, "owner": "u1"},
        {"id": "ghost-ds", "name": "他机数据", "data_path": "/Users/leo/x", "_local": False, "owner": "u9"},
        {"id": "legacy-ds", "name": "无主但有数据", "data_path": "/tmp/legacy", "_local": True},
        {"id": "no-path-ds", "name": "空路径", "data_path": "", "_local": False, "owner": "u1"},
    ]
    monkeypatch.setattr(dr, "registry", _FakeRegistry(entries))
    return entries


@pytest.mark.asyncio
async def test_repository_marks_local_and_owner(fake_registry):
    out = await dr.get_dataset_repository(ctx=_FakeContext("u1"))
    by_id = {d["id"]: d for d in out["datasets"]}

    # 本机有数据 + 本人 → 可用、归属匹配
    assert by_id["local-ds"]["is_local"] is True
    assert by_id["local-ds"]["owner_match"] is True

    # 历史条目（无 owner）但本机有数据 → 视为本人
    assert by_id["legacy-ds"]["is_local"] is True
    assert by_id["legacy-ds"]["owner_match"] is True

    # 空 data_path 不算本机可用（否则 resolve_data_path 会把空串当数据根目录）
    assert by_id["no-path-ds"]["is_local"] is False

    # 默认不返回他人条目（信息泄露面收口）
    assert "ghost-ds" not in by_id

    # data_path 始终存在（前端要用来展示"为什么不可用"）
    assert all("data_path" in d for d in out["datasets"])


@pytest.mark.asyncio
async def test_repository_include_others_requires_admin(fake_registry, monkeypatch):
    # 普通成员：include_others → 403
    with pytest.raises(dr.HTTPException) as exc:
        await dr.get_dataset_repository(include_others=True, ctx=_FakeContext("u1"))
    assert exc.value.status_code == 403

    # 租户 owner 也不再放行（数据集是用户私有资源，跨用户管理视图仅平台管理员）
    with pytest.raises(dr.HTTPException) as exc2:
        await dr.get_dataset_repository(
            include_others=True, ctx=_FakeContext("u1", tenant_role="owner")
        )
    assert exc2.value.status_code == 403

    # 平台管理员（SOTABAND_PLATFORM_ADMINS）可看全部（排查/迁移用）
    from config.settings import settings

    monkeypatch.setattr(settings.security, "platform_admins", ["u1"])
    out = await dr.get_dataset_repository(
        include_others=True, ctx=_FakeContext("u1", tenant_role="owner")
    )
    assert "ghost-ds" in {d["id"] for d in out["datasets"]}


@pytest.mark.asyncio
async def test_repository_keeps_tag_stats(fake_registry):
    for entry in fake_registry:
        if entry["id"] == "local-ds":
            entry["tags"] = ["视觉"]
        if entry["id"] == "ghost-ds":
            entry["tags"] = ["视觉", "公开"]
    out = await dr.get_dataset_repository(ctx=_FakeContext("u1"))
    # 默认只统计本人条目：ghost-ds 的标签不计入
    assert out["tag_stats"]["视觉"] == 1
    assert "公开" not in out["tag_stats"]

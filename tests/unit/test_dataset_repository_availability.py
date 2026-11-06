"""数据集仓库的「可用性标记」测试。

背景：数据集注册表 ``data/registry.json`` 随项目目录走，而数据本身不随
（``data_path`` 常是原机器的绝对路径，如 ``/Users/leo/...``）。从别人环境复制
项目后，条目能看到但文件打不开 —— 前端文件选择框需要 ``is_local`` 才能区分
"可选" 与 "不可选"，并给出具体原因（而不是笼统的"暂无数据集"）。
"""

import pytest

from tests.conftest import run_coro  # noqa: F401

pytestmark = pytest.mark.unit


from app.api.routes import data_routes as dr


class _FakeRegistry:
    def __init__(self, entries):
        self._entries = entries

    async def list_all(self, owner=None):
        return [dict(e) for e in self._entries]

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
    monkeypatch.setattr(dr, "get_current_user_id", lambda: "u1")
    return entries


@pytest.mark.asyncio
async def test_repository_marks_local_and_owner(fake_registry):
    out = await dr.get_dataset_repository()
    by_id = {d["id"]: d for d in out["datasets"]}

    # 本机有数据 + 本人 → 可用、归属匹配
    assert by_id["local-ds"]["is_local"] is True
    assert by_id["local-ds"]["owner_match"] is True

    # 别人的路径 → 能看到但不可用
    assert by_id["ghost-ds"]["is_local"] is False
    assert by_id["ghost-ds"]["owner_match"] is False

    # 历史条目（无 owner）但本机有数据 → 视为本人
    assert by_id["legacy-ds"]["is_local"] is True
    assert by_id["legacy-ds"]["owner_match"] is True

    # 空 data_path 不算本机可用（否则 resolve_data_path 会把空串当数据根目录）
    assert by_id["no-path-ds"]["is_local"] is False

    # data_path 始终存在（前端要用来展示"为什么不可用"）
    assert all("data_path" in d for d in out["datasets"])


@pytest.mark.asyncio
async def test_repository_keeps_tag_stats(fake_registry):
    fake_registry[0]["tags"] = ["视觉"]
    fake_registry[1]["tags"] = ["视觉", "公开"]
    out = await dr.get_dataset_repository()
    assert out["tag_stats"]["视觉"] == 2
    assert out["tag_stats"]["公开"] == 1

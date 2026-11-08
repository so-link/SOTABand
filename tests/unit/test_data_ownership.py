"""数据接口归属校验（P1-3）测试。

数据集是**私有资源**：他人条目一律按「不存在」（404）处理，避免存在性泄露。
历史无 owner 条目沿用「本机有数据视为本人」的迁移启发式。
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, user_id: str = "u1"):
        self.user_id = user_id


class _FakeRegistry:
    def __init__(self, entries):
        self._entries = entries

    async def get(self, dataset_id):
        return self._entries.get(dataset_id)

    def is_present_locally(self, entry):
        return bool(entry.get("_local"))


@pytest.mark.asyncio
async def test_require_owner_dataset_rules(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import data_routes as dr

    monkeypatch.setattr(
        dr,
        "registry",
        _FakeRegistry(
            {
                "mine": {"id": "mine", "owner": "u1"},
                "others": {"id": "others", "owner": "u9"},
                "legacy-local": {"id": "legacy-local", "_local": True},
                "legacy-ghost": {"id": "legacy-ghost", "_local": False},
            }
        ),
    )

    # 自己的：通过
    assert (await dr._require_owner_dataset_async(_Ctx("u1"), "mine"))["id"] == "mine"
    # 历史无 owner 但本机有数据：视为本人
    assert (
        await dr._require_owner_dataset_async(_Ctx("u1"), "legacy-local")
    )["id"] == "legacy-local"
    # 他人的 / 幽灵 / 不存在：一律 404（不泄露存在性）
    for dataset_id in ("others", "legacy-ghost", "missing"):
        with pytest.raises(HTTPException) as exc:
            await dr._require_owner_dataset_async(_Ctx("u1"), dataset_id)
        assert exc.value.status_code == 404, dataset_id


@pytest.mark.asyncio
async def test_machine_owner_datasets_visible_and_claimable(tmp_path, monkeypatch):
    """管理员意见 1 的升级场景：8.1 之前 owner=local-xxxx 的数据集。

    升级到 UUID 身份后必须仍可见（否则老用户历史数据全部失联），且平台
    管理员可以通过 claim_local_orphans 打上 UUID 归属。
    """
    from core.resource.registry import data_registry as dr

    registry_file = tmp_path / "registry.json"
    registry_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(dr, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(dr, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dr, "DATASETS_DIR", tmp_path / "datasets")

    data_dir = tmp_path / "legacy-data"
    data_dir.mkdir()
    (data_dir / "f.csv").write_text("x", encoding="utf-8")

    registry = dr.DataRegistry()
    await registry.register(
        {"id": "legacy-ds", "name": "legacy", "data_path": str(data_dir)},
        owner="local-abcd1234",
    )

    user_uuid = "11111111-1111-1111-1111-111111111111"
    entry = await registry.get("legacy-ds")
    # 机器身份历史条目 = 未认领：本机有数据即对 UUID 用户可见/可读
    assert registry.is_unclaimed(entry) is True
    assert registry.belongs_to(entry, user_uuid) is True
    assert registry.is_visible(entry, user_uuid) is True

    # 平台管理员认领后归属变成 UUID，走精确匹配
    claimed = registry.claim_local_orphans(user_uuid)
    assert claimed >= 1
    entry = await registry.get("legacy-ds")
    assert entry["owner"] == user_uuid
    assert registry.belongs_to(entry, user_uuid) is True
    assert registry.is_unclaimed(entry) is False


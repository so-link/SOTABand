"""新旧身份体系打通的回归测试（对应管理员提问 2）。

管理员的原始疑问：
    旧：user_context → local-xxxx，被 data_routes / tool_routes / api_data 使用
    新：AuthContext.user_id → users 表 UUID，被 network / sharing 使用
    两套身份没有映射 → 多用户共用一台机器时按人不隔离。

本文件锁定修复后的语义：

1. ``resolve_owner_id()`` 已认证时返回 ``AuthContext.user_id``（UUID），
   未认证 / 无上下文时才回退机器身份（``local-xxxx``）；
2. registry 的 ``register(owner=...)`` 使用显式传入的 owner；
3. ``POST /api/data/register`` 带登录 Cookie 时，登记的数据集 owner 是**用户 UUID**，
   不是机器身份 —— 这正是「A 能看到 B 的数据集」的根因。
"""

from __future__ import annotations

import uuid

import pytest

from core.resource import paths
from core.resource.registry.data_registry import DataRegistry
from core.user_context import get_current_user_id, resolve_owner_id

REGISTER = "/api/auth/register"
ME = "/api/v1/me"
DATA_REGISTER = "/api/data/register"
DATA_LIST = "/api/data/list"
PASSWORD = "Passw0rd2026"


class _FakeCtx:
    """只实现 resolve_owner_id 需要的 user_id 属性。"""

    def __init__(self, user_id: str):
        self.user_id = user_id


# ── 1. resolve_owner_id ──────────────────────────────────────────────


def test_resolve_owner_id_prefers_auth_context() -> None:
    uid = str(uuid.uuid4())
    assert resolve_owner_id(_FakeCtx(uid)) == uid


def test_resolve_owner_id_falls_back_to_machine_identity() -> None:
    assert resolve_owner_id(None) == get_current_user_id()


def test_resolve_owner_id_ignores_blank_user_id() -> None:
    """空 user_id 不能把 owner 写成空串，必须回退机器身份。"""
    assert resolve_owner_id(_FakeCtx("")) == get_current_user_id()


# ── 2. registry.register(owner=...) ─────────────────────────────────


@pytest.fixture()
def isolated_data_registry(tmp_path, monkeypatch) -> DataRegistry:
    """把私有资源根指向 tmp，避免污染真实 resources-private。"""
    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "resources-private")
    return DataRegistry()


@pytest.mark.asyncio
async def test_registry_register_uses_explicit_owner(isolated_data_registry: DataRegistry) -> None:
    uid = str(uuid.uuid4())
    ds_id = await isolated_data_registry.register(
        {"id": f"ds-{uuid.uuid4().hex[:8]}", "name": "owner-test"}, owner=uid
    )
    entry = await isolated_data_registry.get(ds_id)
    assert entry is not None
    assert entry["owner"] == uid


@pytest.mark.asyncio
async def test_registry_register_without_owner_falls_back_to_machine(
    isolated_data_registry: DataRegistry,
) -> None:
    ds_id = await isolated_data_registry.register(
        {"id": f"ds-{uuid.uuid4().hex[:8]}", "name": "fallback"}
    )
    entry = await isolated_data_registry.get(ds_id)
    assert entry is not None
    assert entry["owner"] == get_current_user_id()


# ── 3. 路由级：登录后登记的数据集 owner = 用户 UUID（端到端）───────


@pytest.mark.asyncio
async def test_authenticated_dataset_owner_is_user_uuid(api_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "resources-private")

    resp = await api_client.post(
        REGISTER,
        json={
            "email": f"owner-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
            "display_name": "Owner",
        },
    )
    assert resp.status_code == 201, resp.text
    user_id = (await api_client.get(ME)).json()["user_id"]
    assert uuid.UUID(user_id)  # 是合法 UUID

    ds_name = f"owner-ds-{uuid.uuid4().hex[:6]}"
    resp = await api_client.post(
        DATA_REGISTER,
        json={"specMd": "# owner test\n## 1. 概述\n测试", "datasetName": ds_name},
    )
    assert resp.status_code == 200, resp.text
    entry = resp.json()["entry"]
    assert entry["owner"] == user_id, f"owner 应为用户 UUID，实际 {entry['owner']!r}"
    assert not str(entry["owner"]).startswith("local-"), "不得回退成机器身份"

    # 列表按 UUID 过滤，能看到自己刚注册的
    listing = (await api_client.get(DATA_LIST)).json()
    ids = [d["id"] for d in listing["datasets"]]
    assert entry["id"] in ids


@pytest.mark.asyncio
async def test_other_user_does_not_see_my_dataset(api_client, tmp_path, monkeypatch) -> None:
    """管理员场景：多用户共用一台机器，A 看不到 B 的数据集。"""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "resources-private")

    # A 注册并登记数据集
    resp = await api_client.post(
        REGISTER,
        json={"email": f"a-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    ds_name = f"private-ds-{uuid.uuid4().hex[:6]}"
    resp = await api_client.post(
        DATA_REGISTER,
        json={"specMd": "# a\n## 1. 概述\na", "datasetName": ds_name},
    )
    assert resp.status_code == 200, resp.text
    ds_id = resp.json()["entry"]["id"]

    # B 用独立 Cookie 罐登录，不应看到 A 的数据集
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client_b:
        resp = await client_b.post(
            REGISTER,
            json={"email": f"b-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        listing = (await client_b.get(DATA_LIST)).json()
        ids = [d["id"] for d in listing["datasets"]]
        assert ds_id not in ids, "B 不应看到 A 的数据集（按人隔离失效）"

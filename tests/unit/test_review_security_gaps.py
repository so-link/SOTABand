"""独立评审发现的缺口回归测试（v2 复审补）。

覆盖：
- 写守卫的路径解析（register / refine-spec-node / id 与集合名冲突）；
- 文件接口的**归属维度**（上传根、数据集根按 owner 过滤、平台元数据黑名单）；
- register/upsert 不得接管他人资源（data/model/tool/agent）；
- 聊天停止标志按 (owner, session_id) 隔离；
- migrate import-upload 的 owner 变量（reviewer 发现的 NameError）；
- Agent Web 服务无令牌 fail-closed。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

# ══════════════════════════════════════════════════════════════════
# 1) 写守卫路径解析（L1 + H2）
# ══════════════════════════════════════════════════════════════════


def _req(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def test_resource_id_detects_refine_spec_node():
    from core.auth.ownership import _TOOL_WRITE_ACTIONS, _resource_id

    assert "refine-spec-node" in _TOOL_WRITE_ACTIONS
    assert _resource_id(
        _req("POST", "/api/tool/victim/refine-spec-node"), prefix="/api/tool/", actions=_TOOL_WRITE_ACTIONS
    ) == "victim"


def test_resource_id_not_confused_by_collection_names():
    """工具 id 恰好叫 test/list/register 时，写守卫不能跳过（L1）。"""
    from core.auth.ownership import _TOOL_WRITE_ACTIONS, _resource_id

    assert _resource_id(
        _req("POST", "/api/tool/test/save-code"), prefix="/api/tool/", actions=_TOOL_WRITE_ACTIONS
    ) == "test"
    assert _resource_id(
        _req("DELETE", "/api/tool/test"), prefix="/api/tool/", actions=_TOOL_WRITE_ACTIONS
    ) == "test"


def test_resource_id_skips_workspace_and_read_actions():
    from core.auth.ownership import _TOOL_WRITE_ACTIONS, _resource_id

    assert _resource_id(
        _req("DELETE", "/api/tool/workspace/abc"), prefix="/api/tool/", actions=_TOOL_WRITE_ACTIONS
    ) is None
    assert _resource_id(
        _req("POST", "/api/tool/victim/execute"), prefix="/api/tool/", actions=_TOOL_WRITE_ACTIONS
    ) is None


def test_resource_id_guards_bare_workspace_delete():
    """``DELETE /api/tool/workspace`` 会被 ``DELETE /{tool_id}`` 匹配成
    "删除 id 恰好为 workspace 的工具"。

    第一段是 ``workspace`` 并不代表这是"我的工作区"操作 —— 必须要求**有第二段**。
    否则写守卫直接跳过，普通成员能删掉别人的同名工具（独立终审实测 200）。
    """
    from core.auth.ownership import _TOOL_WRITE_ACTIONS, _resource_id

    assert _resource_id(
        _req("DELETE", "/api/tool/workspace"), prefix="/api/tool/", actions=_TOOL_WRITE_ACTIONS
    ) == "workspace"


@pytest.mark.asyncio
async def test_auto_debug_form_tool_id_requires_write_access(monkeypatch):
    """``POST /api/tool/auto-debug`` 的 tool_id 在 Form 里，路由级守卫取不到。

    必须在入口显式校验：否则普通成员能对别人的工具跑自动调试，调试循环里的
    ``install_deps_stream`` 会把 pip 包装进对方的 ``.venv``（实测到达 builder）。
    """
    from fastapi import HTTPException

    from app.api.routes import tool_routes

    class _Registry:
        def __init__(self, entry):
            self.entry = entry

        async def get(self, tool_id):
            return self.entry

    monkeypatch.setattr(tool_routes, "registry", _Registry({"id": "victim", "owner": "user-b"}))
    with pytest.raises(HTTPException) as exc_info:
        await tool_routes._assert_form_tool_write_access(_Ctx("user-a"), "victim")
    assert exc_info.value.status_code == 404

    # 草稿工具（尚未 register）放行：新建流程本来就还没有注册
    monkeypatch.setattr(tool_routes, "registry", _Registry(None))
    await tool_routes._assert_form_tool_write_access(_Ctx("user-a"), "draft")

    # owner 本人放行
    monkeypatch.setattr(tool_routes, "registry", _Registry({"id": "mine", "owner": "user-a"}))
    await tool_routes._assert_form_tool_write_access(_Ctx("user-a"), "mine")


@pytest.mark.asyncio
async def test_auto_debug_endpoint_404s_on_others_tool(api_client, monkeypatch):
    """端到端确认：普通成员 POST /api/tool/auto-debug 带他人 tool_id → 404。

    必须在**进入调试循环之前**拒绝（否则 pip 会装进他人工具的 .venv）。
    """
    import uuid as _uuid

    from app.api.deps import get_auth_context
    from app.api.routes import tool_routes
    from app.main import app
    from core.auth.context import AuthContext

    member = AuthContext(
        user_id=str(_uuid.uuid4()),
        session_id=str(_uuid.uuid4()),
        tenant_id=str(_uuid.uuid4()),
        tenant_role="member",
        request_id="auto-debug-guard",
    )
    app.dependency_overrides[get_auth_context] = lambda: member

    class _Registry:
        async def get(self, tool_id):
            return {"id": tool_id, "owner": str(_uuid.uuid4())}

    monkeypatch.setattr(tool_routes, "registry", _Registry())

    resp = await api_client.post(
        "/api/tool/auto-debug",
        data={"tool_id": "victim", "spec_md": "", "code": "print(1)"},
        files={"files": ("x.txt", b"x")},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_can_edit_uses_same_rule_as_write_guard(monkeypatch):
    """列表里的 ``can_edit`` 必须与写守卫同口径，否则租户 admin 在 UI 变只读。"""
    from core.auth.ownership import can_write_resource

    from app.api.routes import tool_routes

    tools = [
        {"id": "others", "name": "others", "owner": "user-b"},
        {"id": "mine", "name": "mine", "owner": "user-a"},
        {"id": "builtin", "name": "builtin"},
    ]

    async def _list_all():
        return [dict(t) for t in tools]

    monkeypatch.setattr(tool_routes.registry, "list_all", _list_all)

    member = _Ctx("user-a", role="member")
    listed = (await tool_routes.list_tools(ctx=member))["tools"]
    for entry in listed:
        assert entry["can_edit"] is can_write_resource(member, entry), entry
    assert [t["can_edit"] for t in listed] == [False, True, False]

    admin = _Ctx("user-a", role="admin")
    listed = (await tool_routes.list_tools(ctx=admin))["tools"]
    for entry in listed:
        assert entry["can_edit"] is can_write_resource(admin, entry), entry
    assert [t["can_edit"] for t in listed] == [True, True, True]


def test_agent_lifecycle_actions_are_guarded():
    from core.auth.ownership import _AGENT_WRITE_ACTIONS

    for action in ("start", "stop", "restart"):
        assert action in _AGENT_WRITE_ACTIONS


# ══════════════════════════════════════════════════════════════════
# 2) 文件归属边界（C1 / H4）
# ══════════════════════════════════════════════════════════════════


def test_upload_namespace_rejects_other_users(monkeypatch, tmp_path):
    import core.file_guard as guard
    from core.file_guard import PathAccessError, resolve_checked_path, upload_dir_for

    monkeypatch.setattr(guard, "UPLOAD_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(guard, "allowed_read_roots", lambda owner_id=None: [tmp_path / "uploads"])

    other_file = upload_dir_for("user-b") / "a.txt"
    other_file.parent.mkdir(parents=True)
    other_file.write_text("x", encoding="utf-8")

    # 自己上传目录可读
    own = upload_dir_for("user-a") / "a.txt"
    own.parent.mkdir(parents=True)
    own.write_text("x", encoding="utf-8")
    resolve_checked_path(str(own), owner_id="user-a")

    # 他人上传目录 → 拒绝
    with pytest.raises(PathAccessError):
        resolve_checked_path(str(other_file), owner_id="user-a")


def test_dataset_roots_are_owner_filtered(monkeypatch, tmp_path):
    import core.file_guard as guard

    mine = tmp_path / "mine"
    other = tmp_path / "other"
    for d in (mine, other):
        d.mkdir()
        (d / "f.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        guard,
        "_dataset_entries",
        lambda: [
            {"id": "a", "owner": "user-a", "data_path": str(mine)},
            {"id": "b", "owner": "user-b", "data_path": str(other)},
        ],
    )
    roots = guard.dataset_roots("user-a")
    assert mine.resolve() in roots
    assert other.resolve() not in roots
    assert guard.dataset_roots("user-a", own_only=True) == [mine.resolve()]


def test_platform_metadata_is_sensitive(tmp_path):
    from core.file_guard import is_sensitive_path

    assert is_sensitive_path(tmp_path / "data" / "registry.json")
    assert is_sensitive_path(tmp_path / "storage" / "workspace_tools.json")
    assert is_sensitive_path(tmp_path / "data" / "exports" / "bundle.zip")
    assert not is_sensitive_path(tmp_path / "output" / "result.csv")


# ══════════════════════════════════════════════════════════════════
# 3) register/upsert 不得接管他人资源（C2）
# ══════════════════════════════════════════════════════════════════


class _Ctx:
    def __init__(self, user_id: str = "u1", role: str = "member"):
        self.user_id = user_id
        self.tenant_role = role
        self.tenant_id = "11111111-1111-1111-1111-111111111111"
        self.request_id = "r1"


@pytest.mark.asyncio
async def test_data_list_include_others_requires_admin(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import data_routes as dr

    class _Reg:
        async def list_all(self, owner=None):
            return []

    monkeypatch.setattr(dr, "registry", _Reg())
    with pytest.raises(HTTPException) as exc:
        await dr.list_datasets(available_only=False, include_others=True, ctx=_Ctx("u1"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_data_registry_upsert_preserves_owner(tmp_path, monkeypatch):
    """register 同 id 只更新内容，不得把 owner 改成后来者。"""
    from core.resource.registry import data_registry as dr

    registry_file = tmp_path / "registry.json"
    registry_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(dr, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(dr, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dr, "DATASETS_DIR", tmp_path / "datasets")

    registry = dr.DataRegistry()
    await registry.register({"id": "ds-1", "name": "first"}, owner="owner-a")
    await registry.register({"id": "ds-1", "name": "second"}, owner="owner-b")
    entry = await registry.get("ds-1")
    assert entry["owner"] == "owner-a"
    assert entry["name"] == "second"


@pytest.mark.asyncio
async def test_model_registry_upsert_preserves_owner(tmp_path, monkeypatch):
    from core.resource.registry import model_registry as mr

    registry_file = tmp_path / "models.json"
    registry_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mr, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(mr, "RUNTIME_MODEL_DIR", tmp_path)
    monkeypatch.setattr(mr.ModelRegistry, "_get_def_dir", lambda self: tmp_path)

    registry = mr.ModelRegistry()
    await registry.register({"id": "m-1", "name": "m"}, owner="owner-a")
    await registry.register({"id": "m-1", "name": "m2"}, owner="owner-b")
    entry = await registry.get("m-1")
    assert entry["owner"] == "owner-a"


# ══════════════════════════════════════════════════════════════════
# 4) chat 停止标志按用户隔离（H6）
# ══════════════════════════════════════════════════════════════════


def test_chat_stop_flags_are_per_user():
    from app.api.routes import chat_routes as cr

    cr._chat_stop_flags.clear()
    cr._chat_owners.clear()
    try:
        cr._set_chat_running("default", "user-a")
        cr._set_chat_running("default", "user-b")
        assert cr.is_chat_running("default", "user-a") is True
        assert cr.is_chat_running("default", "user-b") is True
        # A 停止只影响自己
        cr._chat_stop_flags[cr._chat_key("default", "user-a")] = False
        assert cr.is_chat_running("default", "user-a") is False
        assert cr.is_chat_running("default", "user-b") is True
        cr._set_chat_stopped("default", "user-a")
        assert cr._chat_owner("default", "user-a") is None
        assert cr._chat_owner("default", "user-b") == "user-b"
    finally:
        cr._chat_stop_flags.clear()
        cr._chat_owners.clear()


# ══════════════════════════════════════════════════════════════════
# 5) migrate import-upload 回归（H5）与 Web 令牌 fail-closed（L2）
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_import_tool_upload_sets_owner(monkeypatch):
    """reviewer 发现的 NameError 回归：owner 变量必须在函数内定义并使用。"""
    from app.api.routes import migrate_routes

    def _fake_import(bundle_path=None, bundle_bytes=None, overwrite=False, **kwargs):
        return {"tool_id": "demo-tool", "name": "demo", "tags": [], "files": []}

    monkeypatch.setattr(migrate_routes.tool_bundle, "import_tool", _fake_import)
    monkeypatch.setattr(migrate_routes, "_ensure_in_workspace", lambda *a, **k: None)
    monkeypatch.setattr(migrate_routes, "_deps_report", lambda tid: {})

    class _Upload:
        async def read(self):
            return b"bundle-bytes"

    ctx = _Ctx("importer-uuid", role="owner")
    result = await migrate_routes.import_tool_upload(
        file=_Upload(), overwrite=False, ctx=ctx
    )
    assert result["tool_id"] == "demo-tool"


@pytest.mark.asyncio
async def test_web_service_token_fail_closed_when_missing():
    """没有引擎令牌的 WebServiceHost 不得放行任何 API 请求（L2）。"""
    import asyncio

    import httpx

    from core.agent.web_server import WebServiceHost

    class _Spec:
        apis = [{"name": "greet", "method": "POST", "path": "/greet"}]

    class _Agent:
        agent_id = "a"
        spec = _Spec()

        async def execute(self, ctx, **kwargs):  # pragma: no cover - 不应被调用
            yield {"event": "result", "data": {"response": {}}}

    host = WebServiceHost(_Agent(), port=0, token="")
    port = await host.start()
    task = asyncio.ensure_future(host.serve_forever())
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"http://127.0.0.1:{port}/greet", json={})
            assert r.status_code == 401
            # 健康检查仍免令牌
            r = await client.get(f"http://127.0.0.1:{port}/_health")
            assert r.status_code == 200
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await host.stop()

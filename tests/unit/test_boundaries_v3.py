"""v3 终审回归：数据集登记/删除边界、迁移包盘符 zip-slip、model/migrate 路径归属。

覆盖独立评审实测复现的四条：

1. **C 数据集登记不看目标归属** —— 可把受害者上传目录 / ``runtime/tasks`` /
   ``PROJECT_ROOT/data`` 登记成数据集，再 ``DELETE /api/data/{id}`` 触发 rmtree；
2. **H data 路由入口漏传 owner_id** —— scan-directory / thumbnail 可越权访问；
3. **C 迁移包 zip-slip（Windows 盘符）** —— ``E:/...`` 成员会重置 pathlib 根；
4. **M model/migrate 路径归属** —— ``model_path`` / ``bundle_path`` 可指向他人
   上传目录。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把 file_guard 的所有根目录重定向到临时目录（不碰真实 runtime）。"""
    import core.file_guard as guard

    project = _mkdir(tmp_path / "project")
    runtime = _mkdir(tmp_path / "runtime")
    private = _mkdir(tmp_path / "private")
    monkeypatch.setattr(guard, "PROJECT_ROOT", project)
    monkeypatch.setattr(guard, "RUNTIME_ROOT", runtime)
    monkeypatch.setattr(guard, "UPLOAD_ROOT", runtime / "uploads")
    monkeypatch.setattr(guard, "PRIVATE_ROOT", private)
    monkeypatch.setattr(guard, "_extra_roots", list)
    monkeypatch.setattr(guard, "_dataset_entries", list)
    return SimpleNamespace(
        guard=guard, project=project, runtime=runtime, private=private
    )


def _ctx(user_id: str, role: str = "member") -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, tenant_role=role)


# ══════════════════════════════════════════════════════════════════
# ① 数据集登记：只允许自己的安全根
# ══════════════════════════════════════════════════════════════════


def test_dataset_register_rejects_other_users_upload_dir(sandbox):
    """攻击面：登记受害者上传目录 → DELETE 删除受害者整个上传目录。"""
    guard = sandbox.guard
    victim_dir = _mkdir(sandbox.runtime / "uploads" / "victim" / "2026-11-05")
    with pytest.raises(guard.PathAccessError):
        guard.validate_dataset_path(str(victim_dir), owner_id="attacker")

    # 自己的上传子目录可以登记
    own_dir = _mkdir(sandbox.runtime / "uploads" / "me" / "2026-11-05")
    assert guard.validate_dataset_path(str(own_dir), owner_id="me") == own_dir.resolve()

    # 没有 owner 的调用方（内部/迁移）不能借上传目录蒙混过关
    with pytest.raises(guard.PathAccessError):
        guard.validate_dataset_path(str(own_dir))


def test_dataset_register_rejects_platform_roots(sandbox):
    guard = sandbox.guard
    tasks_dir = _mkdir(sandbox.runtime / "tasks")
    (tasks_dir / "registry.json").write_text("[]", encoding="utf-8")
    data_root = _mkdir(sandbox.project / "data")
    private_data_root = _mkdir(sandbox.private / "data")

    for forbidden in (
        sandbox.project,
        sandbox.runtime,
        sandbox.private,
        sandbox.runtime / "uploads",
        data_root,
        private_data_root,
    ):
        with pytest.raises(guard.PathAccessError):
            guard.validate_dataset_path(str(forbidden), owner_id="me")

    # runtime/tasks（registry.json 的父目录）也不在允许根内
    with pytest.raises(guard.PathAccessError):
        guard.validate_dataset_path(str(tasks_dir), owner_id="me")

    # 平台数据根的子目录是允许的
    ok = _mkdir(data_root / "my-dataset")
    assert guard.validate_dataset_path(str(ok), owner_id="me") == ok.resolve()
    ok2 = _mkdir(sandbox.private / "data" / "datasets" / "ds-1")
    assert guard.validate_dataset_path(str(ok2), owner_id="me") == ok2.resolve()
    ok3 = _mkdir(sandbox.runtime / "datasets" / "ds-2")
    assert guard.validate_dataset_path(str(ok3), owner_id="me") == ok3.resolve()


def test_dataset_register_rejects_overlap_with_other_user(sandbox, monkeypatch):
    guard = sandbox.guard
    other = _mkdir(sandbox.private / "data" / "datasets" / "other")
    monkeypatch.setattr(
        guard,
        "_dataset_entries",
        lambda: [{"id": "other", "owner": "victim", "data_path": str(other)}],
    )
    with pytest.raises(guard.PathAccessError):
        guard.validate_dataset_path(str(other), owner_id="attacker")
    with pytest.raises(guard.PathAccessError):
        guard.validate_dataset_path(str(other / "sub"), owner_id="attacker")


@pytest.mark.asyncio
async def test_register_route_rejects_victim_upload_dir(sandbox):
    """路由层：sourceFiles 指向受害者上传文件时不能登记成功。"""
    from app.api.routes import data_routes as dr

    victim_dir = _mkdir(sandbox.runtime / "uploads" / "victim" / "run")
    victim_file = victim_dir / "x.bin"
    victim_file.write_bytes(b"secret")

    req = dr.RegisterDatasetRequest(
        specMd="# dataset", sourceFiles=[str(victim_file)]
    )
    with pytest.raises(dr.HTTPException) as exc:
        await dr.register_dataset(req, ctx=_ctx("attacker"))
    assert exc.value.status_code == 400


# ══════════════════════════════════════════════════════════════════
# ① 数据集删除：删除面更窄，不安全 → 409
# ══════════════════════════════════════════════════════════════════


def test_dataset_deletion_rejects_unsafe_paths(sandbox, monkeypatch):
    guard = sandbox.guard
    own_dir = _mkdir(sandbox.runtime / "uploads" / "me" / "run")
    victim_dir = _mkdir(sandbox.runtime / "uploads" / "victim" / "run")

    # 自己的上传子目录可以删
    assert guard.validate_dataset_deletion(own_dir, owner_id="me") == own_dir.resolve()
    # 他人上传目录 / 上传根 / 平台数据根本身 → 拒绝
    for unsafe in (
        victim_dir,
        sandbox.runtime / "uploads",
        sandbox.project / "data",
        sandbox.private / "data",
        sandbox.project,
    ):
        with pytest.raises(guard.PathAccessError):
            guard.validate_dataset_deletion(unsafe, owner_id="me")

    # 敏感目录（storage）即使落在允许根内也拒绝
    sensitive = _mkdir(sandbox.private / "data" / "storage")
    with pytest.raises(guard.PathAccessError):
        guard.validate_dataset_deletion(sensitive, owner_id="me")

    # 与他人已注册数据集重叠 → 拒绝
    other_ds = _mkdir(sandbox.private / "data" / "datasets" / "victim-ds")
    monkeypatch.setattr(
        guard,
        "_dataset_entries",
        lambda: [{"id": "victim-ds", "owner": "victim", "data_path": str(other_ds)}],
    )
    with pytest.raises(guard.PathAccessError):
        guard.validate_dataset_deletion(other_ds, owner_id="attacker")
    # 自己的数据集条目可以删
    monkeypatch.setattr(
        guard,
        "_dataset_entries",
        lambda: [{"id": "mine", "owner": "me", "data_path": str(own_dir)}],
    )
    assert guard.validate_dataset_deletion(own_dir, owner_id="me") == own_dir.resolve()


@pytest.mark.asyncio
async def test_delete_route_returns_409_and_keeps_victim_dir(sandbox, monkeypatch):
    """攻击链终态：attacker 的条目指向受害者目录，DELETE 必须 409 且不删文件。"""
    from app.api.routes import data_routes as dr

    victim_dir = _mkdir(sandbox.runtime / "uploads" / "victim" / "run")
    (victim_dir / "x.bin").write_bytes(b"secret")

    class _Reg:
        async def get(self, dataset_id):
            return {
                "id": dataset_id,
                "owner": "attacker",  # 攻击者自己的条目，归属校验会放行
                "data_path": str(victim_dir),  # 但指向受害者上传目录
            }

        def resolve_data_path(self, data_path):
            return Path(data_path)

    monkeypatch.setattr(dr, "registry", _Reg())

    with pytest.raises(dr.HTTPException) as exc:
        await dr.delete_dataset("ds-evil", ctx=_ctx("attacker"), session=None)
    assert exc.value.status_code == 409
    assert (victim_dir / "x.bin").exists(), "不安全的删除必须原样保留文件"


# ══════════════════════════════════════════════════════════════════
# ③ 迁移包 zip-slip：Windows 盘符成员
# ══════════════════════════════════════════════════════════════════


def _zip_bytes(manifest: dict, members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("registry_entry.json", json.dumps({"id": "demo"}))
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _tool_bundle(members: dict[str, str], tool_id: str = "demo-tool") -> bytes:
    from core.migration import tool_bundle

    manifest = {
        "format": tool_bundle.BUNDLE_FORMAT,
        "format_version": tool_bundle.BUNDLE_VERSION,
        "exported_by": "victim",
        "tool": {"id": tool_id, "name": tool_id},
    }
    payload = {
        f"definitions/{tool_id}.md": "# demo\n",
        **members,
    }
    return _zip_bytes(manifest, payload)


def _agent_bundle(members: dict[str, str], agent_id: str = "demo-agent") -> bytes:
    from core.migration import agent_bundle

    manifest = {
        "format": agent_bundle.BUNDLE_FORMAT,
        "format_version": agent_bundle.BUNDLE_VERSION,
        "exported_by": "victim",
        "agent": {"id": agent_id, "name": agent_id},
    }
    payload = {
        f"definitions/{agent_id}.md": "# demo\n",
        **members,
    }
    return _zip_bytes(manifest, payload)


def test_tool_bundle_rejects_windows_drive_slip(tmp_path, monkeypatch):
    from core.migration import tool_bundle
    from core.resource import paths

    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "private")
    payload = _tool_bundle(
        {
            "implementations/demo-tool/E:/Learn/SOTABand/core/file_guard.py": "pwn",
        }
    )
    with pytest.raises(ValueError):
        tool_bundle.import_tool(bundle_bytes=payload, owner_id="attacker")

    # 正常成员仍可导入（回归：不要为了安全把正常包也打死）
    good = _tool_bundle(
        {
            "implementations/demo-tool/tool.py": (
                "def execute(**kwargs):\n    return {}\n"
            ),
        }
    )
    result = tool_bundle.import_tool(bundle_bytes=good, owner_id="me")
    assert result["tool_id"] == "demo-tool"
    written = (
        tmp_path
        / "private"
        / "tools"
        / "implementations"
        / "demo-tool"
        / "tool.py"
    )
    assert written.is_file()


def test_tool_bundle_rejects_posix_absolute_slip(tmp_path, monkeypatch):
    from core.migration import tool_bundle
    from core.resource import paths

    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "private")
    # 反斜杠 / 前导斜杠 / `..` 的旧拦截仍必须生效
    for member in (
        "implementations/demo-tool/..\\..\\evil.py",
        "implementations/demo-tool//etc/passwd",
        "implementations/demo-tool/sub/../../evil.py",
    ):
        with pytest.raises(ValueError):
            tool_bundle._safe_member_path(member, "demo-tool")


def test_agent_bundle_rejects_windows_drive_slip(tmp_path, monkeypatch):
    from core.migration import agent_bundle
    from core.resource import paths

    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "private")
    payload = _agent_bundle(
        {
            "implementations/demo-agent/E:/Learn/SOTABand/core/file_guard.py": "pwn",
        }
    )
    with pytest.raises(ValueError):
        agent_bundle.import_agent(bundle_bytes=payload, owner_id="attacker")


# ══════════════════════════════════════════════════════════════════
# ④ model_path / migrate bundle_path 归属
# ══════════════════════════════════════════════════════════════════


def test_model_path_rejects_other_users_upload_dir(sandbox):
    from app.api.routes import model_routes

    victim_file = _mkdir(sandbox.runtime / "uploads" / "victim") / "model.pt"
    victim_file.write_bytes(b"weights")
    with pytest.raises(model_routes.HTTPException) as exc:
        model_routes._validate_model_path(str(victim_file), owner_id="attacker")
    assert exc.value.status_code == 400

    own_file = _mkdir(sandbox.runtime / "uploads" / "me") / "model.pt"
    own_file.write_bytes(b"weights")
    assert Path(
        model_routes._validate_model_path(str(own_file), owner_id="me")
    ) == own_file.resolve()

    # 敏感路径（如 .env）一律拒绝
    env_file = sandbox.project / ".env"
    env_file.write_text("LLM_API_KEY=sk-x", encoding="utf-8")
    with pytest.raises(model_routes.HTTPException):
        model_routes._validate_model_path(str(env_file), owner_id="me")


def _write_bundle(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.mark.asyncio
async def test_migrate_tool_bundle_path_rejects_other_upload_dir(sandbox, monkeypatch):
    from app.api.routes import migrate_routes

    victim_bundle = _write_bundle(
        sandbox.runtime / "uploads" / "victim" / "tool.zip",
        _tool_bundle({"implementations/demo-tool/tool.py": "x"}),
    )

    def _no_import(*args, **kwargs):  # pragma: no cover - 不应走到导入
        raise AssertionError("不安全的 bundle_path 必须在导入前被拒绝")

    monkeypatch.setattr(migrate_routes.tool_bundle, "import_tool", _no_import)
    req = migrate_routes.ImportToolRequest(bundle_path=str(victim_bundle))
    with pytest.raises(migrate_routes.HTTPException) as exc:
        await migrate_routes.import_tool(req=req, ctx=_ctx("attacker"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_migrate_tool_bundle_path_enforces_visibility(sandbox, monkeypatch):
    """自己的目录里放着别人导出的包：与 bundle_name 分支同口径 → 404。"""
    from app.api.routes import migrate_routes

    other_bundle = _write_bundle(
        sandbox.runtime / "uploads" / "me" / "tool.zip",
        _tool_bundle({"implementations/demo-tool/tool.py": "x"}),
    )  # manifest.exported_by = "victim"

    def _no_import(*args, **kwargs):  # pragma: no cover
        raise AssertionError("不可见的包不能被导入")

    monkeypatch.setattr(migrate_routes.tool_bundle, "import_tool", _no_import)
    req = migrate_routes.ImportToolRequest(bundle_path=str(other_bundle))
    with pytest.raises(migrate_routes.HTTPException) as exc:
        await migrate_routes.import_tool(req=req, ctx=_ctx("me"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_migrate_agent_bundle_path_rejects_other_upload_dir(sandbox, monkeypatch):
    from app.api.routes import migrate_routes

    victim_bundle = _write_bundle(
        sandbox.runtime / "uploads" / "victim" / "agent.zip",
        _agent_bundle({"implementations/demo-agent/agent.py": "x"}),
    )
    monkeypatch.setattr(
        migrate_routes.agent_bundle,
        "import_agent",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not import")),
    )
    req = migrate_routes.ImportAgentRequest(bundle_path=str(victim_bundle))
    with pytest.raises(migrate_routes.HTTPException) as exc:
        await migrate_routes.import_agent(req=req, ctx=_ctx("attacker"))
    assert exc.value.status_code == 400


# ══════════════════════════════════════════════════════════════════
# ② scan-directory / thumbnail 必须带 owner
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scan_directory_uses_caller_owner(sandbox):
    from app.api.routes import data_routes as dr
    from app.api.schemas.data_schemas import ScanDirectoryRequest

    victim_dir = _mkdir(sandbox.runtime / "uploads" / "victim" / "run")
    (victim_dir / "x.bin").write_bytes(b"secret")

    with pytest.raises(dr.HTTPException) as exc:
        await dr.scan_directory(
            ScanDirectoryRequest(path=str(victim_dir)), ctx=_ctx("attacker")
        )
    assert exc.value.status_code == 403

    own_dir = _mkdir(sandbox.runtime / "uploads" / "me" / "run")
    (own_dir / "ok.bin").write_bytes(b"ok")
    out = await dr.scan_directory(
        ScanDirectoryRequest(path=str(own_dir)), ctx=_ctx("me")
    )
    assert out["file_count"] == 1

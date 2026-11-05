"""文件访问边界（D3 / P1-2 / P1-9 / P1-10）测试。

一台服务器多人共用的部署下，路径参数必须收口：
- 读/删只允许明确允许的根；
- 敏感目录（storage/.git/.env 等）永远拒绝；
- 上传文件名净化（防 CWE-22 路径穿越）；
- 数据集注册路径不能是资源根本身、不能与他人数据集重叠。
"""

from __future__ import annotations

import pytest

import core.file_guard as guard
from core.file_guard import (
    PathAccessError,
    resolve_checked_path,
    validate_dataset_path,
)


@pytest.fixture()
def allowed(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setattr(guard, "allowed_read_roots", lambda owner_id=None: [root.resolve()])
    monkeypatch.setattr(guard, "allowed_write_roots", lambda owner_id=None: [root.resolve()])
    return root


def test_inside_allowed_root_ok(allowed):
    target = allowed / "data" / "a.txt"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    resolved = resolve_checked_path(str(target), must_exist=True)
    assert resolved == target.resolve()


def test_outside_allowed_root_rejected(allowed, tmp_path):
    outside = tmp_path / "other" / "secret.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(PathAccessError):
        resolve_checked_path(str(outside))
    # 允许根内不存在 403；越界路径也不能借 .. 绕回来
    with pytest.raises(PathAccessError):
        resolve_checked_path(str(allowed / ".." / "other" / "secret.txt"))


def test_sensitive_path_rejected(allowed, monkeypatch):
    """即使落在允许根内，storage/.env 也必须拒绝。"""
    storage_dir = allowed / "storage"
    storage_dir.mkdir()
    env_file = allowed / ".env"
    env_file.write_text("LLM_API_KEY=sk-x", encoding="utf-8")
    with pytest.raises(PathAccessError):
        resolve_checked_path(str(storage_dir / "db.sqlite"))
    with pytest.raises(PathAccessError):
        resolve_checked_path(str(env_file))


def test_empty_or_nul_path_rejected(allowed):
    with pytest.raises(PathAccessError):
        resolve_checked_path("")
    with pytest.raises(PathAccessError):
        resolve_checked_path("a\x00b")


def test_validate_dataset_path_rejects_resource_roots(tmp_path, monkeypatch):
    import core.resource.paths as paths
    import core.runtime.paths as runtime_paths

    allowed = tmp_path / "datasets"
    allowed.mkdir()
    monkeypatch.setattr(guard, "_extra_roots", lambda: [allowed.resolve()])
    monkeypatch.setattr(guard, "_dataset_entries", lambda: [])
    monkeypatch.setattr(guard, "PROJECT_ROOT", paths.PROJECT_ROOT)
    monkeypatch.setattr(guard, "RUNTIME_ROOT", runtime_paths.RUNTIME_ROOT)
    monkeypatch.setattr(guard, "PRIVATE_ROOT", paths.PRIVATE_ROOT)

    ok = validate_dataset_path(str(allowed))
    assert ok == allowed.resolve()

    for forbidden in (guard.PROJECT_ROOT, guard.RUNTIME_ROOT, guard.PRIVATE_ROOT):
        with pytest.raises(PathAccessError):
            validate_dataset_path(str(forbidden))


def test_validate_dataset_path_rejects_overlap(tmp_path, monkeypatch):
    existing = tmp_path / "datasets" / "existing"
    existing.mkdir(parents=True)
    monkeypatch.setattr(guard, "_extra_roots", lambda: [existing.parent.resolve()])
    monkeypatch.setattr(
        guard,
        "_dataset_entries",
        lambda: [{"id": "existing", "owner": "u1", "data_path": str(existing)}],
    )

    with pytest.raises(PathAccessError):
        validate_dataset_path(str(existing))  # 已注册
    with pytest.raises(PathAccessError):
        validate_dataset_path(str(existing / "sub"))  # 与已注册目录重叠

    fresh = tmp_path / "datasets" / "fresh"
    fresh.mkdir()
    assert validate_dataset_path(str(fresh)) == fresh.resolve()


def test_upload_filename_sanitized():
    from app.api.routes.tool_routes import _sanitize_filename

    assert _sanitize_filename("../../evil.py") == "evil.py"
    assert _sanitize_filename("..\\..\\evil.py") == "evil.py"
    assert _sanitize_filename("ok.txt") == "ok.txt"
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _sanitize_filename("..")
    with pytest.raises(HTTPException):
        _sanitize_filename("")

"""工作区状态按用户隔离（P1-15）测试。

多用户共用一台服务器时，工具空间（``storage/workspace_tools/{user_id}.json``）
与工作区文件树（``storage/workspace_tree/{user_id}.json``）必须按用户命名空间
存储，否则 A/B 会互相增删对方的工具空间/文件树。旧全局文件仅作只读迁移来源。
"""

from __future__ import annotations

import json


def test_tool_workspace_is_per_user(tmp_path, monkeypatch):
    from app.api.routes import tool_routes

    monkeypatch.setattr(tool_routes, "_WORKSPACE_DIR", tmp_path / "workspace_tools")
    monkeypatch.setattr(tool_routes, "_LEGACY_WORKSPACE_FILE", tmp_path / "none.json")

    tool_routes._write_workspace([{"id": "t-a"}], "user-a")
    tool_routes._write_workspace([{"id": "t-b"}], "user-b")

    assert [t["id"] for t in tool_routes._read_workspace("user-a")] == ["t-a"]
    assert [t["id"] for t in tool_routes._read_workspace("user-b")] == ["t-b"]


def test_tool_workspace_legacy_migrates_to_reader(tmp_path, monkeypatch):
    """旧全局清单在首次被读取时迁移到该用户的命名空间（不再共享写）。"""
    from app.api.routes import tool_routes

    monkeypatch.setattr(tool_routes, "_WORKSPACE_DIR", tmp_path / "workspace_tools")
    legacy = tmp_path / "legacy_workspace.json"
    legacy.write_text(json.dumps([{"id": "old-tool"}]), encoding="utf-8")
    monkeypatch.setattr(tool_routes, "_LEGACY_WORKSPACE_FILE", legacy)

    assert [t["id"] for t in tool_routes._read_workspace("user-a")] == ["old-tool"]
    # 迁移后 user-b 不应看到 user-a 的清单
    assert tool_routes._read_workspace("user-b") == []


def test_file_workspace_tree_is_per_user(tmp_path, monkeypatch):
    from app.api.routes import file_routes

    monkeypatch.setattr(file_routes, "_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(file_routes, "_LEGACY_TREE_FILE", tmp_path / "legacy.json")

    file_routes._write_workspace_tree("user-a", {"name": "A"})
    file_routes._write_workspace_tree("user-b", {"name": "B"})

    assert file_routes._read_workspace_tree("user-a") == {"name": "A"}
    assert file_routes._read_workspace_tree("user-b") == {"name": "B"}


def test_workspace_tree_legacy_global_is_readonly_source(tmp_path, monkeypatch):
    """旧全局文件只作为迁移来源（不会让不同用户互相覆盖）。"""
    from app.api.routes import file_routes

    monkeypatch.setattr(file_routes, "_STORAGE_DIR", tmp_path / "storage")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"root": {"name": "legacy"}}), encoding="utf-8")
    monkeypatch.setattr(file_routes, "_LEGACY_TREE_FILE", legacy)

    assert file_routes._read_workspace_tree("user-new") == {"name": "legacy"}
    # 读取旧文件不会替该用户写个人文件
    assert not (tmp_path / "storage").exists()

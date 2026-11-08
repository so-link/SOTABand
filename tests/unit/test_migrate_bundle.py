"""迁移包归属（D4）测试。

规则（2026-11-04 拍板）：

- 导入后 registry 的 owner = **导入者 UUID**（源环境 owner 跨部署无意义）；
- manifest 记录 ``exported_by``，仅用于包可见性与溯源；
- ``overwrite=true`` 只能覆盖自己的资源（租户管理员可传 allow_overwrite 兜底）；
- 包可见性：本人导出 / 管理员看全部；旧包（无 exported_by）fail-closed。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest


def _make_tool_bundle(tool_id: str = "demo-tool") -> bytes:
    from core.migration import tool_bundle

    manifest = {
        "format": tool_bundle.BUNDLE_FORMAT,
        "format_version": tool_bundle.BUNDLE_VERSION,
        "exported_by": "exporter-uuid",
        "tool": {"id": tool_id, "name": tool_id},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("registry_entry.json", json.dumps({"id": tool_id}))
        zf.writestr(f"definitions/{tool_id}.md", "# demo\n")
        zf.writestr(
            f"implementations/{tool_id}/tool.py",
            "def execute(**kwargs):\n    return {}\n",
        )
    return buf.getvalue()


def test_import_tool_owner_is_importer(tmp_path, monkeypatch):
    from core.migration import tool_bundle
    from core.resource import paths

    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "resources-private")

    result = tool_bundle.import_tool(
        bundle_bytes=_make_tool_bundle("demo-tool"), owner_id="importer-uuid"
    )
    assert result["tool_id"] == "demo-tool"

    registry_file = tmp_path / "resources-private" / "tools" / "registry.json"
    entries = json.loads(registry_file.read_text(encoding="utf-8"))
    entry = next(e for e in entries if e["id"] == "demo-tool")
    assert entry["owner"] == "importer-uuid", "导入后的 owner 必须是导入者 UUID"


def test_import_overwrite_requires_ownership(tmp_path, monkeypatch):
    from core.migration import tool_bundle
    from core.resource import paths

    monkeypatch.setattr(paths, "PRIVATE_ROOT", tmp_path / "resources-private")
    bundle = _make_tool_bundle("demo-tool")
    tool_bundle.import_tool(bundle_bytes=bundle, owner_id="user-a")

    # 别人覆盖 → 拒绝
    with pytest.raises(ValueError):
        tool_bundle.import_tool(
            bundle_bytes=bundle, overwrite=True, owner_id="user-b", allow_overwrite=False
        )
    # 管理员兜底允许
    result = tool_bundle.import_tool(
        bundle_bytes=bundle, overwrite=True, owner_id="user-b", allow_overwrite=True
    )
    assert result["overwritten"] is True


def test_list_bundles_visibility(tmp_path):
    from core.migration import tool_bundle

    def _write_bundle(name: str, exported_by: str | None):
        manifest = {
            "format": tool_bundle.BUNDLE_FORMAT,
            "format_version": tool_bundle.BUNDLE_VERSION,
            "tool": {"id": "t"},
        }
        if exported_by is not None:
            manifest["exported_by"] = exported_by
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
        (tmp_path / name).write_bytes(buf.getvalue())

    _write_bundle(f"tool-x-1{tool_bundle.BUNDLE_SUFFIX}", "user-a")
    _write_bundle(f"tool-y-1{tool_bundle.BUNDLE_SUFFIX}", "user-b")
    _write_bundle(f"tool-z-1{tool_bundle.BUNDLE_SUFFIX}", None)  # 旧包

    mine = tool_bundle.list_bundles(tmp_path, user_id="user-a", include_all=False)
    assert [b["file_name"].split("-")[1] for b in mine] == ["x"]

    all_bundles = tool_bundle.list_bundles(tmp_path, user_id="user-a", include_all=True)
    assert len(all_bundles) == 3

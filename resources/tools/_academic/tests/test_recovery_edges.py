"""Real storage, filesystem and concurrency edges; no replaced service responses."""

import secrets
from pathlib import Path

import pytest

from resources.tools._academic.common import invoke


def pending_export(tmp_path):
    project = invoke("writing", {"mode": "create", "title": "恢复期间的版本一致性检查"})["data"]
    config = tmp_path / ("配置 " + secrets.token_hex(6) + ".json")
    response = invoke("writing", {
        "mode": "export", "project_id": project["id"], "format": "markdown", "config_file": str(config),
    })
    assert response["status"] == "failed"
    return project, config, response["data"]["recovery"]["id"]


@pytest.mark.parametrize("case", range(8))
def test_changed_project_requires_new_request(tmp_path, store, case):
    project, config, ident = pending_export(tmp_path)
    section = "操作记录-" + secrets.token_hex(4)
    content = "用户已修改文稿。本段仅记录操作，不包含研究结果。"
    edited = invoke("writing", {
        "mode": "save_section", "project_id": project["id"], "base_revision": project["revision"],
        "section": section, "content": content,
    })
    assert edited["status"] == "success"
    config.write_text("{}", encoding="utf-8")
    retried = invoke("writing", {"mode": "recover", "request": "重试 " + ident})
    assert retried["status"] == "failed", retried
    assert retried["data"]["error_code"] == "RECOVERY_STALE"
    assert store.get("writing", project["id"])["sections"][section] == content
    assert not list(store.folder("writing", project["id"]).rglob("manuscript.md"))


def test_view_is_available_while_execution_lock_is_held(tmp_path, store):
    _, _, ident = pending_export(tmp_path)
    with store.lock("recovery:" + ident):
        viewed = invoke("writing", {"mode": "recover", "request": "查看 " + ident})
    assert viewed["status"] == "success", viewed
    assert viewed["data"]["state"] == "waiting"
    assert store.get("recovery", ident)["attempts"] == 0


def test_completed_record_keeps_artifact_reference(tmp_path, store):
    _, config, ident = pending_export(tmp_path)
    config.write_text("{}", encoding="utf-8")
    retried = invoke("writing", {"mode": "recover", "request": "重试 " + ident})
    assert retried["status"] == "success", retried
    viewed = invoke("writing", {"mode": "recover", "request": "查看 " + ident})
    assert viewed["data"]["result"]["file_path"] == retried["data"]["file_path"]
    assert Path(viewed["data"]["result"]["file_path"]).is_file()
    assert viewed["data"]["can_retry"] is False


def test_revision_is_checked_at_writing_execution_boundary(tmp_path, store):
    project, _, _ = pending_export(tmp_path)
    edited = invoke("writing", {
        "mode": "save_section", "project_id": project["id"], "base_revision": project["revision"],
        "section": "操作记录", "content": "任务开始前已经修改的文稿。",
    })
    assert edited["status"] == "success"
    response = invoke("writing", {
        "mode": "export", "project_id": project["id"], "format": "markdown",
        "_recovery_project_revision": project["revision"],
    })
    assert response["data"]["error_code"] == "RECOVERY_STALE"
    assert response["data"]["recovery"]["can_retry"] is False
    assert not list(store.folder("writing", project["id"]).rglob("manuscript.md"))

"""Model-space categories and groups survive API reloads and state changes."""

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import model_routes
from core.resource.registry import model_registry


def isolated_registry(tmp_path, monkeypatch):
    registry_file = tmp_path / "models" / "registry.json"
    registry_file.parent.mkdir(parents=True)
    registry_file.write_text("[]", encoding="utf-8")
    groups_file = tmp_path / "models" / "groups.json"
    groups_file.write_text(json.dumps([
        {"id": "legacy", "name": "旧分组", "system": False},
        {"id": "other", "name": "其它模型", "system": True},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(model_registry, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(model_registry, "RUNTIME_MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(model_registry, "model_groups_path", lambda: groups_file)
    registry = model_registry.ModelRegistry()
    monkeypatch.setattr(model_routes, "registry", registry)
    return registry


def test_groups_are_scoped_persistent_and_deletable(tmp_path, monkeypatch):
    isolated_registry(tmp_path, monkeypatch)
    with TestClient(app) as client:
        legacy = client.get("/api/model/groups").json()["groups"]
        assert {(g["category"], g["name"]) for g in legacy if g["name"] == "旧分组"} == {
            ("trained", "旧分组"), ("architecture", "旧分组"),
        }

        architecture = client.post("/api/model/groups", json={"name": "研究组", "category": "architecture"})
        trained = client.post("/api/model/groups", json={"name": "研究组", "category": "trained"})
        assert architecture.status_code == trained.status_code == 200
        architecture_id = architecture.json()["group"]["id"]
        trained_id = trained.json()["group"]["id"]
        assert architecture_id != trained_id

        # A fresh registry instance reads the same empty groups after reload.
        refreshed = asyncio.run(model_registry.ModelRegistry().list_groups())
        assert {(g["category"], g["name"]) for g in refreshed if g["name"] == "研究组"} == {
            ("architecture", "研究组"), ("trained", "研究组"),
        }
        assert client.delete(f"/api/model/groups/{architecture_id}").status_code == 200
        remaining = client.get("/api/model/groups").json()["groups"]
        assert not any(g["id"] == architecture_id for g in remaining)
        assert any(g["id"] == trained_id for g in remaining)
        assert client.delete("/api/model/groups/trained:other").status_code == 409


def test_trained_category_requires_artifacts_and_group_updates_stay_scoped(tmp_path, monkeypatch):
    registry = isolated_registry(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.post("/api/model/groups", json={"name": "配置组", "category": "architecture"})
        client.post("/api/model/groups", json={"name": "结果组", "category": "trained"})
        model_id = asyncio.run(registry.register({
            "id": "test-model", "name": "test-model", "model_path": "",
            "associated_tool_id": "timeseries-classification-runner",
            "runtime_status": "configured", "group_path": "配置组",
        }))
        assert client.get(f"/api/model/{model_id}").json()["model"]["model_category"] == "architecture"
        assert client.patch(f"/api/model/{model_id}", json={"group_path": "结果组"}).status_code == 400
        architecture_group = next(g for g in client.get("/api/model/groups").json()["groups"] if g["name"] == "配置组")
        assert client.delete(f"/api/model/groups/{architecture_group['id']}").status_code == 409

        artifact = tmp_path / "run" / "best_model.pt"
        artifact.parent.mkdir()
        artifact.touch()
        client.patch(f"/api/model/{model_id}", json={
            "runtime_status": "trained", "model_path": str(artifact),
        })
        stale = client.get(f"/api/model/{model_id}").json()["model"]
        assert stale["model_category"] == "architecture"
        assert stale["artifact_available"] is False

        for name in ("scaler.pkl", "tslib_model_config.json", "threshold_report_f1.json", "manifest.json"):
            (artifact.parent / name).touch()
        trained = client.get(f"/api/model/{model_id}").json()["model"]
        assert trained["model_category"] == "trained"
        assert trained["artifact_available"] is True
        assert client.patch(f"/api/model/{model_id}", json={"group_path": "结果组"}).status_code == 200
        assert client.get(f"/api/model/{model_id}").json()["model"]["group_path"] == "结果组"

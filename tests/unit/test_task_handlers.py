"""The task worker delegates workflow side effects without changing task results."""

import json
from pathlib import Path

import pytest

from core.task.handlers import get_task_handler
from core.task import worker
from resources.workflows.hypoxemia import task_handler


def make_artifacts(trial_dir, run_dir):
    trial_dir.mkdir()
    run_dir.mkdir()
    (trial_dir / "trial_result.json").write_text(
        json.dumps({"run_dir": str(run_dir)}), encoding="utf-8"
    )
    (run_dir / "best_model.pt").write_bytes(b"weights")
    (run_dir / "scaler.pkl").write_bytes(b"scaler")
    (run_dir / "tslib_model_config.json").write_text(
        json.dumps({"seq_len": 180}), encoding="utf-8"
    )
    (run_dir / "threshold_report_f1.json").write_text(
        json.dumps({"validation": {"threshold": 0.42}}), encoding="utf-8"
    )


def test_training_handler_registers_artifacts_in_isolated_registry(tmp_path, monkeypatch):
    registry = tmp_path / "models.json"
    registry.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(task_handler, "model_registry_path", lambda: registry)
    trial_dir = tmp_path / "trial"
    run_dir = tmp_path / "trained"
    make_artifacts(trial_dir, run_dir)
    task = {"metadata": {
        "model_id": "SegRNN", "model_name": "SegRNN", "dataset_id": "data-1",
        "trial_dir": str(trial_dir), "training_config": {"epochs": 3},
        "sequence_length": 180, "sampling_hz": 1,
    }}
    handler = get_task_handler("timeseries-training")
    handler.on_start(task, "task-123-abc")
    artifacts = handler.on_success(task, "task-123-abc")

    models = json.loads(registry.read_text(encoding="utf-8"))
    assert len(models) == 1
    assert models[0]["id"] == artifacts["model_resource_id"]
    assert models[0]["runtime_status"] == "trained"
    assert models[0]["threshold"] == 0.42
    assert models[0]["dataset_id"] == "data-1"
    assert artifacts["model_weights_path"] == str(run_dir / "best_model.pt")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sequence_length"] == 180


def test_training_handler_defers_registration_when_requested(tmp_path, monkeypatch):
    registry = tmp_path / "models.json"
    registry.write_text(json.dumps([{"id": "config-1", "runtime_status": "training"}]), encoding="utf-8")
    monkeypatch.setattr(task_handler, "model_registry_path", lambda: registry)
    trial_dir = tmp_path / "trial"
    run_dir = tmp_path / "run"
    make_artifacts(trial_dir, run_dir)
    task = {"metadata": {
        "model_id": "config-1", "trial_dir": str(trial_dir), "save_trained_model": False,
        "sequence_length": 180, "sampling_hz": 1,
    }}

    artifacts = task_handler.HypoxemiaTrainingTaskHandler().on_success(task, "task-1")

    assert artifacts["artifact_path"] == str(run_dir)
    assert "model_resource_id" not in artifacts
    models = json.loads(registry.read_text(encoding="utf-8"))
    assert len(models) == 1
    assert models[0]["runtime_status"] == "configured"


def test_training_completion_reuses_resource_for_same_task(tmp_path, monkeypatch):
    registry = tmp_path / "models.json"
    registry.write_text(json.dumps([{"id": "config-1", "runtime_status": "configured"}]), encoding="utf-8")
    monkeypatch.setattr(task_handler, "model_registry_path", lambda: registry)
    trial_dir, run_dir = tmp_path / "trial", tmp_path / "run"
    make_artifacts(trial_dir, run_dir)
    task = {"metadata": {"model_id": "config-1", "trial_dir": str(trial_dir),
                         "sequence_length": 180, "sampling_hz": 1}}
    handler = task_handler.HypoxemiaTrainingTaskHandler()

    first = handler.on_success(task, "task-123-abc")
    second = handler.on_success(task, "task-123-abc")

    assert first["model_resource_id"] == second["model_resource_id"]
    models = json.loads(registry.read_text(encoding="utf-8"))
    assert len(models) == 2
    assert next(item for item in models if item["id"] == "config-1")["runtime_status"] == "configured"


def test_legacy_training_task_reads_sampling_rate_from_dataset_stats(tmp_path, monkeypatch):
    registry = tmp_path / "models.json"
    registry.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(task_handler, "model_registry_path", lambda: registry)
    trial_dir, run_dir = tmp_path / "trial", tmp_path / "run"
    make_artifacts(trial_dir, run_dir)
    data_dir = tmp_path / "dataset"
    data_dir.mkdir()
    (data_dir / "window_stats.json").write_text(
        json.dumps({"sampling_hz": 1}), encoding="utf-8"
    )
    task = {"metadata": {"model_id": "SegRNN", "trial_dir": str(trial_dir)},
            "command": ["python", "train.py", "--data-path", str(data_dir / "window_features.pkl")]}

    result = task_handler.HypoxemiaTrainingTaskHandler().on_success(task, "task-legacy")
    manifest = json.loads(Path(result["model_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["sampling_hz"] == 1


def test_training_handler_rejects_missing_artifact(tmp_path, monkeypatch):
    registry = tmp_path / "models.json"
    registry.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(task_handler, "model_registry_path", lambda: registry)
    trial_dir = tmp_path / "trial"
    run_dir = tmp_path / "run"
    make_artifacts(trial_dir, run_dir)
    (run_dir / "scaler.pkl").unlink()

    with pytest.raises(RuntimeError, match="scaler.pkl"):
        task_handler.HypoxemiaTrainingTaskHandler().on_success(
            {"metadata": {"trial_dir": str(trial_dir), "sampling_hz": 1}}, "task-1"
        )
    assert json.loads(registry.read_text(encoding="utf-8")) == []


def test_training_handler_rejects_missing_run_directory(tmp_path):
    trial_dir = tmp_path / "trial"
    trial_dir.mkdir()
    (trial_dir / "trial_result.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="run_dir"):
        task_handler.HypoxemiaTrainingTaskHandler().on_success(
            {"metadata": {"trial_dir": str(trial_dir)}}, "task-1"
        )


def test_training_handler_rejects_missing_threshold(tmp_path):
    trial_dir = tmp_path / "trial"
    run_dir = tmp_path / "run"
    make_artifacts(trial_dir, run_dir)
    (run_dir / "threshold_report_f1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="有效阈值"):
        task_handler.HypoxemiaTrainingTaskHandler().on_success(
            {"metadata": {"trial_dir": str(trial_dir), "sampling_hz": 1}}, "task-1"
        )


def test_namespaced_task_handlers_preserve_legacy_tasks():
    assert get_task_handler("hypoxemia-training") is get_task_handler("timeseries-training")
    assert get_task_handler("hypoxemia-data-processing") is get_task_handler("timeseries-data-processing")


@pytest.mark.parametrize(
    ("result", "cancelled", "expected_status"),
    [(0, False, "trained"), (5, False, "failed"), (0, True, "cancelled")],
)
def test_worker_calls_lifecycle_hooks(tmp_path, monkeypatch, result, cancelled, expected_status):
    registry = tmp_path / "models.json"
    registry.write_text(json.dumps([{"id": "model-1", "runtime_status": "configured"}]), encoding="utf-8")
    monkeypatch.setattr(task_handler, "model_registry_path", lambda: registry)
    task = {
        "id": "task-1", "kind": "timeseries-training", "status": "queued",
        "metadata": {"model_id": "model-1", "trial_dir": str(tmp_path / "trial"),
                     "sequence_length": 180, "sampling_hz": 1},
        "log_path": str(tmp_path / "task.log"), "command": ["unused"], "cwd": "",
    }
    if result == 0 and not cancelled:
        make_artifacts(tmp_path / "trial", tmp_path / "run")

    class FakeManager:
        def __init__(self):
            self.task = task.copy()
            self.updates = []

        def get(self, task_id):
            current = self.task.copy()
            if cancelled and current.get("status") == "running" and current.get("command_pid"):
                self.task["status"] = "cancelled"
                task_handler.HypoxemiaTrainingTaskHandler().on_cancel(task, task_id)
                current = self.task.copy()
            return current

        def claim(self, task_id, updates):
            return self._update(task_id, updates)

        def _update(self, task_id, updates, **kwargs):
            self.task.update(updates)
            self.updates.append(dict(updates))
            return self.task

    class FakeProcess:
        pid = 123

        def terminate(self):
            return None

        def wait(self):
            return result

    manager = FakeManager()
    monkeypatch.setattr(worker, "TaskManager", lambda: manager)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    assert worker.main("task-1") == result
    models = json.loads(registry.read_text(encoding="utf-8"))
    if expected_status == "trained":
        assert len(models) == 2
        assert next(item for item in models if item["id"] != "model-1")["runtime_status"] == "trained"
    else:
        assert models[0]["runtime_status"] == expected_status
    if not cancelled:
        assert manager.task["status"] == ("completed" if result == 0 else "failed")
    if result == 0 and not cancelled:
        completed = next(item for item in manager.updates if item.get("status") == "completed")
        assert completed["metadata"]["artifact_path"] == str(tmp_path / "run")
        assert manager.updates[-1] is completed
    assert get_task_handler("ordinary-tool") is None

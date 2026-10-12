"""Evaluation must use the selected artifacts and reject incompatible windows."""

import importlib.util
import json
from pathlib import Path

import pytest


TOOL_PATH = (Path(__file__).resolve().parents[2] / "resources" / "tools" /
             "implementations" / "timeseries-classification-runner" / "tool.py")
SPEC = importlib.util.spec_from_file_location("hypoxemia_evaluation_tool", TOOL_PATH)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def make_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPO2_HYPOXEMIA_TRUSTED_DATA_ROOTS", str(tmp_path))
    dataset = tmp_path / "dataset"
    artifact = tmp_path / "artifact"
    dataset.mkdir()
    artifact.mkdir()
    features = ["SpO2", "HR", "PR", "RR", "PI"]
    for name in ("window_features.pkl", "window_info.pkl"):
        (dataset / name).write_bytes(b"fixture")
    (dataset / "window_stats.json").write_text(json.dumps({
        "lookback_seconds": 180, "sampling_hz": 1,
    }), encoding="utf-8")
    (dataset / "dataset_manifest.json").write_text(json.dumps({
        "dataset_id": "data-1", "sequence_length": 180, "sampling_hz": 1,
        "feature_order": features,
    }), encoding="utf-8")
    for name in ("best_model.pt", "scaler.pkl"):
        (artifact / name).write_bytes(b"fixture")
    (artifact / "tslib_model_config.json").write_text(json.dumps({
        "model": "SegRNN", "seq_len": 180, "enc_in": 5,
    }), encoding="utf-8")
    (artifact / "threshold_report_f1.json").write_text(json.dumps({
        "validation": {"threshold": 0.5},
    }), encoding="utf-8")
    (artifact / "manifest.json").write_text(json.dumps({
        "sequence_length": 180, "sampling_hz": 1, "feature_order": features,
    }), encoding="utf-8")
    registry = tmp_path / "models.json"
    registry.write_text(json.dumps([{
        "id": "model-1", "model_path": str(artifact / "best_model.pt"),
        "runtime_status": "trained",
    }]), encoding="utf-8")
    monkeypatch.setattr(tool, "model_registry_path", lambda: registry)
    monkeypatch.setattr(tool, "timeseries_runs_dir", lambda: tmp_path / "runs")
    return dataset, artifact


@pytest.mark.parametrize("field,value", [
    ("sequence_length", 256), ("sampling_hz", 2),
    ("feature_order", ["HR", "SpO2", "PR", "RR", "PI"]),
])
def test_evaluation_rejects_incompatible_manifest(tmp_path, monkeypatch, field, value):
    dataset, artifact = make_inputs(tmp_path, monkeypatch)
    path = dataset / "dataset_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = tool._evaluate({"dataset": str(dataset), "model_id": "model-1"})

    assert result["status"] == "failed"
    assert "不兼容" in result["message"] or "不一致" in result["message"]
    assert not (tmp_path / "runs").exists()


def test_evaluation_uses_model_id_and_records_dataset_source(tmp_path, monkeypatch):
    dataset, artifact = make_inputs(tmp_path, monkeypatch)
    calls = []

    def evaluate(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps({"metrics": {"f1": 0.7},
                                      "dataset_window_count": 3}), encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(tool.subprocess, "run", evaluate)

    result = tool._evaluate({"dataset": str(dataset), "model_id": "model-1", "export_excel": True})

    assert result["status"] == "success"
    assert calls[0][calls[0].index("--artifact") + 1] == str(artifact)
    assert result["data"]["model_resource_id"] == "model-1"
    assert result["data"]["dataset_id"] == "data-1"
    assert Path(result["data"]["file_path"]).is_file()


def test_evaluation_accepts_legacy_stats_without_sampling_rate(tmp_path, monkeypatch):
    dataset, artifact = make_inputs(tmp_path, monkeypatch)
    (dataset / "dataset_manifest.json").unlink()
    (dataset / "window_stats.json").write_text(json.dumps({
        "lookback_seconds": 180,
        "interval_seconds": 30,
        "total_windows": 3,
    }), encoding="utf-8")

    def evaluate(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps({"metrics": {"f1": 0.7},
                                      "dataset_window_count": 1}), encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(tool.subprocess, "run", evaluate)
    result = tool._evaluate({"dataset": str(dataset), "model_id": "model-1"})

    assert result["status"] == "success"
    assert result["data"]["dataset_id"] == ""


def test_evaluation_rejects_model_id_path_disagreement(tmp_path, monkeypatch):
    dataset, artifact = make_inputs(tmp_path, monkeypatch)
    result = tool._evaluate({
        "dataset": str(dataset), "model_id": "model-1",
        "artifact_path": str(tmp_path / "other-artifact"),
    })
    assert result["status"] == "failed"
    assert "不一致" in result["message"]


def test_evaluation_rejects_dataset_id_path_disagreement(tmp_path, monkeypatch):
    dataset, artifact = make_inputs(tmp_path, monkeypatch)
    result = tool._evaluate({"dataset": str(dataset), "dataset_id": "data-2",
                             "model_id": "model-1"})
    assert result["status"] == "failed"
    assert "不一致" in result["message"]


def test_evaluation_requires_model_manifest(tmp_path, monkeypatch):
    dataset, artifact = make_inputs(tmp_path, monkeypatch)
    (artifact / "manifest.json").unlink()
    result = tool._evaluate({"dataset": str(dataset), "model_id": "model-1"})
    assert result["status"] == "failed"
    assert "manifest" in result["message"]

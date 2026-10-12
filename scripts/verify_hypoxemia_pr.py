"""Run the offline hypoxemia acceptance flow with synthetic, non-patient data.

Run with the dedicated PyTorch interpreter. Set SOTABAND_RUNTIME_ROOT to an
isolated directory before starting this script.
"""

from __future__ import annotations

import json
import os
import pickle
import runpy
import sys
import time
import zipfile
import argparse
import hashlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.runtime.paths import RUNTIME_ROOT
from core.task.task_manager import TaskManager
from core.timeseries.manifests import write_dataset_manifest
from resources.workflows.hypoxemia.runtime_config import compute_env


def await_task(request):
    task = TaskManager().submit(**request)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        task = TaskManager().get(task["id"])
        if task["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.5)
    assert task["status"] == "completed", {"status": task["status"],
        "message": task.get("message"), "log_tail": task.get("log_tail", "")[-3000:]}
    return task


def verify_raw(tool):
    """Synthetic CSV -> ZIP -> durable task -> registered window dataset."""
    archive = RUNTIME_ROOT / "synthetic-monitor.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for group in range(24):
            rows = ["Time,SpO2,HR,PR,RR,PI"]
            start = datetime(2025, 1, 1)
            for second in range(1200):
                saturation = 90 if group % 2 == 0 and 900 <= second < 940 else 98
                rows.append(f"{start + timedelta(seconds=second)},{saturation},75,75,16,2")
            zipped.writestr(f"synthetic/monitor___{group:03d}/ParameterData/ParameterData-1s-synthetic.csv",
                            "\n".join(rows))
    requested = tool["execute"](mode="process-raw-dataset", source_path=str(archive),
        edge_trim_mode="none", crop_seconds=0, min_segment_seconds=600)
    assert requested["status"] == "success", requested
    task = await_task(requested["data"]["task_request"])
    dataset = Path(task["metadata"]["processed_dataset_path"])
    checked = tool["execute"](mode="validate", source_path=task["metadata"]["dataset_id"])
    assert checked["status"] == "success", checked
    assert checked["data"]["window_count"] > 0, checked
    return {"task_id": task["id"], "dataset_id": task["metadata"]["dataset_id"],
            "windows": checked["data"]["window_count"], "path": str(dataset)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="SegRNN", choices=["SegRNN", "SegRNNDualScaleV2"])
    parser.add_argument("--skip-raw", action="store_true")
    args = parser.parse_args()
    if "SOTABAND_RUNTIME_ROOT" not in os.environ:
        raise RuntimeError("Set SOTABAND_RUNTIME_ROOT to an isolated directory")
    project = Path(__file__).resolve().parents[1]
    tool = runpy.run_path(str(project / "resources/tools/implementations/timeseries-classification-runner/tool.py"))
    assert not (RUNTIME_ROOT / "tasks/registry.json").exists(), "Use a NEW runtime root for each acceptance run"
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    raw_result = {} if args.skip_raw else verify_raw(tool)
    dataset = RUNTIME_ROOT / "synthetic-windows"
    dataset.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    features = []
    info = []
    for group in range(24):
        for sample in range(2):
            positive = group % 2 == 0
            window = np.column_stack((
                np.arange(256),
                97 - (4 if positive else 0) + rng.normal(0, 0.2, 256),
                75 + rng.normal(0, 1, 256),
                75 + rng.normal(0, 1, 256),
                16 + rng.normal(0, 0.5, 256),
                2 + rng.normal(0, 0.1, 256),
            ))
            features.append(window)
            info.append({"features": ["Time", "SpO2", "HR", "PR", "RR", "PI"],
                         "sample_type": "hypoxemia" if positive else "normal",
                         "group_id": f"synthetic-{group:02d}", "sample": sample})
    with (dataset / "window_features.pkl").open("wb") as handle:
        pickle.dump(features, handle)
    with (dataset / "window_info.pkl").open("wb") as handle:
        pickle.dump(info, handle)
    stats = {"lookback_seconds": 256, "sampling_hz": 1, "interval_seconds": 30,
             "total_windows": 48, "hypoxemia_windows": 24, "non_hypoxemia_windows": 24}
    (dataset / "window_stats.json").write_text(json.dumps(stats), encoding="utf-8")
    write_dataset_manifest(dataset, dataset_id="synthetic-pr-acceptance",
                           feature_order=["SpO2", "HR", "PR", "RR", "PI"],
                           sequence_length=256, sampling_hz=1)

    validated = tool["execute"](mode="validate", source_path=str(dataset))
    assert validated["status"] == "success", validated
    registered = tool["execute"](mode="register-dataset", source_path=str(dataset),
        dataset_id="synthetic-pr-acceptance", dataset_name="Synthetic acceptance windows")
    assert registered["status"] == "success", registered
    requested = tool["execute"](
        mode="train", dataset="synthetic-pr-acceptance", model_id=args.model,
        training_config={"epochs": 1, "batch_size": 8, "use_ema": False,
                         "early_stop_patience": 0},
        model_config={"d_model": 32},
    )
    assert requested["status"] == "success", requested
    task = await_task(requested["data"]["task_request"])
    artifact = Path(task["metadata"]["artifact_path"])
    saved = tool["execute"](mode="inspect-model", artifact_path=str(artifact),
                            dataset_id="synthetic-pr-acceptance", register_model=True)
    assert saved["status"] == "success", saved
    model_id = saved["data"]["model_id"]
    training_dataset = dataset
    dataset = RUNTIME_ROOT / "synthetic-evaluation-windows"
    dataset.mkdir()
    evaluation_features, evaluation_info = [], []
    for index in range(60):
        window = features[index % len(features)].copy()
        window[:, 1:] += rng.normal(0, 0.1, window[:, 1:].shape)
        evaluation_features.append(window)
        evaluation_info.append({**info[index % len(info)], "group_id": f"evaluation-{index // 2:02d}"})
    (dataset / "window_features.pkl").write_bytes(pickle.dumps(evaluation_features))
    (dataset / "window_info.pkl").write_bytes(pickle.dumps(evaluation_info))
    (dataset / "window_stats.json").write_text(json.dumps({**stats, "total_windows": 60,
        "hypoxemia_windows": 30, "non_hypoxemia_windows": 30}), encoding="utf-8")
    write_dataset_manifest(dataset, dataset_id="synthetic-evaluation",
        feature_order=["SpO2", "HR", "PR", "RR", "PI"], sequence_length=256, sampling_hz=1)
    registered = tool["execute"](mode="register-dataset", source_path=str(dataset),
        dataset_id="synthetic-evaluation", dataset_name="Separate synthetic evaluation windows")
    assert registered["status"] == "success", registered
    evaluated = tool["execute"](mode="evaluate", dataset="synthetic-evaluation",
                                model_id=model_id, export_excel=True)
    assert evaluated["status"] == "success", evaluated
    result = evaluated["data"]
    assert result["model_resource_id"] == model_id, result
    assert Path(result["model_artifact"]).resolve() == artifact.resolve(), result
    assert result["source_dataset_window_count"] == 60, result
    assert Path(result["dataset_path"]).resolve() != training_dataset.resolve(), result
    assert result["dataset_id"] == "synthetic-evaluation", result
    excel = Path(result["excel_path"])
    assert excel.is_file() and zipfile.is_zipfile(excel), excel
    # The download route belongs to the platform environment, so invoke its
    # verification in a separate process instead of importing FastAPI here.
    platform_python = os.environ.get("SOTABAND_PLATFORM_PYTHON", "")
    if not platform_python:
        relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        platform_python = str(project / ".venv-platform" / relative)
    route_check = subprocess.run(
        [platform_python, str(project / "scripts/verify_hypoxemia_download.py"),
         "--excel", str(excel), "--model-id", model_id],
        env={**compute_env(), "SOTABAND_RUNTIME_ROOT": str(RUNTIME_ROOT),
             "SOTABAND_LOAD_DOTENV": "0"},
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert route_check.returncode == 0, route_check.stdout + route_check.stderr
    download_evidence = json.loads(route_check.stdout)

    mismatched = dataset / "dataset_manifest.json"
    original = mismatched.read_text(encoding="utf-8")
    try:
        manifest = json.loads(original)
        manifest["sampling_hz"] = 2
        mismatched.write_text(json.dumps(manifest), encoding="utf-8")
        rejected = tool["execute"](mode="evaluate", dataset=str(dataset), model_id=model_id)
        assert rejected["status"] != "success", rejected
    finally:
        mismatched.write_text(original, encoding="utf-8")

    try:
        manifest = json.loads(original)
        manifest["feature_order"] = ["HR", "SpO2", "PR", "RR", "PI"]
        mismatched.write_text(json.dumps(manifest), encoding="utf-8")
        assert tool["execute"](mode="evaluate", dataset=str(dataset), model_id=model_id)["status"] == "failed"
    finally:
        mismatched.write_text(original, encoding="utf-8")

    feature_file = dataset / "window_features.pkl"
    original_windows = feature_file.read_bytes()
    try:
        shortened = pickle.loads(original_windows)
        shortened[0] = shortened[0][:-1]
        with feature_file.open("wb") as handle:
            pickle.dump(shortened, handle)
        rejected_length = tool["execute"](mode="evaluate", dataset=str(dataset), model_id=model_id)
        assert rejected_length["status"] != "success", rejected_length
    finally:
        feature_file.write_bytes(original_windows)

    # All windows equally short must also fail: the declaration cannot be inferred from payload shape.
    try:
        feature_file.write_bytes(pickle.dumps([window[:-1] for window in evaluation_features]))
        assert tool["execute"](mode="evaluate", dataset=str(dataset), model_id=model_id)["status"] == "failed"
    finally:
        feature_file.write_bytes(original_windows)
    weights = artifact / "best_model.pt"
    hidden = artifact / "best_model.pt.acceptance-backup"
    weights.rename(hidden)
    try:
        assert tool["execute"](mode="evaluate", dataset=str(dataset), model_id=model_id)["status"] == "failed"
    finally:
        hidden.rename(weights)

    # Real subprocess failure and cancellation, independent of mocked unit tests.
    manager = TaskManager()
    failed = manager.submit(name="synthetic launch failure", command=[str(RUNTIME_ROOT / "missing-python.exe")])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and manager.get(failed["id"])["status"] not in {"failed", "completed"}:
        time.sleep(0.2)
    assert manager.get(failed["id"])["status"] == "failed"
    cancelled = manager.submit(name="synthetic cancellation", command=[sys.executable, "-c", "import time; time.sleep(120)"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not manager.get(cancelled["id"]).get("command_pid"):
        time.sleep(0.2)
    assert manager.cancel(cancelled["id"])["status"] == "cancelled"
    assert manager.cancel(cancelled["id"])["status"] == "cancelled"

    from resources.workflows.hypoxemia.runtime_config import compute_python
    version = subprocess.check_output([compute_python(), "-c", "import sys,torch; print(sys.version); print(torch.__version__)"],
                                     env=compute_env(), text=True, encoding="utf-8")

    evidence = {
        "raw_processing": raw_result, "architecture": args.model, "compute_version": version.strip(),
        "platform_python": sys.version, "compute_interpreter": compute_python(),
        "task_id": task["id"], "model_id": model_id,
        "artifact_path": str(artifact), "dataset_path": str(dataset),
        "training_dataset_path": str(training_dataset), "evaluation_dataset_id": result["dataset_id"],
        "source_dataset_window_count": result["source_dataset_window_count"],
        "evaluation_scope": result["evaluation_scope"],
        "evaluation_window_count": result["dataset_window_count"],
        "metrics": result["validation"], "excel_path": str(excel),
        "sampling_mismatch_rejected": True,
        "short_window_rejected": True,
        "channel_mismatch_rejected": True, "all_short_windows_rejected": True,
        "missing_artifact_rejected": True, "launch_failure_persisted": True,
        "cancellation_persisted": True, "excel_opened_and_downloaded": download_evidence,
        "weights_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
    }
    (RUNTIME_ROOT / "acceptance.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

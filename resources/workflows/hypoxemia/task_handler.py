"""Model registry updates for hypoxemia training tasks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from core.resource.registry.json_store import update_json_list
from core.runtime.paths import model_registry_path
from core.task.task_manager import _now
from core.timeseries.manifests import write_dataset_manifest, write_model_manifest


def _update_model_resource(model_id: str, updates: dict[str, Any]) -> None:
    if not model_id:
        return
    def change(models):
        for model in models:
            if model.get("id") == model_id:
                model.update(updates)
                return

    update_json_list(model_registry_path(), change)


def _ensure_trained_model_resource(task: dict, run_dir: Path, report: dict, task_id: str) -> str:
    metadata = task.get("metadata", {})
    catalog_model_id = str(metadata.get("model_id", ""))
    model_name = str(metadata.get("model_name") or catalog_model_id or run_dir.name)
    resource_id = f"model-trained-{task_id}"
    entry = {
        "id": resource_id,
        "name": f"{model_name}_{task_id.rsplit('-', 1)[-1]}",
        "version": "0.1.0",
        "type": "model",
        "status": "registered",
        "spec_path": f"definitions/{resource_id}.md",
        "framework": "PyTorch",
        "model_path": str(run_dir / "best_model.pt"),
        "input_format": f"timeseries-{int(metadata.get('sequence_length') or 256)}x5",
        "output_format": "binary-classification",
        "tags": ["时序分类", "低氧预测", "五通道", model_name],
        "associated_tool_id": "timeseries-classification-runner",
        "dataset_id": str(metadata.get("dataset_id", "")),
        "model_name": model_name,
        "model_type": model_name,
        "config_path": str(run_dir / "tslib_model_config.json"),
        "scaler_path": str(run_dir / "scaler.pkl"),
        "metrics_path": str(run_dir / "threshold_report_f1.json"),
        "threshold": report.get("validation", {}).get("threshold"),
        "task_id": task_id,
        "runtime_status": "trained",
        "training_config": metadata.get("training_config", {}),
        "config_schema": {},
        "catalog_model_id": catalog_model_id,
        "creation_type": "catalog-model",
        "group_path": "低氧血症预测",
        "derived_from_model_id": "",
        "created_at": _now(),
    }
    def add(models):
        existing = next((item for item in models if item.get("task_id") == task_id
                         and item.get("model_path") == entry["model_path"]), None)
        if existing:
            return existing["id"]
        models.append(entry)
        return resource_id

    return update_json_list(model_registry_path(), add)


def _complete_training_model(task: dict, task_id: str) -> dict[str, str]:
    metadata = task.get("metadata", {})
    model_id = str(metadata.get("model_id", ""))
    model_name = str(metadata.get("model_name") or model_id or "unknown")
    trial_dir = Path(str(metadata.get("trial_dir", "")))
    try:
        trial_result = json.loads((trial_dir / "trial_result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"训练结果不存在或无效: {trial_dir / 'trial_result.json'}") from exc
    run_dir = Path(str(trial_result.get("run_dir", ""))) if trial_result.get("run_dir") else None
    if not run_dir:
        raise RuntimeError("训练结果缺少 run_dir")
    required = ("best_model.pt", "scaler.pkl", "tslib_model_config.json", "threshold_report_f1.json")
    missing = [name for name in required if not (run_dir / name).is_file()
               or (run_dir / name).stat().st_size == 0]
    if missing:
        raise RuntimeError(f"训练产物不完整: {', '.join(missing)}")
    config_path = run_dir / "tslib_model_config.json"
    try:
        model_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"训练配置无效: {config_path}") from exc
    report_path = run_dir / "threshold_report_f1.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"阈值报告无效: {report_path}") from exc
    try:
        threshold = float(report["validation"]["threshold"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"阈值报告缺少有效阈值: {report_path}") from exc
    if not 0 <= threshold <= 1:
        raise RuntimeError(f"阈值超出范围: {threshold}")
    sequence_length = int(model_config.get("seq_len") or metadata.get("sequence_length") or 0)
    sampling_hz = int(metadata.get("sampling_hz") or 0)
    if sampling_hz <= 0:
        command = task.get("command") or []
        if "--data-path" in command:
            index = command.index("--data-path")
            if index + 1 < len(command):
                dataset_root = Path(str(command[index + 1])).parent
                try:
                    stats = json.loads((dataset_root / "window_stats.json").read_text(encoding="utf-8"))
                    sampling_hz = int(stats.get("sampling_hz") or 0)
                except (OSError, json.JSONDecodeError, ValueError, TypeError):
                    pass
    if sequence_length <= 0 or sampling_hz <= 0:
        raise RuntimeError("训练产物缺少有效的窗口长度或采样率")
    if metadata.get("sequence_length") and sequence_length != int(metadata["sequence_length"]):
        raise RuntimeError("模型窗口长度与训练数据集不一致")
    manifest_path = write_model_manifest(
        run_dir,
        model_id=model_id,
        task_id=task_id,
        model_name=model_name,
        dataset_id=str(metadata.get("dataset_id", "")),
        feature_order=metadata.get("feature_order") or ["SpO2", "HR", "PR", "RR", "PI"],
        sequence_length=sequence_length,
        sampling_hz=sampling_hz,
        preprocessing=metadata.get("processing_config", {}),
        threshold=threshold,
        artifacts={
            "weights": str(run_dir / "best_model.pt"),
            "scaler": str(run_dir / "scaler.pkl"),
            "config": str(run_dir / "tslib_model_config.json"),
            "threshold_report": str(report_path),
        },
    )
    if not metadata.get("save_trained_model", True):
        _update_model_resource(model_id, {"task_id": task_id, "runtime_status": "configured"})
        return {
            "artifact_path": str(run_dir),
            "model_weights_path": str(run_dir / "best_model.pt"),
            "model_scaler_path": str(run_dir / "scaler.pkl"),
            "model_config_path": str(run_dir / "tslib_model_config.json"),
            "model_metrics_path": str(report_path),
            "model_manifest_path": str(manifest_path),
        }
    resource_id = _ensure_trained_model_resource(task, run_dir, report, task_id)
    if model_id != resource_id:
        _update_model_resource(model_id, {"task_id": task_id, "runtime_status": "configured"})
    _update_model_resource(resource_id, {
        "model_path": str(run_dir / "best_model.pt"),
        "config_path": str(run_dir / "tslib_model_config.json"),
        "scaler_path": str(run_dir / "scaler.pkl"),
        "metrics_path": str(report_path),
        "threshold": report.get("validation", {}).get("threshold"),
        "task_id": task_id,
        "runtime_status": "trained",
    })
    return {
        "model_resource_id": resource_id,
        "artifact_path": str(run_dir),
        "model_weights_path": str(run_dir / "best_model.pt"),
        "model_scaler_path": str(run_dir / "scaler.pkl"),
        "model_config_path": str(run_dir / "tslib_model_config.json"),
        "model_metrics_path": str(report_path),
        "model_manifest_path": str(manifest_path),
    }


class HypoxemiaTrainingTaskHandler:
    @staticmethod
    def process_environment():
        from .runtime_config import compute_env
        return compute_env()

    def _set_status(self, task: dict, task_id: str, status: str) -> None:
        model_id = str(task.get("metadata", {}).get("model_id", ""))
        def change(models):
            for model in models:
                if model.get("id") == model_id and model.get("runtime_status") != "trained":
                    model.update({"task_id": task_id, "runtime_status": status})
                    return
        if model_id:
            update_json_list(model_registry_path(), change)

    def on_start(self, task: dict, task_id: str) -> None:
        self._set_status(task, task_id, "training")

    def on_success(self, task: dict, task_id: str) -> dict[str, str]:
        return _complete_training_model(task, task_id)

    def on_failure(self, task: dict, task_id: str) -> None:
        self._set_status(task, task_id, "failed")

    def on_cancel(self, task: dict, task_id: str) -> None:
        self._set_status(task, task_id, "cancelled")


class HypoxemiaDatasetTaskHandler:
    @staticmethod
    def process_environment():
        from .runtime_config import compute_env
        return compute_env()

    """Register validated raw-pipeline output in the data space."""

    def on_start(self, task: dict, task_id: str) -> None:
        return None

    def on_success(self, task: dict, task_id: str) -> dict[str, str]:
        metadata = task.get("metadata", {})
        output = Path(str(metadata.get("processed_dataset_path", ""))).expanduser().resolve()
        stats_path = output / "window_stats.json"
        features = output / "window_features.pkl"
        info = output / "window_info.pkl"
        if not output.is_dir() or not all(item.is_file() for item in (features, info, stats_path)):
            raise RuntimeError(f"处理完成但窗口数据不完整: {output}")
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取窗口统计: {stats_path}") from exc
        if int(stats.get("total_windows", 0)) <= 0:
            raise RuntimeError(f"处理完成但没有有效窗口: {output}")
        dataset_id = str(metadata.get("dataset_id") or f"spo2-five-channel-{task_id}")
        manifest = write_dataset_manifest(
            output, dataset_id=dataset_id, feature_order=["SpO2", "HR", "PR", "RR", "PI"],
            sequence_length=int(stats.get("lookback_seconds", 256)),
            sampling_hz=int(stats.get("sampling_hz", 1)),
            prediction_interval_seconds=int(stats.get("interval_seconds", 30)),
            stats=stats, source_path=metadata.get("source_path", ""),
            processing_config=metadata.get("processing_config", {}),
        )
        from core.api import get_api
        call = get_api("api-data-register").call(
            id=dataset_id, name=str(metadata.get("dataset_name") or output.name),
            data_path=str(output), file_count=len(list(output.rglob("*"))),
            total_size=sum(item.stat().st_size for item in output.rglob("*") if item.is_file()),
            formats=["pkl", "json"], tags=["时序分类", "低氧预测", "五通道"],
            group_path="低氧血症预测", manifest_path=str(manifest),
        )
        result = asyncio.run(call) if asyncio.iscoroutine(call) else call
        if not isinstance(result, dict) or result.get("dataset_id") != dataset_id:
            raise RuntimeError(f"数据集注册失败: {result}")
        return {"dataset_id": dataset_id, "dataset_manifest_path": str(manifest), "registration": str(result)}

    def on_failure(self, task: dict, task_id: str) -> None:
        return None

    def on_cancel(self, task: dict, task_id: str) -> None:
        return None

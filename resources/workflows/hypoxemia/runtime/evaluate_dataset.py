"""Evaluate a saved five-channel model on a selected window dataset."""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metrics", default="all")
    args = parser.parse_args()

    project = Path(__file__).resolve().parent
    sys.path.insert(0, str(project))
    from scripts.tslib_classification_common import (  # type: ignore
        build_tslib_config,
        create_data_loader,
        default_device,
        evaluate_epoch,
        instantiate_tslib_model,
        load_window_dataset,
        split_dataset_grouped_indices,
        split_dataset_random_indices,
        summarize_binary_split,
    )

    artifact = Path(args.artifact).resolve()
    dataset = Path(args.dataset).resolve()
    feature_path = dataset / "window_features.pkl"
    info_path = dataset / "window_info.pkl"
    config_path = artifact / "tslib_model_config.json"
    checkpoint_path = artifact / "best_model.pt"
    scaler_path = artifact / "scaler.pkl"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    model_name = str(config.get("model") or config.get("model_name"))
    x, y, mask, _features, window_info = load_window_dataset(feature_path, info_path)
    expected_len = int(config["seq_len"])
    expected_features = ["SpO2", "HR", "PR", "RR", "PI"]
    if _features != expected_features or x.shape[2] != len(expected_features):
        raise ValueError(f"dataset feature order is incompatible: {_features}")
    if x.shape[1] != expected_len or not np.all(mask.sum(axis=1) == expected_len):
        raise ValueError(f"dataset window length {x.shape[1]} does not match model {expected_len}")
    original_count = int(x.shape[0])
    original_positive_count = int(np.sum(y))
    report_path = artifact / "threshold_report_f1.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    split_report = report.get("split_report", {}) if isinstance(report, dict) else {}
    split_mode = str(split_report.get("split_mode") or "grouped")
    group_key = str(split_report.get("group_key") or "group_id")
    validation_size = float(split_report.get("validation_size", 0.15))
    test_size = float(split_report.get("test_size", 0.15))
    split_seed = int(split_report.get("seed", config.get("seed", 42)))
    if split_mode == "random_window":
        _train_idx, eval_idx, _test_idx, applied_split = split_dataset_random_indices(
            y, validation_size=validation_size, test_size=test_size, random_state=split_seed
        )
    else:
        _train_idx, eval_idx, _test_idx, applied_split = split_dataset_grouped_indices(
            y, window_info, validation_size=validation_size, test_size=test_size,
            random_state=split_seed, group_key=group_key,
        )
    x = x[eval_idx]
    y = y[eval_idx]
    mask = mask[eval_idx]
    evaluation_count = int(len(y))
    evaluation_positive_count = int(np.sum(y))
    with scaler_path.open("rb") as handle:
        scaler = pickle.load(handle)
    x = scaler.transform(x.reshape(-1, x.shape[-1])).reshape(x.shape).astype(np.float32)

    device = default_device()
    model_config = build_tslib_config(
        model_name,
        seq_len=expected_len,
        enc_in=int(x.shape[2]),
        num_class=2,
        device=device,
        overrides=config,
    )
    model = instantiate_tslib_model(model_name, model_config, tslib_root=project / "vendor" / "Time-Series-Library").to(device)
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    model.load_state_dict(checkpoint.get("model_state_dict", checkpoint), strict=True)
    from scripts.provenance import write_provenance
    provenance = write_provenance(Path(args.output).with_suffix(".provenance.json"))
    loader = create_data_loader(x, y, mask, batch_size=128, shuffle=False, seed=42)
    metrics = evaluate_epoch(model, loader, nn.CrossEntropyLoss(), device)
    threshold = float(report["validation"]["threshold"])
    if not np.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("invalid saved validation threshold")
    selected = [item.strip() for item in args.metrics.split(",") if item.strip()]
    if not selected or selected == ["all"]:
        selected = list(metrics.keys())
    summary = summarize_binary_split(metrics["y_true"], metrics["y_score"], threshold)
    result = {
        "runtime_provenance": provenance,
        "dataset_path": str(dataset),
        "evaluation_scope": "validation_split",
        "evaluation_split": "validation",
        "evaluation_scope_label": "验证集（按训练时的分组规则重新划分；训练集和测试集未参与本次评测）",
        "source_dataset_window_count": original_count,
        "source_dataset_positive_count": original_positive_count,
        "split_report": applied_split,
        "dataset_window_count": evaluation_count,
        "dataset_positive_count": evaluation_positive_count,
        "dataset_negative_count": int(evaluation_count - evaluation_positive_count),
        "model_artifact": str(artifact),
        "model_files": {
            "weights": str(checkpoint_path),
            "scaler": str(scaler_path),
            "config": str(config_path),
            "threshold_report": str(report_path),
        },
        "threshold": threshold,
        "metrics": {key: summary.get(key) for key in selected if key in summary},
        "all_metrics": summary,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

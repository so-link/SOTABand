"""Create non-patient inputs for the hypoxemia user guide; never run training.

Run with .venv-hypoxemia/Scripts/python.exe. Only NumPy is required.
The output directory must not exist, so existing datasets cannot be overwritten.
"""

from __future__ import annotations

import argparse
import json
import pickle
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np


def write_windows(root: Path, *, groups: int, seed: int, dataset_id: str) -> None:
    root.mkdir()
    rng = np.random.default_rng(seed)
    features, info = [], []
    for group in range(groups):
        positive = group % 2 == 0
        for sample in range(2):
            features.append(np.column_stack((
                np.arange(256),
                97 - (4 if positive else 0) + rng.normal(0, 0.2, 256),
                75 + rng.normal(0, 1, 256),
                75 + rng.normal(0, 1, 256),
                16 + rng.normal(0, 0.5, 256),
                2 + rng.normal(0, 0.1, 256),
            )))
            info.append({
                "features": ["Time", "SpO2", "HR", "PR", "RR", "PI"],
                "sample_type": "hypoxemia" if positive else "normal",
                "group_id": f"{dataset_id}-{group:03d}", "sample": sample,
            })
    (root / "window_features.pkl").write_bytes(pickle.dumps(features))
    (root / "window_info.pkl").write_bytes(pickle.dumps(info))
    stats = {"lookback_seconds": 256, "sampling_hz": 1, "interval_seconds": 30,
             "total_windows": groups * 2, "hypoxemia_windows": groups,
             "non_hypoxemia_windows": groups}
    (root / "window_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    manifest = {
        "manifest_version": 1, "kind": "timeseries-dataset",
        "dataset_id": dataset_id, "path": str(root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "feature_order": ["SpO2", "HR", "PR", "RR", "PI"],
        "sequence_length": 256, "sampling_hz": 1,
    }
    (root / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


def create_demo(output: Path) -> dict[str, str]:
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_windows(output / "processed-train", groups=24, seed=42,
                  dataset_id="demo-processed-train")
    write_windows(output / "processed-evaluation", groups=30, seed=43,
                  dataset_id="demo-processed-evaluation")
    raw = output / "raw"
    raw.mkdir()
    archive = raw / "synthetic-monitor.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for group in range(24):
            rows = ["Time,SpO2,HR,PR,RR,PI"]
            start = datetime(2025, 1, 1)
            for second in range(1200):
                saturation = 90 if group % 2 == 0 and 900 <= second < 940 else 98
                rows.append(f"{start + timedelta(seconds=second)},{saturation},75,75,16,2")
            zipped.writestr(
                f"synthetic/monitor___{group:03d}/ParameterData/ParameterData-1s-synthetic.csv",
                "\n".join(rows))
    paths = {"raw_zip": str(archive), "staging_root": str(output / "staging"),
             "processed_train": str(output / "processed-train"),
             "processed_evaluation": str(output / "processed-evaluation")}
    (output / "demo-paths.json").write_text(json.dumps(paths, indent=2), encoding="utf-8")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/hypoxemia-demo"))
    args = parser.parse_args()
    if args.output.expanduser().exists():
        parser.error("Output already exists; choose a new --output directory. No files changed.")
    for name, value in create_demo(args.output).items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()

"""Durable manifests shared by time-series workflow adapters."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def write_dataset_manifest(root: str | Path, *, dataset_id: str = "", **metadata: Any) -> Path:
    directory = Path(root).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_version": 1,
        "kind": "timeseries-dataset",
        "dataset_id": dataset_id,
        "path": str(directory),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **metadata,
    }
    path = directory / "dataset_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def write_model_manifest(root: str | Path, *, model_id: str = "", **metadata: Any) -> Path:
    directory = Path(root).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_version": 1,
        "kind": "timeseries-model-artifact",
        "model_id": model_id,
        "path": str(directory),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **metadata,
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def read_manifest(root: str | Path, filename: str = "manifest.json") -> dict[str, Any]:
    path = Path(root).expanduser().resolve() / filename
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

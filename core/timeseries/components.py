"""Small domain-neutral components for the time-series service.

These components only manage references and local files.  Domain-specific
feature extraction, model construction and metrics remain in workflow tools.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.resource.registry.json_store import update_json_list
from core.runtime.paths import model_registry_path, timeseries_runs_dir

from .contracts import DatasetRef, ModelRef


class LocalDatasetProcessor:
    """Validate a dataset reference without reading or transforming its data."""

    def validate(self, dataset: DatasetRef | str | Path, **metadata: Any) -> dict[str, Any]:
        source = dataset.path if isinstance(dataset, DatasetRef) else dataset
        if source is None:
            raise ValueError("dataset reference has no path")
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"dataset path does not exist: {path}")
        return {
            "dataset_id": dataset.id if isinstance(dataset, DatasetRef) else "",
            "path": str(path),
            "is_directory": path.is_dir(),
            "metadata": {**(dataset.metadata if isinstance(dataset, DatasetRef) else {}), **metadata},
        }


class JsonResultStore:
    """Persist result payloads below the isolated time-series runtime."""

    def __init__(self, root: Path | None = None):
        self.root = (root or timeseries_runs_dir()) / "results"

    def save(self, result: Any, *, result_id: str | None = None, **metadata: Any) -> dict[str, Any]:
        identifier = result_id or f"result-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", identifier) or identifier in {".", ".."}:
            raise ValueError(f"invalid result id: {identifier}")
        directory = self.root / identifier
        directory.mkdir(parents=True, exist_ok=False)
        payload = {"result_id": identifier, "created_at": time.time(), "metadata": metadata, "result": result}
        path = directory / "result.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return {"result_id": identifier, "path": str(path), "result": result, "metadata": metadata}

    def load(self, result_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", result_id) or result_id in {".", ".."}:
            raise ValueError(f"invalid result id: {result_id}")
        path = self.root / result_id / "result.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, **filters: Any) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        results = []
        for path in sorted(self.root.glob("*/result.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            metadata = item.get("metadata") or {}
            if all(metadata.get(key) == value for key, value in filters.items()):
                results.append(item)
        return results


class LocalArtifactStore:
    """Copy and load model artifacts without knowing their model framework."""

    def save(self, artifact: str | Path, *, destination: str | Path | None = None, **_: Any) -> str:
        source = Path(artifact).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"artifact does not exist: {source}")
        target = Path(destination).expanduser().resolve() if destination else timeseries_runs_dir() / "artifacts" / source.name
        if target.exists():
            raise FileExistsError(f"artifact destination already exists: {target}")
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return str(target)

    def load(self, model: ModelRef | str | Path, **_: Any) -> Path:
        path = model.artifact_path if isinstance(model, ModelRef) else Path(model)
        if path is None or not Path(path).exists():
            raise FileNotFoundError(f"model artifact does not exist: {path}")
        return Path(path).resolve()


class JsonModelCatalog:
    """Minimal registry-backed catalog with no domain-specific model rules."""

    def __init__(self, registry: Path | None = None):
        self.registry = registry or model_registry_path()

    def _read(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.registry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    def list_models(self, *, ids: Iterable[str] | None = None, **_: Any) -> list[dict[str, Any]]:
        models = self._read()
        allowed = set(ids or ())
        return [model for model in models if not allowed or model.get("id") in allowed]

    def create(self, spec: dict[str, Any], **_: Any) -> ModelRef:
        model_id = str(spec.get("id") or f"model-{uuid.uuid4().hex[:8]}")
        entry = {**spec, "id": model_id}
        def add(models):
            if any(item.get("id") == model_id for item in models):
                raise ValueError(f"model already exists: {model_id}")
            models.append(entry)

        update_json_list(self.registry, add)
        return ModelRef(id=model_id, name=str(entry.get("name", "")), metadata=entry)

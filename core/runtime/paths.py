"""Filesystem locations that must be isolated per checkout.

The baseline checkout keeps its historical locations.  This refactor checkout
defaults to ``.runtime/refactor`` so a second backend cannot update the
baseline task registry or consume its training outputs.  Every location can
be overridden for CI or a separately managed runtime.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(
    os.environ.get("SOTABAND_RUNTIME_ROOT") or str(PROJECT_ROOT / ".runtime" / "refactor")
).expanduser().resolve()

TASKS_DIR = RUNTIME_ROOT / "tasks"
TASK_REGISTRY_FILE = TASKS_DIR / "registry.json"
TASK_RUNS_DIR = TASKS_DIR / "runs"

MODEL_DIR = RUNTIME_ROOT / "models"
MODEL_REGISTRY_FILE = MODEL_DIR / "registry.json"
MODEL_GROUPS_FILE = MODEL_DIR / "groups.json"
DATA_DIR = RUNTIME_ROOT / "data"
DATASETS_DIR = DATA_DIR / "datasets"
DATA_REGISTRY_FILE = DATA_DIR / "registry.json"

TIMESERIES_RUNS_DIR = Path(
    os.environ.get("SOTABAND_TIMESERIES_RUNS_DIR", str(RUNTIME_ROOT / "timeseries-runs"))
).expanduser().resolve()
PROCESSED_DATA_DIR = Path(
    os.environ.get("SOTABAND_PROCESSED_DATA_DIR", str(RUNTIME_ROOT / "processed-data"))
).expanduser().resolve()
TEMP_DIR = Path(
    os.environ.get("SOTABAND_TEMP_DIR", str(RUNTIME_ROOT / "tmp"))
).expanduser().resolve()
LOGS_DIR = Path(
    os.environ.get("SOTABAND_LOGS_DIR", str(RUNTIME_ROOT / "logs"))
).expanduser().resolve()


def ensure_runtime_dirs() -> None:
    """Create only isolated runtime directories, never baseline directories."""

    for path in (
        TASKS_DIR,
        TASK_RUNS_DIR,
        MODEL_DIR,
        DATA_DIR,
        DATASETS_DIR,
        TIMESERIES_RUNS_DIR,
        PROCESSED_DATA_DIR,
        TEMP_DIR,
        LOGS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def task_registry_path() -> Path:
    ensure_runtime_dirs()
    if not TASK_REGISTRY_FILE.exists():
        TASK_REGISTRY_FILE.write_text("[]", encoding="utf-8")
    return TASK_REGISTRY_FILE


def task_runs_dir() -> Path:
    ensure_runtime_dirs()
    return TASK_RUNS_DIR


def timeseries_runs_dir() -> Path:
    ensure_runtime_dirs()
    return TIMESERIES_RUNS_DIR


def processed_data_dir() -> Path:
    ensure_runtime_dirs()
    return PROCESSED_DATA_DIR


def runtime_temp_dir() -> Path:
    ensure_runtime_dirs()
    return TEMP_DIR


def model_registry_path() -> Path:
    """Return an isolated model registry seeded from the baseline catalog once."""

    ensure_runtime_dirs()
    if not MODEL_REGISTRY_FILE.exists():
        baseline = PROJECT_ROOT / "resources" / "models" / "registry.json"
        if baseline.is_file():
            _seed_portable_catalog(baseline, MODEL_REGISTRY_FILE)
        else:
            MODEL_REGISTRY_FILE.write_text("[]", encoding="utf-8")
    return MODEL_REGISTRY_FILE


def model_groups_path() -> Path:
    """Return the isolated model-space group definitions."""

    ensure_runtime_dirs()
    if not MODEL_GROUPS_FILE.exists():
        MODEL_GROUPS_FILE.write_text(
            json.dumps(
                [
                    {"id": "hypoxemia", "name": "低氧血症预测", "system": True},
                    {"id": "other", "name": "其它模型", "system": True},
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return MODEL_GROUPS_FILE


def data_registry_path() -> Path:
    """Return the isolated dataset registry, seeded from static metadata once."""

    ensure_runtime_dirs()
    if not DATA_REGISTRY_FILE.exists():
        baseline = PROJECT_ROOT / "resources" / "data" / "registry.json"
        if baseline.is_file():
            _seed_portable_catalog(baseline, DATA_REGISTRY_FILE)
        else:
            DATA_REGISTRY_FILE.write_text("[]", encoding="utf-8")
    return DATA_REGISTRY_FILE


def read_runtime_model_registry() -> list[dict]:
    try:
        value = json.loads(model_registry_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _seed_portable_catalog(source: Path, target: Path) -> None:
    """Static examples may seed a checkout; machine-specific run state may not."""
    entries = json.loads(source.read_text(encoding="utf-8"))
    def portable(entry):
        if entry.get("task_id") or entry.get("runtime_status") in {"trained", "training", "failed", "cancelled"}:
            return False
        for key in ("model_path", "data_path", "config_path", "artifact_path", "manifest_path"):
            value = str(entry.get(key) or "")
            if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
                return False
        return True
    target.write_text(json.dumps([item for item in entries if portable(item)],
                                 ensure_ascii=False, indent=2), encoding="utf-8")

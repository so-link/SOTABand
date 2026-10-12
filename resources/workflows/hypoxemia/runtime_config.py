"""Locate the delivered computation component without importing PyTorch."""
from __future__ import annotations

import os
from pathlib import Path

COMPONENT_ROOT = Path(__file__).resolve().parent / "runtime"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def compute_python(override: str | None = None) -> str:
    """Explicit overrides are optional; managed installation is the default.

    The retired external-project and legacy interpreter variables are deliberately
    ignored so an existing .env cannot redirect a new installation to old code.
    """
    configured = override or os.environ.get("SOTABAND_HYPOXEMIA_PYTHON", "")
    if configured:
        return str(Path(configured).expanduser().resolve())
    relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return str(PROJECT_ROOT / ".venv-hypoxemia" / relative)


def compute_env() -> dict[str, str]:
    """Do not forward platform credentials or Python import overrides."""
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR", "HOME",
               "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL",
               "CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "MKL_NUM_THREADS"}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return result

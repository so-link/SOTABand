import sys
from pathlib import Path

from resources.workflows.hypoxemia.runtime_config import COMPONENT_ROOT, compute_env, compute_python


def test_retired_project_settings_cannot_redirect_computation(monkeypatch):
    monkeypatch.setenv("SPO2_HYPOXEMIA_PROJECT", "nonexistent-retired-project")
    monkeypatch.setenv("SPO2_HYPOXEMIA_PYTHON", "nonexistent-retired-python")
    monkeypatch.delenv("SOTABAND_HYPOXEMIA_PYTHON", raising=False)
    assert ".venv-hypoxemia" in compute_python()
    assert (COMPONENT_ROOT / "scripts/run_tslib_classification_trial.py").is_file()


def test_compute_child_does_not_inherit_credentials_or_pythonpath(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-secret")
    monkeypatch.setenv("PYTHONPATH", "old-source-tree")
    env = compute_env()
    assert "LLM_API_KEY" not in env
    assert "PYTHONPATH" not in env
    assert "SPO2_HYPOXEMIA_PROJECT" not in env


def test_interpreter_override_is_explicit(monkeypatch):
    monkeypatch.setenv("SOTABAND_HYPOXEMIA_PYTHON", sys.executable)
    assert compute_python() == str(Path(sys.executable).resolve())

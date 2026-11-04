import importlib.util
from pathlib import Path

import pytest

PATH = (
    Path(__file__).resolve().parents[2]
    / "implementations"
    / "code-reproduction"
    / "deploy.py"
)
SPEC = importlib.util.spec_from_file_location("code_reproduction_deploy", PATH)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def test_systemd_unit_contains_isolated_worker_configuration(tmp_path):
    unit = deploy.systemd_unit(
        tmp_path / "project root",
        tmp_path / "venv/bin/python",
        "researcher",
        tmp_path / "academic data",
        "server-user",
    )
    assert "User=researcher" in unit
    assert "SupplementaryGroups=docker" in unit
    assert "resources.tools._academic.worker" in unit
    assert "SOTABAND_ACADEMIC_DATA=" in unit
    assert "Restart=on-failure" in unit


def test_systemd_unit_rejects_line_injection(tmp_path):
    with pytest.raises(deploy.DeploymentError, match="line breaks"):
        deploy.systemd_unit(tmp_path, tmp_path / "python", "user", tmp_path, "bad\nvalue")


def test_deployment_runner_dry_run_does_not_execute():
    runner = deploy.Runner(dry_run=True)
    assert runner.run(["definitely-not-a-command", "argument"]) == ""
    assert runner.actions == [["definitely-not-a-command", "argument"]]

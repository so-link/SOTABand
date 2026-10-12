"""Task registry transitions remain correct across worker timing races."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.task import task_manager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(task_manager, "TASK_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "RUNS_DIR", tmp_path / "tasks" / "runs")
    monkeypatch.setattr(task_manager, "REGISTRY_FILE", tmp_path / "tasks" / "registry.json")
    return task_manager.TaskManager()


def test_worker_launch_failure_is_persisted_as_failed(manager, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("executable unavailable")

    monkeypatch.setattr(task_manager.subprocess, "Popen", fail)
    with pytest.raises(RuntimeError, match="工作进程"):
        manager.submit(name="sample", command=["python", "task.py"])

    task = manager.list_all()[0]
    assert task["status"] == "failed"
    assert task["finished_at"]
    assert task["return_code"] == -1


def test_worker_pid_does_not_replace_command_pid(manager, monkeypatch):
    class WorkerProcess:
        pid = 100

    def start(*args, **kwargs):
        task = manager.list_all()[0]
        manager._update(task["id"], {"status": "running", "phase": "executing",
                                     "command_pid": 200}, expected_status="queued")
        return WorkerProcess()

    monkeypatch.setattr(task_manager.subprocess, "Popen", start)
    task = manager.submit(name="sample", command=["python", "task.py"])

    assert task["worker_pid"] == 100
    assert task["command_pid"] == 200
    assert task["pid"] == 100


def test_terminal_state_cannot_restart_and_finalization_cannot_cancel(manager, monkeypatch):
    class WorkerProcess:
        pid = 100

    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *a, **k: WorkerProcess())
    task = manager.submit(name="sample", command=["python", "task.py"])
    manager._update(task["id"], {"status": "running", "phase": "finalizing"},
                    expected_status="queued")

    assert manager.cancel(task["id"])["status"] == "running"
    manager._update(task["id"], {"status": "completed", "phase": "finished"},
                    expected_status="running")
    assert manager._update(task["id"], {"status": "running"})["status"] == "completed"
    assert len(json.loads(task_manager.REGISTRY_FILE.read_text(encoding="utf-8"))) == 1


def test_only_one_worker_can_claim_a_queued_task(manager, monkeypatch):
    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 1})())
    task = manager.submit(name="race", command=["unused"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: manager.claim(task["id"], {"status": "running"}), range(8)))
    assert sum(item is not None for item in claims) == 1

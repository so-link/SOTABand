"""Persistent subprocess task manager.

Tasks are deliberately independent from FastAPI request lifetimes.  The worker
owns the actual command and writes status to disk, so the UI can safely poll
after an Agent reply or a backend restart.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from core.resource.registry.json_store import update_json_list
from core.runtime.paths import TASK_REGISTRY_FILE as REGISTRY_FILE
from core.runtime.paths import TASK_RUNS_DIR as RUNS_DIR
from core.runtime.paths import TASKS_DIR as TASK_DIR


class TaskManager:
    """Create, inspect, and cancel durable subprocess tasks."""

    def __init__(self) -> None:
        TASK_DIR.mkdir(parents=True, exist_ok=True)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        if not REGISTRY_FILE.exists():
            update_json_list(REGISTRY_FILE, lambda tasks: None)

    def _read(self) -> list[dict[str, Any]]:
        try:
            return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, tasks: list[dict[str, Any]]) -> None:
        REGISTRY_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

    def _update(
        self, task_id: str, updates: dict[str, Any], *,
        expected_status: str | None = None, not_finalizing: bool = False,
    ) -> dict[str, Any] | None:
        def change(tasks):
            for task in tasks:
                if task.get("id") == task_id:
                    current = task.get("status")
                    if (expected_status is not None and current != expected_status
                            or not_finalizing and task.get("phase") == "finalizing"
                            or current in {"completed", "failed", "cancelled"}):
                        return dict(task)
                    task.update(updates)
                    task["updated_at"] = _now()
                    return dict(task)
            return None

        return update_json_list(REGISTRY_FILE, change)

    def submit(
        self,
        *,
        name: str,
        command: list[str],
        kind: str = "tool",
        metadata: dict[str, Any] | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        if not command:
            raise ValueError("task command cannot be empty")

        task_id = f"task-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        task_dir = RUNS_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=False)
        log_path = task_dir / "task.log"
        task = {
            "id": task_id,
            "name": name,
            "kind": kind,
            "status": "queued",
            "progress": 0,
            "message": "任务已提交，等待启动",
            "command": command,
            "cwd": cwd or "",
            "log_path": str(log_path),
            "metadata": metadata or {},
            "created_at": _now(),
            "updated_at": _now(),
            "started_at": None,
            "finished_at": None,
            "pid": None,
            "worker_pid": None,
            "command_pid": None,
            "phase": "queued",
            "return_code": None,
        }
        update_json_list(REGISTRY_FILE, lambda tasks: tasks.append(task))

        worker_command = [sys.executable, "-m", "core.task.worker", task_id]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                worker_command,
                cwd=str(Path(__file__).resolve().parents[2]),
                creationflags=creationflags,
                close_fds=os.name != "nt",
            )
        except OSError as exc:
            self._update(task_id, {
                "status": "failed", "phase": "finished", "progress": 100,
                "message": f"无法启动任务工作进程: {exc}",
                "finished_at": _now(), "return_code": -1,
            }, expected_status="queued")
            raise RuntimeError(f"无法启动任务工作进程: {exc}") from exc
        return self._update(task_id, {
            "pid": process.pid, "worker_pid": process.pid,
        }) or task

    def claim(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Atomically claim queued work; a losing worker receives no task."""
        def change(tasks):
            for task in tasks:
                if task.get("id") == task_id and task.get("status") == "queued":
                    task.update(updates)
                    task["updated_at"] = _now()
                    return dict(task)
            return None
        return update_json_list(REGISTRY_FILE, change)

    def get(self, task_id: str) -> dict[str, Any] | None:
        task = next((item for item in self._read() if item.get("id") == task_id), None)
        if task is None:
            return None
        return self._with_live_progress(task)

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        tasks = self._read()
        return [self._with_live_progress(task) for task in tasks[-limit:]][::-1]

    def cancel(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.get("status") not in {"queued", "running"}:
            return task
        cancelled = self._update(task_id, {
            "status": "cancelled", "phase": "finished", "progress": 100,
            "message": "任务已取消", "finished_at": _now(),
        }, expected_status=task["status"], not_finalizing=True)
        if not cancelled or cancelled.get("status") != "cancelled":
            return cancelled
        worker_pid = cancelled.get("worker_pid") or cancelled.get("pid")
        command_pid = cancelled.get("command_pid")
        if worker_pid or command_pid:
            try:
                if os.name == "nt":
                    target = worker_pid or command_pid
                    subprocess.run(["taskkill", "/PID", str(target), "/T", "/F"], check=False,
                                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    if command_pid:
                        os.killpg(int(command_pid), 15)
                    if worker_pid:
                        os.kill(int(worker_pid), 15)
            except (OSError, ProcessLookupError):
                pass
        from core.task.handlers import get_task_handler
        handler = get_task_handler(str(cancelled.get("kind", "")))
        if handler:
            handler.on_cancel(cancelled, task_id)
        return cancelled

    def _with_live_progress(self, task: dict[str, Any]) -> dict[str, Any]:
        log_path = Path(task.get("log_path", ""))
        if not log_path.exists():
            return task
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        except OSError:
            return task
        task = dict(task)
        task["log_tail"] = tail[-3000:]
        if task.get("status") == "running":
            epoch_matches = re.findall(r"epoch=(\d+)", tail)
            epochs = int(task.get("metadata", {}).get("epochs") or 0)
            if epoch_matches and epochs > 0:
                epoch = int(epoch_matches[-1])
                task["progress"] = min(99, max(int(task.get("progress", 1)), int(epoch * 100 / epochs)))
                task["message"] = f"训练中: epoch {epoch}/{epochs}"
        return task


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

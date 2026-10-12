"""Worker entry point used by :mod:`core.task.task_manager`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.task.handlers import get_task_handler
from core.task.task_manager import TaskManager, _now


def main(task_id: str) -> int:
    manager = TaskManager()
    task = manager.get(task_id)
    if task is None:
        return 2
    if task.get("status") != "queued":
        return 0

    handler = get_task_handler(str(task.get("kind", "")))
    log_path = Path(task["log_path"])
    claimed = manager.claim(task_id, {
        "status": "running", "phase": "starting", "progress": 1,
        "message": "任务运行中", "started_at": _now(),
    })
    if not claimed or claimed.get("status") != "running":
        return 0
    if handler:
        try:
            handler.on_start(task, task_id)
        except Exception as exc:  # noqa: BLE001 - task hooks may fail with any plugin error
            manager._update(task_id, {"status": "failed", "phase": "finished", "progress": 100,
                                       "message": f"任务启动处理失败: {exc}",
                                       "finished_at": _now(), "return_code": -1}, expected_status="running")
            handler.on_failure(task, task_id)
            return 1
    if (manager.get(task_id) or {}).get("status") == "cancelled":
        if handler:
            handler.on_cancel(task, task_id)
        return 0
    command_env = handler.process_environment() if handler and hasattr(handler, "process_environment") else None
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(
                task["command"], cwd=task.get("cwd") or None, stdout=log, env=command_env,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=sys.platform != "win32",
            )
            manager._update(task_id, {"command_pid": process.pid, "phase": "executing"},
                            expected_status="running")
            if (manager.get(task_id) or {}).get("status") == "cancelled":
                process.terminate()
            return_code = process.wait()
    except OSError as exc:
        manager._update(task_id, {
            "status": "failed", "phase": "finished", "progress": 100,
            "message": f"无法启动任务: {exc}",
            "finished_at": _now(), "return_code": -1,
        }, expected_status="running")
        if handler:
            handler.on_failure(task, task_id)
        return 1

    latest = manager.get(task_id)
    if latest and latest.get("status") == "cancelled":
        return return_code

    if return_code == 0:
        claimed = manager._update(task_id, {"phase": "finalizing", "message": "正在校验任务产物"},
                                  expected_status="running")
        if not claimed or claimed.get("status") != "running":
            return 0
        artifacts = {}
        if handler:
            try:
                artifacts = handler.on_success(task, task_id) or {}
            except Exception as exc:  # noqa: BLE001 - task hooks may fail with any plugin error
                manager._update(task_id, {"status": "failed", "phase": "finished", "progress": 100,
                                           "message": f"任务完成处理失败: {exc}",
                                           "finished_at": _now()}, expected_status="running")
                handler.on_failure(task, task_id)
                return 1
        latest_task = manager.get(task_id) or task
        manager._update(task_id, {
            "status": "completed", "phase": "finished", "progress": 100, "message": "任务完成",
            "finished_at": _now(), "return_code": return_code,
            "metadata": {**(latest_task.get("metadata") or {}), **artifacts},
        }, expected_status="running")
    else:
        manager._update(task_id, {
            "status": "failed", "phase": "finished", "progress": 100,
            "message": f"任务失败，退出码 {return_code}",
            "finished_at": _now(), "return_code": return_code,
        }, expected_status="running")
        if handler:
            handler.on_failure(task, task_id)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))

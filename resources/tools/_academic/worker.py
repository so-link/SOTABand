"""Optional scheduler owned by the new tools, independent of FastAPI."""

import argparse
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .common import ROOT, Store, ToolError, uid


def tick(store, subscriptions=True):
    from .papers import sync_subscription
    from .reproduction import TERMINAL, advance

    errors = []
    kind = "subscription" if subscriptions else "job"
    for record in store.find(kind, 100000):
        if worker_state(store).get("stop_requested"):
            break
        try:
            if subscriptions:
                if record.get("enabled") and record.get("next_run", 0) <= time.time():
                    sync_subscription(
                        store, record, cancel=lambda: worker_state(store).get("stop_requested")
                    )
            elif record["state"] not in TERMINAL:
                advance(store, record["id"])
        except Exception as exc:  # noqa: BLE001 - One failed task must not stop other scheduled work.
            errors.append({"id": record["id"], "error": str(exc)})
            if subscriptions:
                try:
                    current = store.get(kind, record["id"])
                    current.update({"next_run": time.time() + 300, "last_error": str(exc)})
                    store.put(kind, current, expected=current["revision"])
                except ToolError:
                    pass
    return errors


def worker_state(store):
    try:
        return store.get("worker", "main")
    except ToolError as exc:
        if exc.code != "NOT_FOUND":
            raise
        return {"id": "main", "heartbeat": 0}


def control(mode, store):
    with store.lock("worker-control", wait=5):
        state = worker_state(store)
        running = time.time() - state.get("heartbeat", 0) < 45
        if mode == "worker_status":
            return {**state, "alive": running}
        if mode == "worker_stop":
            state["stop_requested"] = True
            return store.put("worker", state)
        if running:
            return {**state, "alive": True}
        launch_id = uid("worker")
        store.put(
            "worker",
            {
                "id": "main",
                "stop_requested": False,
                "heartbeat": time.time(),
                "launch_id": launch_id,
            },
        )
        path = store.home / "worker.log"
        env = {
            **os.environ,
            "SOTABAND_ACADEMIC_DATA": str(store.root),
            "SOTABAND_USER_ID": store.owner,
            "PYTHONIOENCODING": "utf-8",
            "SOTABAND_ACADEMIC_WORKER_ID": launch_id,
        }
        options = (
            {"start_new_session": True}
            if os.name != "nt"
            else {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS}
        )
        with path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "resources.tools._academic.worker"],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                **options,
            )
        # Windows venv launchers may spawn a different PID for the actual interpreter.
        return {
            "launcher_pid": process.pid,
            "launch_id": launch_id,
            "log_path": str(path),
            "state": "starting",
        }


def serve(store, once=False):
    with store.lock("scheduler", seconds=60):
        done = threading.Event()

        def heartbeat():
            while not done.is_set():
                with store.lock("worker-control", wait=5):
                    state = worker_state(store)
                    state.update(
                        {
                            "heartbeat": time.time(),
                            "pid": os.getpid(),
                            "launch_id": os.getenv(
                                "SOTABAND_ACADEMIC_WORKER_ID", state.get("launch_id")
                            ),
                        }
                    )
                    store.put("worker", state)
                done.wait(10)

        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = None
                while not worker_state(store).get("stop_requested"):
                    errors = tick(store, subscriptions=False)
                    if future is None or future.done():
                        if future:
                            errors.extend(future.result())
                        future = pool.submit(tick, store)
                    with store.lock("worker-control", wait=5):
                        state = worker_state(store)
                        state["errors"] = errors
                        store.put("worker", state)
                    if once:
                        future.result()
                        break
                    time.sleep(10)
        finally:
            done.set()
            pulse.join(timeout=35)
            with store.lock("worker-control", wait=5):
                state = worker_state(store)
                state["heartbeat"] = 0
                store.put("worker", state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    serve(Store(), args.once)

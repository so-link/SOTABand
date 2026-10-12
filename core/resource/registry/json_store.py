"""Atomic cross-process updates for the local JSON resource registries."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def update_json_list(path: Path, mutate: Callable[[list[dict]], T]) -> T:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            if not isinstance(data, list):
                raise TypeError(f"registry is not a list: {path}")
            result = mutate(data)
            fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as temp:
                    json.dump(data, temp, ensure_ascii=False, indent=2)
                    temp.flush()
                    os.fsync(temp.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return result
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

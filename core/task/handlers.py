"""Lifecycle hooks for task kinds with domain-specific side effects."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol


class TaskHandler(Protocol):
    def on_start(self, task: dict[str, Any], task_id: str) -> None: ...

    def on_success(self, task: dict[str, Any], task_id: str) -> dict[str, Any]: ...

    def on_failure(self, task: dict[str, Any], task_id: str) -> None: ...

    def on_cancel(self, task: dict[str, Any], task_id: str) -> None: ...


_HANDLERS: dict[str, TaskHandler] = {}
_BUILTINS_LOADED = False


def register_task_handler(kind: str, handler: TaskHandler) -> None:
    if not kind or kind in _HANDLERS:
        raise ValueError(f"task handler already registered or kind is empty: {kind}")
    _HANDLERS[kind] = handler


def get_task_handler(kind: str) -> TaskHandler | None:
    global _BUILTINS_LOADED
    if not _BUILTINS_LOADED:
        import_module("resources.workflows.task_handlers")
        _BUILTINS_LOADED = True
    return _HANDLERS.get(kind)

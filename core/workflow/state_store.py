"""Small session state store used by workflow adapters during migration."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class SessionStateStore:
    def __init__(self):
        self._states: dict[str, dict[str, Any]] = defaultdict(dict)

    def get(self, session_id: str) -> dict[str, Any]:
        return self._states[session_id]

    def reset(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    def clear(self) -> None:
        self._states.clear()

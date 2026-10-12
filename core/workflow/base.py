"""Stable contract between the platform Agent and domain workflows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Any


@dataclass
class WorkflowContext:
    session_id: str
    user_id: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowAdapter(ABC):
    """A domain workflow that can be mounted by the platform Agent."""

    workflow_id: str

    @abstractmethod
    def detect(self, message: str, context: WorkflowContext) -> bool:
        """Return whether this workflow owns the message."""

    @abstractmethod
    async def handle(
        self,
        message: str,
        context: WorkflowContext,
        state: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield platform events such as content, card, error, and done."""
        if False:
            yield {}

    def reset(self, state: dict[str, Any]) -> None:
        """Clear domain state for a session."""
        state.clear()

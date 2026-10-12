"""Runtime registry for domain workflow adapters."""

from __future__ import annotations

from collections.abc import Iterable

from .base import WorkflowAdapter, WorkflowContext


class WorkflowRegistry:
    def __init__(self, adapters: Iterable[WorkflowAdapter] | None = None):
        self._adapters: dict[str, WorkflowAdapter] = {}
        self._owners: dict[str, str] = {}
        for adapter in adapters or ():
            self.register(adapter)

    def register(self, adapter: WorkflowAdapter) -> WorkflowAdapter:
        workflow_id = str(adapter.workflow_id).strip()
        if not workflow_id:
            raise ValueError("workflow adapter must define workflow_id")
        if workflow_id in self._adapters:
            raise ValueError(f"workflow already registered: {workflow_id}")
        self._adapters[workflow_id] = adapter
        return adapter

    def get(self, workflow_id: str) -> WorkflowAdapter | None:
        return self._adapters.get(workflow_id)

    def list(self) -> list[WorkflowAdapter]:
        return list(self._adapters.values())

    def for_tool(self, tool_id: str) -> WorkflowAdapter | None:
        return next((adapter for adapter in self.list()
                     if tool_id in getattr(adapter, "tool_ids", ())), None)

    def activate(self, session_id: str, workflow_id: str) -> None:
        if workflow_id not in self._adapters:
            raise ValueError(f"unknown workflow: {workflow_id}")
        self._owners[session_id or "default"] = workflow_id

    def release(self, session_id: str) -> None:
        self._owners.pop(session_id or "default", None)

    def augment_candidates(self, message, candidates, active):
        result = list(candidates)
        for adapter in self.list():
            hook = getattr(adapter, "suggest_tools", None)
            if hook:
                for tool in hook(message, active):
                    if not any(item["id"] == tool["id"] for item in result):
                        result.append(tool)
        return result

    def match(self, message: str, context: WorkflowContext) -> WorkflowAdapter | None:
        key = context.session_id or "default"
        owner_id = self._owners.get(key)
        for adapter in self._adapters.values():
            if adapter.workflow_id != owner_id and getattr(adapter, "detect_explicit", lambda *_: False)(message, context):
                self.activate(key, adapter.workflow_id)
                return adapter
        if owner_id:
            owner = self._adapters[owner_id]
            if owner.detect(message, context):
                return owner
            self.release(key)
        for adapter in self._adapters.values():
            if adapter.detect(message, context):
                self.activate(key, adapter.workflow_id)
                return adapter
        return None

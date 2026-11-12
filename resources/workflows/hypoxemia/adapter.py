"""Production workflow plugin for the low-oxygen domain."""
from core.workflow.base import WorkflowAdapter
from core.workflow.host import WorkflowHost

from .conversation import HypoxemiaConversation
from .state import HypoxemiaState


class HypoxemiaWorkflowAdapter(HypoxemiaConversation, HypoxemiaState, WorkflowAdapter):
    tool_ids = ("timeseries-classification-runner",)
    def __init__(self, profile=None, *, host: WorkflowHost | None = None):
        super().__init__(profile)
        if host is None:
            from resources.agents.implementations.interactive_agent.agent import interactive_agent
            from resources.workflows.host import AgentWorkflowHost
            host = AgentWorkflowHost(interactive_agent)
        self.host = host
        self._timeseries_workflows = self.workflow_states
        self._pending_timeseries_choice = self.pending_choices
        self._pending_timeseries_input = self.pending_inputs
        self._pending_calls = {}
        self._pending_modes = {}
        self._pending_confirm = {}
        from core.timeseries import (
            JsonModelCatalog,
            JsonResultStore,
            LocalArtifactStore,
            LocalDatasetProcessor,
            TimeSeriesWorkflowService,
        )

        from .service_components import HypoxemiaEvaluator, HypoxemiaTrainer
        self.timeseries_service = TimeSeriesWorkflowService(
            dataset_processor=LocalDatasetProcessor(), model_catalog=JsonModelCatalog(),
            trainer=HypoxemiaTrainer(), evaluator=HypoxemiaEvaluator(), artifact_store=LocalArtifactStore(),
            result_store=JsonResultStore(),
        )

    def suggest_tools(self, message, active):
        if any(word in message for word in ("训练", "评测模型", "评估模型", "数据集")):
            return [tool for tool in active if tool["id"] in self.tool_ids]
        return []

    def detect(self, message, context):
        key = context.session_id or "default"
        active = any(key in mapping for mapping in (
            self._pending_calls, self._pending_modes, self.pending_choices, self.pending_inputs,
        ))
        explicit = self._resolve_timeseries_workflow_intent(message, False, session_key=key)
        if explicit:
            return True
        if key in self.workflow_states and any(token in message for token in (
            "新的结构", "新结构", "模型", "训练", "评测", "参数", "继续", "数据集",
        )):
            return True
        # A completed workflow does not capture unrelated platform requests.
        return active or bool(self.workflow_states.get(key) and message.strip() in {
            "继续", "开始训练", "开始评测", "评测", "取消", "退出",
        })

    def detect_explicit(self, message, context):
        normalized = message.strip().lower()
        return any(marker in normalized for marker in (
            "低氧", "血氧", "spo2", "hypoxemia", "hypoxaemia", "五通道", "256x5",
        ))

    def reset_session(self, session_id):
        super().reset_session(session_id)
        for mapping in (self._pending_calls, self._pending_modes, self._pending_confirm):
            mapping.pop(session_id or "default", None)

    async def handle(self, message, context, state):
        if self.host is None:
            raise RuntimeError("workflow host is required for execution")
        self.state_for(context.session_id).update(state)
        async for event in self._execute_impl(
            context, message, context.metadata.get("attachments", []),
            context.metadata.get("workspace_tool_ids", []),
        ):
            yield event

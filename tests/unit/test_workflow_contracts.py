import unittest

from core.runtime.paths import (
    DATA_REGISTRY_FILE,
    MODEL_REGISTRY_FILE,
    RUNTIME_ROOT,
    TASK_REGISTRY_FILE,
    TASK_RUNS_DIR,
    TIMESERIES_RUNS_DIR,
)
from core.workflow import SessionStateStore, WorkflowAdapter, WorkflowContext, WorkflowRegistry
from resources.workflows.hypoxemia import HypoxemiaWorkflowAdapter


class EchoWorkflow(WorkflowAdapter):
    workflow_id = "echo"

    def detect(self, message: str, context: WorkflowContext) -> bool:
        return message.strip().lower().startswith("echo")

    async def handle(self, message, context, state):
        state["handled"] = True
        yield {"event": "content", "data": {"text": message}}


class OtherWorkflow(EchoWorkflow):
    workflow_id = "other"

    def detect_explicit(self, message, context):
        return message.startswith("other:")

    def detect(self, message, context):
        return message.startswith("other:") or message == "continue other"


class WorkflowContractTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_matches_adapter(self):
        registry = WorkflowRegistry([EchoWorkflow()])
        context = WorkflowContext(session_id="session-1")

        self.assertIsNotNone(registry.match("echo hello", context))
        self.assertIsNone(registry.match("ordinary message", context))

    def test_explicit_domain_switch_replaces_session_owner(self):
        first = EchoWorkflow()
        second = OtherWorkflow()
        registry = WorkflowRegistry([first, second])
        context = WorkflowContext(session_id="session-1")
        assert registry.match("echo hello", context) is first
        assert registry.match("other: start", context) is second
        assert registry.match("continue other", context) is second
        registry.release("session-1")
        assert registry.match("unrelated", context) is None

    async def test_adapter_and_session_state(self):
        workflow = EchoWorkflow()
        state_store = SessionStateStore()
        state = state_store.get("session-1")
        events = [event async for event in workflow.handle("echo hello", WorkflowContext("session-1"), state)]

        self.assertEqual(events[0]["event"], "content")
        self.assertTrue(state["handled"])
        state_store.reset("session-1")
        self.assertEqual(state_store.get("session-1"), {})

    def test_refactor_runtime_is_isolated_from_baseline(self):
        baseline_root = RUNTIME_ROOT.parents[1] / "resources"

        self.assertNotEqual(TASK_REGISTRY_FILE, baseline_root / "tasks" / "registry.json")
        self.assertNotEqual(TASK_RUNS_DIR, baseline_root / "tasks" / "runs")
        self.assertNotEqual(MODEL_REGISTRY_FILE, baseline_root / "models" / "registry.json")
        self.assertNotEqual(DATA_REGISTRY_FILE, baseline_root / "data" / "registry.json")
        self.assertNotEqual(TIMESERIES_RUNS_DIR, RUNTIME_ROOT.parents[1] / "data" / "spo2-runs")

    def test_hypoxemia_adapter_applies_session_context(self):
        adapter = HypoxemiaWorkflowAdapter()
        adapter.state_for("session").update({
            "dataset_id": "dataset-1", "model_id": "model-1", "task_id": "task-1",
            "processed_dataset_path": "D:/runtime/data", "artifact_path": "D:/runtime/model",
        })
        tool = {"id": adapter.profile.tool_id}
        self.assertEqual(
            adapter.apply_context("session", tool, "train", {}),
            {"dataset": "D:/runtime/data", "model_id": "model-1"},
        )
        self.assertEqual(
            adapter.apply_context("session", tool, "evaluate", {}),
            {
                "model_id": "model-1", "dataset": "D:/runtime/data",
                "task_id": "task-1", "artifact_path": "D:/runtime/model",
                "dataset_id": "dataset-1",
            },
        )

    def test_hypoxemia_adapter_owns_pending_session_state(self):
        adapter = HypoxemiaWorkflowAdapter()
        adapter.pending_choices["session"] = {"stage": "model"}
        adapter.pending_inputs["session"] = {"stage": "training-config"}
        adapter.state_for("session")["model_id"] = "model-1"
        adapter.reset_session("session")
        self.assertNotIn("session", adapter.pending_choices)
        self.assertNotIn("session", adapter.pending_inputs)
        self.assertNotIn("session", adapter.workflow_states)


if __name__ == "__main__":
    unittest.main()

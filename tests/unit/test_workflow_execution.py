"""The mounted low-oxygen workflow uses the service for compute requests."""

import json

import pytest
from pathlib import Path

from core.executor.tool_executor import ToolExecutor
from core.timeseries.components import JsonResultStore
from core.timeseries import DatasetRef, EvaluationRequest, ModelRef
from resources.workflows.hypoxemia.adapter import HypoxemiaWorkflowAdapter


@pytest.mark.asyncio
async def test_training_and_evaluation_use_service_with_provenance(tmp_path, monkeypatch):
    calls = []

    async def execute(_cls, tool_id, params, **kwargs):
        calls.append((tool_id, dict(params)))
        if params["mode"] == "train":
            return {"status": "success", "data": {"task_request": {"kind": "hypoxemia-training"}}}
        return {"status": "success", "card_type": "evaluation-result",
                "data": {"report_path": "report.json", "selected_metrics": {"f1": 0.8}}}

    monkeypatch.setattr(ToolExecutor, "execute", classmethod(execute))
    adapter = HypoxemiaWorkflowAdapter()
    adapter.timeseries_service.result_store = JsonResultStore(tmp_path)
    tool = {"id": "timeseries-classification-runner"}

    training = await adapter._execute_tool(tool, "train", pre_params={
        "mode": "train", "dataset": "dataset-1", "model_id": "model-1",
    })
    evaluation = await adapter._execute_tool(tool, "evaluate", pre_params={
        "mode": "evaluate", "dataset": "dataset-1", "dataset_id": "dataset-1",
        "model_id": "model-1", "task_id": "task-1", "metrics": ["f1"],
    })

    assert training["data"]["task_request"]["kind"] == "hypoxemia-training"
    assert [call[1]["mode"] for call in calls] == ["train", "evaluate"]
    assert calls[1][1]["metrics"] == ["f1"]
    stored = json.loads((tmp_path / "results" / evaluation["data"]["result_id"] / "result.json").read_text(encoding="utf-8"))
    assert stored["metadata"]["model_id"] == "model-1"
    assert stored["metadata"]["dataset_id"] == "dataset-1"
    assert stored["metadata"]["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_model_ref_artifact_path_reaches_evaluation_tool(monkeypatch):
    captured = {}

    async def execute(_cls, _tool_id, params, **kwargs):
        captured.update(params)
        return {"status": "failed", "message": "mocked"}

    monkeypatch.setattr(ToolExecutor, "execute", classmethod(execute))
    adapter = HypoxemiaWorkflowAdapter()
    await adapter.timeseries_service.evaluate(EvaluationRequest(
        DatasetRef(id="data-1", path=Path("dataset")),
        ModelRef(id="model-1", artifact_path=Path("selected-artifact")),
    ))

    assert captured["artifact_path"] == "selected-artifact"
    assert captured["dataset_id"] == "data-1"

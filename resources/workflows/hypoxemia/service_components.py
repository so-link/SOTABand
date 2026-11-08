"""Hypoxemia bindings for the domain-neutral time-series service."""

from __future__ import annotations

from typing import Any

from core.executor.tool_executor import ToolExecutor

TOOL_ID = "timeseries-classification-runner"


class HypoxemiaTrainer:
    async def submit(self, request, **kwargs: Any):
        params = {**request.config, **request.metadata, "mode": "train"}
        params.setdefault("dataset", request.dataset.id or str(request.dataset.path or ""))
        params.setdefault("model_id", request.model.id)
        return await ToolExecutor.execute(TOOL_ID, params)


class HypoxemiaEvaluator:
    async def evaluate(self, request, **kwargs: Any):
        params = {**request.config, **request.metadata, "mode": "evaluate"}
        params.setdefault("dataset", request.dataset.id or str(request.dataset.path or ""))
        if request.dataset.id:
            params.setdefault("dataset_id", request.dataset.id)
        params.setdefault("model_id", request.model.id)
        if request.model.artifact_path:
            params.setdefault("artifact_path", str(request.model.artifact_path))
        if request.metrics:
            params["metrics"] = list(request.metrics)
        return await ToolExecutor.execute(TOOL_ID, params)

    async def predict(self, request, **kwargs: Any):
        params = {**request.config, "mode": "predict",
                  "dataset": request.dataset.id or str(request.dataset.path or ""),
                  "model_id": request.model.id}
        if request.model.artifact_path:
            params.setdefault("artifact_path", str(request.model.artifact_path))
        return await ToolExecutor.execute(TOOL_ID, params)

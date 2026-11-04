"""Composable service boundary for reusable time-series capabilities.

This class deliberately delegates to injected components.  It is the stable
entry point for domain adapters; data processing, training and evaluation can
be migrated behind it one component at a time.
"""

from __future__ import annotations

import inspect
from typing import Any

from .contracts import EvaluationRequest, PredictionRequest, TrainingRequest


class TimeSeriesWorkflowService:
    def __init__(
        self,
        *,
        dataset_processor: Any,
        model_catalog: Any,
        trainer: Any,
        evaluator: Any,
        artifact_store: Any,
        result_store: Any,
    ):
        self.dataset_processor = dataset_processor
        self.model_catalog = model_catalog
        self.trainer = trainer
        self.evaluator = evaluator
        self.artifact_store = artifact_store
        self.result_store = result_store

    def validate_dataset(self, dataset, **kwargs):
        return self.dataset_processor.validate(dataset, **kwargs)

    def list_models(self, **kwargs):
        return self.model_catalog.list_models(**kwargs)

    def create_model(self, spec, **kwargs):
        return self.model_catalog.create(spec, **kwargs)

    async def submit_training(self, request: TrainingRequest, **kwargs):
        result = self.trainer.submit(request, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def evaluate(self, request: EvaluationRequest, **kwargs):
        result = self.evaluator.evaluate(request, **kwargs)
        result = await result if inspect.isawaitable(result) else result
        if not isinstance(result, dict) or result.get("status") != "success" or result.get("card_type") != "evaluation-result":
            return result
        data = result.get("data") or {}
        saved = self.save_result(
            data,
            model_id=data.get("model_resource_id") or request.model.id,
            dataset_id=data.get("dataset_id") or request.dataset.id,
            task_id=data.get("task_id") or request.metadata.get("task_id", ""),
            tool_id=request.metadata.get("tool_id", ""),
            report_path=data.get("report_path", ""),
        )
        return {**result, "data": {**data, "result_id": saved["result_id"],
                                    "result_path": saved["path"]}}

    async def predict(self, request: PredictionRequest, **kwargs):
        result = self.evaluator.predict(request, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def save_result(self, result, *, model_id="", dataset_id="", task_id="", **metadata):
        """Persist an externally computed result with traceable provenance."""
        return self.result_store.save(
            result,
            model_id=model_id,
            dataset_id=dataset_id,
            task_id=task_id,
            **metadata,
        )

    def load_result(self, result_id: str):
        return self.result_store.load(result_id)

    def list_results(self, **filters):
        return self.result_store.list(**filters) if hasattr(self.result_store, "list") else []

    def save_artifact(self, artifact, **kwargs):
        return self.artifact_store.save(artifact, **kwargs)

    def load_artifact(self, model, **kwargs):
        return self.artifact_store.load(model, **kwargs)

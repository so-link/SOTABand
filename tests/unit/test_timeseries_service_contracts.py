import unittest

from core.timeseries import (
    DatasetRef,
    EvaluationRequest,
    ModelRef,
    PredictionRequest,
    TimeSeriesWorkflowService,
    TrainingRequest,
)


class Recorder:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.value

        return call


class TimeSeriesServiceContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.processor = Recorder("dataset-report")
        self.catalog = Recorder("model-list")
        self.trainer = Recorder("task-ref")
        self.evaluator = Recorder("evaluation")
        self.artifacts = Recorder("artifact")
        self.results = Recorder("stored-result")
        self.service = TimeSeriesWorkflowService(
            dataset_processor=self.processor,
            model_catalog=self.catalog,
            trainer=self.trainer,
            evaluator=self.evaluator,
            artifact_store=self.artifacts,
            result_store=self.results,
        )
        self.dataset = DatasetRef(id="dataset-1")
        self.model = ModelRef(id="model-1", name="SegRNN")

    async def test_delegates_core_operations(self):
        self.assertEqual(self.service.validate_dataset(self.dataset), "dataset-report")
        self.assertEqual(self.service.list_models(), "model-list")
        self.assertEqual(
            await self.service.submit_training(TrainingRequest(self.dataset, self.model)),
            "task-ref",
        )
        self.assertEqual(
            await self.service.evaluate(EvaluationRequest(self.dataset, self.model)),
            "evaluation",
        )
        self.assertEqual(
            await self.service.predict(PredictionRequest(self.dataset, self.model)),
            "evaluation",
        )

        self.assertEqual(self.trainer.calls[0][0], "submit")
        self.assertEqual(self.evaluator.calls[0][0], "evaluate")
        self.assertEqual(self.evaluator.calls[1][0], "predict")
        self.assertEqual(self.results.calls, [])


if __name__ == "__main__":
    unittest.main()

"""Composition root for workflow task handlers."""

from core.task.handlers import register_task_handler
from resources.workflows.hypoxemia.task_handler import (
    HypoxemiaDatasetTaskHandler,
    HypoxemiaTrainingTaskHandler,
)

training = HypoxemiaTrainingTaskHandler()
processing = HypoxemiaDatasetTaskHandler()
register_task_handler("hypoxemia-training", training)
register_task_handler("hypoxemia-data-processing", processing)
register_task_handler("timeseries-training", training)
register_task_handler("timeseries-data-processing", processing)

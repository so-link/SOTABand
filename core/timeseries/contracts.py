"""Domain-neutral request and resource contracts for time-series workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetRef:
    id: str = ""
    path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRef:
    id: str = ""
    name: str = ""
    artifact_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingRequest:
    dataset: DatasetRef
    model: ModelRef
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationRequest:
    dataset: DatasetRef
    model: ModelRef
    metrics: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionRequest:
    dataset: DatasetRef
    model: ModelRef
    config: dict[str, Any] = field(default_factory=dict)

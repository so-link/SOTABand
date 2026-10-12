from __future__ import annotations

import copy
import importlib
import inspect
import json
import pickle
import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, Dataset


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TSLIB_ROOT = PROJECT_DIR / "vendor" / "Time-Series-Library"
TSLIB_IMPORT_PREFIXES = ("models", "layers", "utils", "data_provider", "exp")

# FreTS references classification in __init__, but its forward path only supports forecasting.
TSLIB_CLASSIFICATION_MODEL_NAMES = ('Autoformer', 'Crossformer', 'DLinear', 'ETSformer', 'FEDformer', 'FiLM', 'Informer', 'KANAD', 'LightTS', 'MICN', 'MSGNet', 'MambaSingleLayer', 'MultiPatchFormer', 'Nonstationary_Transformer', 'PatchTST', 'Pyraformer', 'Reformer', 'SegRNN', 'SegRNNClsPool', 'SegRNNDeepHead', 'SegRNNDiffFusion', 'SegRNNDiffTimeMixerGate', 'SegRNNDualScale', 'SegRNNDualScaleV2', 'SegRNNHybridHead', 'SegRNNMambaFusion', 'SegRNNNumericQualityAware', 'SegRNNResidualAux', 'SegRNNRichFusion', 'SegRNNTimeMixerFusion', 'SegRNNTimeMixerGate', 'SegRNNTrendResidualFusion', 'SegRNNiTransformerFusion', 'SegRNNiTransformerGate', 'TiDE', 'TimeFilter', 'TimeMixer', 'TimeMixerClsPool', 'TimeMixeriTransformerFusion', 'TimesNet', 'Transformer', 'TransformerClsPool', 'WPMixer', 'iTransformer')

MODEL_DEFAULT_OVERRIDES: dict[str, dict[str, Any]] = {
    "TimeMixer": {
        "channel_independence": 0,
        "down_sampling_layers": 2,
        "down_sampling_window": 2,
        "down_sampling_method": "avg",
        "use_norm": 1,
    },
    "TimeMixerClsPool": {
        "channel_independence": 0,
        "down_sampling_layers": 2,
        "down_sampling_window": 2,
        "down_sampling_method": "avg",
        "use_norm": 1,
    },
    "MambaSingleLayer": {
        "expand": 2,
        "d_conv": 4,
        "tv_dt": 0,
        "tv_B": 0,
        "tv_C": 0,
        "use_D": 0,
        "num_kernels": 6,
    },
    "SegRNN": {
        "seg_len": 16,
    },
    "SegRNNClsPool": {
        "seg_len": 16,
    },
    "SegRNNDeepHead": {
        "seg_len": 16,
    },
    "SegRNNDiffFusion": {
        "seg_len": 32,
    },
    "SegRNNDiffTimeMixerGate": {
        "seg_len": 32,
        "channel_independence": 0,
        "down_sampling_layers": 2,
        "down_sampling_window": 2,
        "down_sampling_method": "avg",
        "use_norm": 1,
    },
    "SegRNNHybridHead": {
        "seg_len": 16,
    },
    "SegRNNTimeMixerFusion": {
        "seg_len": 32,
        "channel_independence": 0,
        "down_sampling_layers": 2,
        "down_sampling_window": 2,
        "down_sampling_method": "avg",
        "use_norm": 1,
    },
    "SegRNNiTransformerFusion": {
        "seg_len": 32,
    },
    "SegRNNiTransformerGate": {
        "seg_len": 32,
    },
    "SegRNNMambaFusion": {
        "seg_len": 32,
        "expand": 2,
        "d_conv": 4,
        "tv_dt": 0,
        "tv_B": 0,
        "tv_C": 0,
        "use_D": 0,
        "num_kernels": 6,
    },
    "SegRNNPlethFusion": {
        "seg_len": 32,
    },
    "SegRNNParamBranchFusion": {
        "seg_len": 32,
    },
    "SegRNNParamBranchGatedFusion": {
        "seg_len": 32,
    },
    "SegRNNParamBranchQualityAwareFusion": {
        "seg_len": 32,
    },
    "SegRNNPlethLateFusion": {
        "seg_len": 32,
    },
    "SegRNNPlethGatedFusion": {
        "seg_len": 32,
    },
    "SegRNNPlethQualityAwareFusion": {
        "seg_len": 32,
    },
    "SegRNNResidualAux": {
        "seg_len": 16,
    },
    "SegRNNRichFusion": {
        "seg_len": 32,
    },
    "SegRNNNumericQualityAware": {
        "seg_len": 32,
    },
    "SegRNNParamPlethTriFusion": {
        "seg_len": 32,
    },
    "SegRNNParamPlethTriQualityAwareFusion": {
        "seg_len": 32,
    },
    "SegRNNTrendResidualFusion": {
        "seg_len": 32,
    },
    "SegRNNDualScale": {
        "seg_len": 16,
    },
    "SegRNNDualScaleV2": {
        "seg_len": 32,
    },
    "SegRNNTimeMixerGate": {
        "seg_len": 32,
        "channel_independence": 0,
        "down_sampling_layers": 2,
        "down_sampling_window": 2,
        "down_sampling_method": "avg",
        "use_norm": 1,
    },
    "TimeMixeriTransformerFusion": {
        "channel_independence": 0,
        "down_sampling_layers": 2,
        "down_sampling_window": 2,
        "down_sampling_method": "avg",
        "use_norm": 1,
    },
}

CONSTRUCTOR_DEFAULTS: dict[str, dict[str, Any]] = {
    "DLinear": {"individual": False},
    "FEDformer": {"version": "fourier", "mode_select": "random", "modes": 32},
    "LightTS": {"chunk_size": 24},
    "MICN": {"conv_kernel": [12, 16]},
    "PatchTST": {"patch_len": 16, "stride": 8},
    "Pyraformer": {"window_size": [4, 4], "inner_size": 5},
    "Reformer": {"bucket_size": 4, "n_hashes": 4},
    "TiDE": {"bias": True, "feature_encode_dim": 2},
}


class WindowDataset(Dataset):
    def __init__(
        self,
        x: np.ndarray,
        y: np.ndarray,
        mask: np.ndarray,
        aux_x: np.ndarray | None = None,
        aux_mask: np.ndarray | None = None,
        aux2_x: np.ndarray | None = None,
        aux2_mask: np.ndarray | None = None,
    ):
        self.x = torch.from_numpy(np.ascontiguousarray(x)).float()
        self.y = torch.from_numpy(np.asarray(y, dtype=np.int64))
        self.mask = torch.from_numpy(np.ascontiguousarray(mask)).float()
        self.aux_x = None
        self.aux_mask = None
        self.aux2_x = None
        self.aux2_mask = None
        if aux_x is not None:
            self.aux_x = torch.from_numpy(np.ascontiguousarray(aux_x)).float()
        if aux_mask is not None:
            self.aux_mask = torch.from_numpy(np.ascontiguousarray(aux_mask)).float()
        if aux2_x is not None:
            self.aux2_x = torch.from_numpy(np.ascontiguousarray(aux2_x)).float()
        if aux2_mask is not None:
            self.aux2_mask = torch.from_numpy(np.ascontiguousarray(aux2_mask)).float()

    def __len__(self) -> int:
        return len(self.y)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, ...]:
        if self.aux_x is None:
            return self.x[index], self.y[index], self.mask[index]
        if self.aux_mask is None:
            raise ValueError("aux_x was provided without aux_mask")
        if self.aux2_x is None:
            return self.x[index], self.y[index], self.mask[index], self.aux_x[index], self.aux_mask[index]
        if self.aux2_mask is None:
            raise ValueError("aux2_x was provided without aux2_mask")
        return (
            self.x[index],
            self.y[index],
            self.mask[index],
            self.aux_x[index],
            self.aux_mask[index],
            self.aux2_x[index],
            self.aux2_mask[index],
        )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_project_path(path_like: str | Path) -> Path:
    path = Path(path_like)
    if path.is_absolute():
        return path
    return (PROJECT_DIR / path).resolve()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if torch.backends.cudnn.is_available():
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def load_window_dataset(data_path, info_path):
    """Read trusted windows, rejecting all shape/schema mismatches before splitting."""
    data_path = resolve_project_path(data_path)
    info_path = resolve_project_path(info_path)
    expected = ["SpO2", "HR", "PR", "RR", "PI"]
    declarations = []
    for name in ("dataset_manifest.json", "window_stats.json"):
        path = data_path.parent / name
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            # Older processed SpO2 datasets omit sampling_hz. The pipeline
            # contract is 1 Hz, so an omitted field uses that default while an
            # explicitly incompatible rate is still rejected.
            rate = value.get("sampling_hz", 1)
            length = value.get("sequence_length", value.get("lookback_seconds"))
            if rate != 1 or not isinstance(length, int) or length <= 0:
                raise ValueError("dataset must declare sampling_hz=1 and a positive integer window length")
            if value.get("feature_order", expected) != expected:
                raise ValueError("dataset feature order is incompatible")
            declarations.append(length)
    if not declarations or len(set(declarations)) != 1:
        raise ValueError("missing or inconsistent dataset window declarations")
    expected_len = declarations[0]
    with data_path.open("rb") as handle:
        windows = pickle.load(handle)
    with info_path.open("rb") as handle:
        metadata = pickle.load(handle)
    if len(windows) != len(metadata) or not len(metadata):
        raise ValueError("window count and metadata count must agree and be nonzero")
    series, labels = [], []
    for index, (window, item) in enumerate(zip(windows, metadata)):
        names = item.get("features")
        if names not in (expected, ["Time", *expected]):
            raise ValueError(f"window {index} feature order is incompatible: {names}")
        array = np.asarray(window)
        if array.shape != (expected_len, len(names)):
            raise ValueError(f"window {index} shape {array.shape} does not match declared {(expected_len, len(names))}")
        values = array[:, 1:] if names[0] == "Time" else array
        values = np.asarray(values, dtype=np.float32)
        if not np.isfinite(values).all():
            raise ValueError(f"window {index} contains nonfinite values; run raw preprocessing first")
        label = item.get("sample_type")
        if label not in {"hypoxemia", "normal", "non_hypoxemia"}:
            raise ValueError(f"window {index} has unknown label {label!r}")
        series.append(values)
        labels.append(int(label == "hypoxemia"))
    x = np.stack(series)
    return x, np.asarray(labels, dtype=np.int64), np.ones(x.shape[:2], dtype=np.float32), expected, list(metadata)


def _safe_stratify(labels: np.ndarray) -> np.ndarray | None:
    counts = np.bincount(np.asarray(labels, dtype=int))
    if len(counts) < 2 or np.any(counts < 2):
        return None
    return labels


def _metadata_groups(window_info: list[dict[str, Any]], group_key: str) -> tuple[np.ndarray, int]:
    groups: list[str] = []
    missing_count = 0
    for index, item in enumerate(window_info):
        value = item.get(group_key)
        if value is None or value == "":
            value = f"__row_{index}"
            missing_count += 1
        groups.append(str(value))
    return np.asarray(groups), missing_count


def _group_labels(grouped_indices: list[np.ndarray], y: np.ndarray) -> np.ndarray:
    labels = []
    for indices in grouped_indices:
        labels.append(int(np.mean(y[indices]) >= 0.5))
    return np.asarray(labels, dtype=np.int64)


def split_dataset_grouped_indices(
    y: np.ndarray,
    window_info: list[dict[str, Any]],
    *,
    validation_size: float = 0.15,
    test_size: float = 0.15,
    random_state: int = 42,
    group_key: str = "group_id",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    holdout_size = validation_size + test_size
    if not 0 < holdout_size < 1:
        raise ValueError("validation_size + test_size must be between 0 and 1")

    groups, missing_count = _metadata_groups(window_info, group_key)
    if missing_count:
        raise ValueError("grouped split requires group_id for every window")
    unique_values, inverse = np.unique(groups, return_inverse=True)
    grouped_indices = [np.where(inverse == group_index)[0] for group_index in range(len(unique_values))]
    if len(unique_values) < 3:
        raise ValueError(f"grouped split requires at least 3 groups, got {len(unique_values)}")

    group_labels = _group_labels(grouped_indices, y)
    group_indices = np.arange(len(unique_values))
    train_group_idx, holdout_group_idx = train_test_split(
        group_indices,
        test_size=holdout_size,
        stratify=_safe_stratify(group_labels),
        random_state=random_state,
    )

    holdout_labels = group_labels[holdout_group_idx]
    relative_test_size = test_size / holdout_size
    val_group_idx, test_group_idx = train_test_split(
        holdout_group_idx,
        test_size=relative_test_size,
        stratify=_safe_stratify(holdout_labels),
        random_state=random_state,
    )

    def flatten(selected_group_idx: np.ndarray) -> np.ndarray:
        indices = np.concatenate([grouped_indices[group_index] for group_index in selected_group_idx])
        return np.sort(indices)

    train_idx = flatten(train_group_idx)
    val_idx = flatten(val_group_idx)
    test_idx = flatten(test_group_idx)
    split_report = {
        "validation_size": validation_size, "test_size": test_size, "seed": random_state,
        "split_mode": "grouped",
        "group_key": group_key,
        "missing_group_count": int(missing_count),
        "unique_group_count": int(len(unique_values)),
        "train_group_count": int(len(train_group_idx)),
        "val_group_count": int(len(val_group_idx)),
        "test_group_count": int(len(test_group_idx)),
        "train_positive_rate": float(np.mean(y[train_idx])),
        "val_positive_rate": float(np.mean(y[val_idx])),
        "test_positive_rate": float(np.mean(y[test_idx])),
    }
    return train_idx, val_idx, test_idx, split_report


def split_dataset_random_indices(
    y: np.ndarray,
    *,
    validation_size: float = 0.15,
    test_size: float = 0.15,
    random_state: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    holdout_size = validation_size + test_size
    if not 0 < holdout_size < 1:
        raise ValueError("validation_size + test_size must be between 0 and 1")

    indices = np.arange(len(y))
    train_idx, holdout_idx = train_test_split(
        indices,
        test_size=holdout_size,
        stratify=y,
        random_state=random_state,
    )
    relative_test_size = test_size / holdout_size
    val_idx, test_idx = train_test_split(
        holdout_idx,
        test_size=relative_test_size,
        stratify=y[holdout_idx],
        random_state=random_state,
    )
    split_report = {
        "validation_size": validation_size, "test_size": test_size, "seed": random_state,
        "split_mode": "random_window",
        "group_key": None,
        "train_positive_rate": float(np.mean(y[train_idx])),
        "val_positive_rate": float(np.mean(y[val_idx])),
        "test_positive_rate": float(np.mean(y[test_idx])),
    }
    return np.sort(train_idx), np.sort(val_idx), np.sort(test_idx), split_report


def standardize_splits(
    x_train: np.ndarray,
    x_val: np.ndarray,
    x_test: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, StandardScaler]:
    scaler = StandardScaler()

    def flatten(values: np.ndarray) -> np.ndarray:
        return values.reshape(-1, values.shape[-1])

    scaler.fit(flatten(x_train))

    def transform(values: np.ndarray) -> np.ndarray:
        return scaler.transform(flatten(values)).reshape(values.shape).astype(np.float32)

    return transform(x_train), transform(x_val), transform(x_test), scaler


def create_data_loader(
    x: np.ndarray,
    y: np.ndarray,
    mask: np.ndarray,
    *,
    batch_size: int,
    shuffle: bool,
    seed: int,
    aux_x: np.ndarray | None = None,
    aux_mask: np.ndarray | None = None,
    aux2_x: np.ndarray | None = None,
    aux2_mask: np.ndarray | None = None,
) -> DataLoader:
    dataset = WindowDataset(
        x,
        y,
        mask,
        aux_x=aux_x,
        aux_mask=aux_mask,
        aux2_x=aux2_x,
        aux2_mask=aux2_mask,
    )
    generator = torch.Generator()
    generator.manual_seed(seed)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=0,
        pin_memory=torch.cuda.is_available(),
        generator=generator if shuffle else None,
    )


def forward_classification_model(
    model: torch.nn.Module,
    features: torch.Tensor,
    mask: torch.Tensor,
    aux_features: torch.Tensor | None = None,
    aux_mask: torch.Tensor | None = None,
    aux2_features: torch.Tensor | None = None,
    aux2_mask: torch.Tensor | None = None,
) -> torch.Tensor:
    if aux2_features is not None:
        if hasattr(model, "forward_multibranch"):
            return model.forward_multibranch(features, mask, aux_features, aux_mask, aux2_features, aux2_mask)
        raise ValueError(f"model {type(model).__name__} does not support a second auxiliary branch")
    return model(features, mask, aux_features, aux_mask)


def positive_class_weight(y_train: np.ndarray) -> float:
    positives = int(np.sum(y_train))
    negatives = int(len(y_train) - positives)
    if positives <= 0:
        raise ValueError("training split has no positive samples")
    return negatives / positives


def classification_report_text(y_true: np.ndarray, y_pred: np.ndarray) -> str:
    return classification_report(
        y_true,
        y_pred,
        target_names=["non-hypoxemia", "hypoxemia"],
        digits=4,
        zero_division=0,
    )


def safe_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    try:
        return float(roc_auc_score(y_true, y_score))
    except ValueError:
        return float("nan")


def summarize_binary_split(y_true: np.ndarray, y_score: np.ndarray, threshold: float) -> dict[str, Any]:
    y_pred = (y_score > threshold).astype(int)
    return {
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "auc": safe_auc(y_true, y_score),
    }


def metric_value(y_true: np.ndarray, y_score: np.ndarray, threshold: float, metric: str) -> float:
    y_pred = (y_score > threshold).astype(int)
    if metric == "f1":
        return float(f1_score(y_true, y_pred, zero_division=0))
    if metric == "accuracy":
        return float(accuracy_score(y_true, y_pred))
    sensitivity = recall_score(y_true, y_pred, zero_division=0)
    specificity = recall_score(1 - y_true, 1 - y_pred, zero_division=0)
    return float((sensitivity + specificity) / 2.0)


def tie_break_value(y_true: np.ndarray, y_score: np.ndarray, threshold: float, tie_breaker: str) -> float:
    if tie_breaker == "metric":
        return 0.0
    if tie_breaker == "threshold":
        return float(threshold)

    y_pred = (y_score > threshold).astype(int)
    if tie_breaker == "precision":
        return float(precision_score(y_true, y_pred, zero_division=0))
    if tie_breaker == "recall":
        return float(recall_score(y_true, y_pred, zero_division=0))
    return float(accuracy_score(y_true, y_pred))


def satisfies_constraints(
    y_true: np.ndarray,
    y_score: np.ndarray,
    threshold: float,
    *,
    min_recall: float | None = None,
    min_precision: float | None = None,
) -> bool:
    y_pred = (y_score > threshold).astype(int)
    if min_recall is not None and recall_score(y_true, y_pred, zero_division=0) < min_recall:
        return False
    if min_precision is not None and precision_score(y_true, y_pred, zero_division=0) < min_precision:
        return False
    return True


def build_threshold_report(
    y_val_true: np.ndarray,
    y_val_score: np.ndarray,
    y_test_true: np.ndarray,
    y_test_score: np.ndarray,
    *,
    selection_metric: str = "f1",
    min_recall: float | None = None,
    min_precision: float | None = None,
    tie_tolerance: float = 0.0,
    tie_breaker: str = "metric",
) -> tuple[float, dict[str, Any]]:
    thresholds = np.linspace(0.05, 0.95, 181)
    feasible_thresholds = [
        float(threshold)
        for threshold in thresholds
        if satisfies_constraints(
            y_val_true,
            y_val_score,
            float(threshold),
            min_recall=min_recall,
            min_precision=min_precision,
        )
    ]
    if not feasible_thresholds:
        raise ValueError("no validation threshold satisfies the requested constraints")

    metric_scores = {
        threshold: metric_value(y_val_true, y_val_score, threshold, selection_metric)
        for threshold in feasible_thresholds
    }
    best_metric = max(metric_scores.values())
    near_best_thresholds = [
        threshold
        for threshold in feasible_thresholds
        if metric_scores[threshold] >= best_metric - tie_tolerance
    ]
    best_threshold = max(
        near_best_thresholds,
        key=lambda threshold: (
            tie_break_value(y_val_true, y_val_score, threshold, tie_breaker),
            metric_scores[threshold],
        ),
    )
    report = {
        "selection_metric": selection_metric,
        "min_recall": min_recall,
        "min_precision": min_precision,
        "tie_tolerance": tie_tolerance,
        "tie_breaker": tie_breaker,
        "validation": summarize_binary_split(y_val_true, y_val_score, best_threshold),
        "test": summarize_binary_split(y_test_true, y_test_score, best_threshold),
        "test_at_0_5": summarize_binary_split(y_test_true, y_test_score, 0.5),
    }
    return float(best_threshold), report


def make_test_metrics_payload(y_true: np.ndarray, y_score: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    y_pred = (y_score > threshold).astype(int)
    return {
        "threshold": float(threshold),
        "auc_roc": safe_auc(y_true, y_score),
        "classification_report": classification_report_text(y_true, y_pred),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
    }


def softmax_positive_scores(logits: torch.Tensor) -> torch.Tensor:
    if logits.ndim != 2 or logits.shape[1] != 2:
        raise ValueError(f"expected logits with shape [batch, 2], got {tuple(logits.shape)}")
    return torch.softmax(logits, dim=1)[:, 1]


def unpack_classification_batch(
    batch: tuple[torch.Tensor, ...],
) -> tuple[
    torch.Tensor,
    torch.Tensor,
    torch.Tensor,
    torch.Tensor | None,
    torch.Tensor | None,
]:
    if len(batch) == 7:
        features, labels, mask, aux_features, aux_mask, aux2_features, aux2_mask = batch
        return features, labels, mask, (aux_features, aux_mask), (aux2_features, aux2_mask)
    if len(batch) == 5:
        features, labels, mask, aux_features, aux_mask = batch
        return features, labels, mask, (aux_features, aux_mask), None
    features, labels, mask = batch
    return features, labels, mask, None, None


def evaluate_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    criterion: torch.nn.Module,
    device: torch.device,
) -> dict[str, Any]:
    total_loss = 0.0
    batch_count = 0
    all_labels: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []

    model.eval()
    with torch.no_grad():
        for batch in loader:
            features, labels, mask, aux_bundle, aux2_bundle = unpack_classification_batch(batch)
            aux_features = None if aux_bundle is None else aux_bundle[0]
            aux_mask = None if aux_bundle is None else aux_bundle[1]
            aux2_features = None if aux2_bundle is None else aux2_bundle[0]
            aux2_mask = None if aux2_bundle is None else aux2_bundle[1]
            features = features.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            mask = mask.to(device, non_blocking=True)
            if aux_features is not None:
                aux_features = aux_features.to(device, non_blocking=True)
            if aux_mask is not None:
                aux_mask = aux_mask.to(device, non_blocking=True)
            if aux2_features is not None:
                aux2_features = aux2_features.to(device, non_blocking=True)
            if aux2_mask is not None:
                aux2_mask = aux2_mask.to(device, non_blocking=True)

            logits = forward_classification_model(
                model,
                features,
                mask,
                aux_features,
                aux_mask,
                aux2_features,
                aux2_mask,
            )
            loss = criterion(logits, labels)

            total_loss += float(loss.item())
            batch_count += 1
            all_labels.append(labels.detach().cpu().numpy())
            all_scores.append(softmax_positive_scores(logits).detach().cpu().numpy())

    y_true = np.concatenate(all_labels)
    y_score = np.concatenate(all_scores)
    metrics = summarize_binary_split(y_true, y_score, 0.5)
    metrics["loss"] = float(total_loss / max(batch_count, 1))
    metrics["y_true"] = y_true
    metrics["y_score"] = y_score
    return metrics


def default_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def parse_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    text = text.strip("[]")
    return [int(part.strip()) for part in text.split(",") if part.strip()]


def maybe_none_string(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return None
    return value


def override_or_default(overrides: dict[str, Any], key: str, default: Any) -> Any:
    value = overrides.get(key, default)
    if value is None:
        return default
    return value


def build_tslib_config(
    model_name: str,
    *,
    seq_len: int,
    enc_in: int,
    num_class: int,
    device: torch.device,
    overrides: dict[str, Any],
) -> SimpleNamespace:
    args: dict[str, Any] = {
        "task_name": "classification",
        "model": model_name,
        "seq_len": int(seq_len),
        "label_len": int(override_or_default(overrides, "label_len", seq_len // 2)),
        "pred_len": int(override_or_default(overrides, "pred_len", 0)),
        "enc_in": int(override_or_default(overrides, "enc_in", enc_in)),
        "dec_in": int(override_or_default(overrides, "dec_in", enc_in)),
        "c_out": int(override_or_default(overrides, "c_out", enc_in)),
        "aux_enc_in": int(override_or_default(overrides, "aux_enc_in", enc_in)),
        "aux2_enc_in": int(override_or_default(overrides, "aux2_enc_in", enc_in)),
        "num_class": int(num_class),
        "d_model": int(override_or_default(overrides, "d_model", 128)),
        "n_heads": int(override_or_default(overrides, "n_heads", 4)),
        "e_layers": int(override_or_default(overrides, "e_layers", 3)),
        "d_layers": int(override_or_default(overrides, "d_layers", 3)),
        "d_ff": int(override_or_default(overrides, "d_ff", 256)),
        "dropout": float(override_or_default(overrides, "dropout", 0.1)),
        "activation": str(override_or_default(overrides, "activation", "gelu")),
        "embed": str(override_or_default(overrides, "embed", "fixed")),
        "freq": str(override_or_default(overrides, "freq", "h")),
        "factor": int(override_or_default(overrides, "factor", 1)),
        "distil": coerce_bool(override_or_default(overrides, "distil", True)),
        "top_k": int(override_or_default(overrides, "top_k", 3)),
        "num_kernels": int(override_or_default(overrides, "num_kernels", 6)),
        "moving_avg": int(override_or_default(overrides, "moving_avg", 25)),
        "individual": coerce_bool(override_or_default(overrides, "individual", False)),
        "channel_independence": int(override_or_default(overrides, "channel_independence", 0)),
        "down_sampling_layers": int(override_or_default(overrides, "down_sampling_layers", 0)),
        "down_sampling_window": int(override_or_default(overrides, "down_sampling_window", 1)),
        "down_sampling_method": maybe_none_string(override_or_default(overrides, "down_sampling_method", None)),
        "use_norm": int(override_or_default(overrides, "use_norm", 1)),
        "decomp_method": str(override_or_default(overrides, "decomp_method", "moving_avg")),
        "seg_len": int(override_or_default(overrides, "seg_len", 16)),
        "p_hidden_dims": parse_int_list(override_or_default(overrides, "p_hidden_dims", [128, 128])),
        "p_hidden_layers": int(override_or_default(overrides, "p_hidden_layers", 2)),
        "alpha": float(override_or_default(overrides, "alpha", 0.1)),
        "top_p": float(override_or_default(overrides, "top_p", 0.5)),
        "pos": int(override_or_default(overrides, "pos", 1)),
        "node_dim": int(override_or_default(overrides, "node_dim", 10)),
        "gcn_depth": int(override_or_default(overrides, "gcn_depth", 2)),
        "propalpha": float(override_or_default(overrides, "propalpha", 0.3)),
        "conv_channel": int(override_or_default(overrides, "conv_channel", 32)),
        "skip_channel": int(override_or_default(overrides, "skip_channel", 32)),
        "subgraph_size": int(override_or_default(overrides, "subgraph_size", 5)),
        "ratio": float(override_or_default(overrides, "ratio", 0.5)),
        "expand": int(override_or_default(overrides, "expand", 2)),
        "d_conv": int(override_or_default(overrides, "d_conv", 4)),
        "tv_dt": int(override_or_default(overrides, "tv_dt", 0)),
        "tv_B": int(override_or_default(overrides, "tv_B", 0)),
        "tv_C": int(override_or_default(overrides, "tv_C", 0)),
        "use_D": int(override_or_default(overrides, "use_D", 0)),
        "aux_branch_dim": int(override_or_default(overrides, "aux_branch_dim", max(32, enc_in * 4))),
        "wave_branch_dim": int(override_or_default(overrides, "wave_branch_dim", 128)),
        "patch_len": int(override_or_default(overrides, "patch_len", 16)),
        "stride": int(override_or_default(overrides, "stride", 8)),
        "device": device,
    }

    for key, value in MODEL_DEFAULT_OVERRIDES.get(model_name, {}).items():
        args[key] = copy.deepcopy(value)

    for key, value in overrides.items():
        if key in args and value is not None:
            if key == "p_hidden_dims":
                args[key] = parse_int_list(value)
            elif key in {
                "individual",
                "distil",
            }:
                args[key] = coerce_bool(value)
            elif key == "down_sampling_method":
                args[key] = maybe_none_string(value)
            else:
                args[key] = value

    return SimpleNamespace(**args)


def validate_tslib_config(model_name: str, config: SimpleNamespace) -> None:
    # In the current TSLib MambaSL classification implementation, d_ff is passed
    # directly to Mamba_TimeVariant as d_state. The installed selective_scan CUDA
    # kernel supports d_state <= 256 only, so reject impossible configs early.
    if model_name in {"MambaSingleLayer", "SegRNNMambaFusion"} and int(config.d_ff) > 256:
        raise ValueError(
            f"{model_name} uses d_ff as Mamba d_state, but the current selective_scan "
            f"kernel only supports d_state <= 256. Got d_ff={config.d_ff}."
        )


def prepare_tslib_imports(tslib_root: str | Path) -> Path:
    tslib_root = Path(tslib_root).resolve()
    for module_name in list(sys.modules):
        if module_name == "scripts":
            continue
        if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in TSLIB_IMPORT_PREFIXES):
            del sys.modules[module_name]

    root_text = str(tslib_root)
    if root_text in sys.path:
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)
    return tslib_root


def instantiate_tslib_model(
    model_name: str,
    config: SimpleNamespace,
    *,
    tslib_root: str | Path,
) -> torch.nn.Module:
    validate_tslib_config(model_name, config)
    prepare_tslib_imports(tslib_root)
    from scripts.tslib_local_models import LOCAL_TSLIB_MODEL_REGISTRY

    model_cls = LOCAL_TSLIB_MODEL_REGISTRY.get(model_name)
    if model_cls is None:
        module = importlib.import_module(f"models.{model_name}")
        model_cls = getattr(module, "Model")

    signature = inspect.signature(model_cls)
    kwargs: dict[str, Any] = {}
    use_positional_config = True
    constructor_defaults = CONSTRUCTOR_DEFAULTS.get(model_name, {})
    for parameter_name, parameter in signature.parameters.items():
        if parameter_name == "self":
            continue
        if parameter_name == "configs":
            continue
        if parameter_name == "args":
            kwargs[parameter_name] = config
            use_positional_config = False
            continue
        if hasattr(config, parameter_name):
            kwargs[parameter_name] = copy.deepcopy(getattr(config, parameter_name))
            continue
        if parameter_name in constructor_defaults:
            kwargs[parameter_name] = copy.deepcopy(constructor_defaults[parameter_name])
            continue
        if parameter.default is inspect._empty:
            raise ValueError(
                f"constructor for {model_name} requires argument {parameter_name!r}, but no value was resolved"
            )
    if use_positional_config:
        return model_cls(config, **kwargs)
    return model_cls(**kwargs)


def resolve_tslib_revision(tslib_root):
    source = json.loads((Path(tslib_root) / "SOURCE.json").read_text(encoding="utf-8"))
    return source["delivery_version"] + ":" + source["comparison_commit"]


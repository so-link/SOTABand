from __future__ import annotations

import argparse
import copy
import csv
import json
import pickle
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.optim import Adam, AdamW, RMSprop, SGD
from torch.optim.lr_scheduler import ReduceLROnPlateau

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

def infer_dataset_id(data_path, **kwargs):
    root = Path(data_path).resolve().parent
    manifest = root / "dataset_manifest.json"
    if manifest.is_file():
        return json.loads(manifest.read_text(encoding="utf-8")).get("dataset_id") or root.name
    return root.name
from scripts.tslib_classification_common import (
    DEFAULT_TSLIB_ROOT,
    TSLIB_CLASSIFICATION_MODEL_NAMES,
    build_threshold_report,
    build_tslib_config,
    create_data_loader,
    default_device,
    evaluate_epoch,
    instantiate_tslib_model,
    load_window_dataset,
    make_test_metrics_payload,
    positive_class_weight,
    resolve_project_path,
    resolve_tslib_revision,
    set_seed,
    softmax_positive_scores,
    split_dataset_grouped_indices,
    split_dataset_random_indices,
    standardize_splits,
    summarize_binary_split,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one V2-grouped TSLib classification trial.")
    parser.add_argument("--trial-dir", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--order-index", type=int, default=0)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-name", required=True, choices=TSLIB_CLASSIFICATION_MODEL_NAMES)
    parser.add_argument("--label", default=None)
    parser.add_argument("--description", default="")
    parser.add_argument("--tslib-root", default=str(DEFAULT_TSLIB_ROOT))

    parser.add_argument("--data-path", required=True)
    parser.add_argument("--info-path", required=True)
    parser.add_argument("--aux-data-path", default=None)
    parser.add_argument("--aux-mask-path", default=None)
    parser.add_argument("--wave-data-path", default=None)
    parser.add_argument("--wave-mask-path", default=None)
    parser.add_argument("--wave-quality-path", default=None)
    parser.add_argument("--split-mode", choices=["grouped", "random_window"], default="grouped")
    parser.add_argument("--group-key", default="group_id")
    parser.add_argument("--validation-size", type=float, default=0.15)
    parser.add_argument("--test-size", type=float, default=0.15)

    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--optimizer", choices=["adamw", "adam", "sgd", "rmsprop"], default="adamw")
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--pos-weight-scale", type=float, default=1.0)
    parser.add_argument("--use-ema", action="store_true", default=False)
    parser.add_argument("--ema-momentum", type=float, default=0.99)
    parser.add_argument("--label-smoothing", type=float, default=0.0)
    parser.add_argument("--grad-clip-norm", type=float, default=4.0)
    parser.add_argument("--teacher-probs-path", default=None)
    parser.add_argument("--distill-hard-weight", type=float, default=0.7)
    parser.add_argument("--distill-teacher-weight", type=float, default=0.3)
    parser.add_argument("--contrastive-weight", type=float, default=0.0)
    parser.add_argument("--contrastive-temperature", type=float, default=0.1)
    parser.add_argument("--init-model-state-path", default=None)
    parser.add_argument("--init-backbone-state-path", default=None)
    parser.add_argument("--freeze-backbone-epochs", type=int, default=0)
    parser.add_argument("--early-stop-patience", type=int, default=20)
    parser.add_argument("--early-stop-start-epoch", type=int, default=20)
    parser.add_argument("--reduce-lr-patience", type=int, default=5)
    parser.add_argument("--reduce-lr-factor", type=float, default=0.5)
    parser.add_argument("--min-lr", type=float, default=1e-5)
    parser.add_argument("--selection-monitor", choices=["auc", "accuracy", "loss"], default="auc")
    parser.add_argument("--lr-monitor", choices=["auc", "accuracy", "loss"], default="loss")
    parser.add_argument("--threshold-metric", choices=["f1", "accuracy", "balanced"], default="f1")
    parser.add_argument("--hard-sample-margin", type=float, default=0.1)
    parser.add_argument("--hard-sample-top-k", type=int, default=100)
    parser.add_argument("--verbose", type=int, default=1)

    parser.add_argument("--d-model", type=int, default=128)
    parser.add_argument("--n-heads", type=int, default=4)
    parser.add_argument("--e-layers", type=int, default=3)
    parser.add_argument("--d-layers", type=int, default=3)
    parser.add_argument("--d-ff", type=int, default=256)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--activation", default="gelu")
    parser.add_argument("--embed", default="fixed")
    parser.add_argument("--freq", default="h")
    parser.add_argument("--factor", type=int, default=1)
    parser.add_argument("--distil", action="store_true", default=True)
    parser.add_argument("--no-distil", dest="distil", action="store_false")
    parser.add_argument("--label-len", type=int, default=None)
    parser.add_argument("--pred-len", type=int, default=None)
    parser.add_argument("--patch-len", type=int, default=16)
    parser.add_argument("--stride", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--num-kernels", type=int, default=6)
    parser.add_argument("--moving-avg", type=int, default=25)
    parser.add_argument("--individual", action="store_true", default=False)
    parser.add_argument("--channel-independence", type=int, default=0)
    parser.add_argument("--down-sampling-layers", type=int, default=0)
    parser.add_argument("--down-sampling-window", type=int, default=1)
    parser.add_argument("--down-sampling-method", default=None)
    parser.add_argument("--use-norm", type=int, default=1)
    parser.add_argument("--decomp-method", default="moving_avg")
    parser.add_argument("--seg-len", type=int, default=16)
    parser.add_argument("--p-hidden-dims", default="128,128")
    parser.add_argument("--p-hidden-layers", type=int, default=2)
    parser.add_argument("--alpha", type=float, default=0.1)
    parser.add_argument("--top-p", type=float, default=0.5)
    parser.add_argument("--pos", type=int, default=1)
    parser.add_argument("--node-dim", type=int, default=10)
    parser.add_argument("--gcn-depth", type=int, default=2)
    parser.add_argument("--propalpha", type=float, default=0.3)
    parser.add_argument("--conv-channel", type=int, default=32)
    parser.add_argument("--skip-channel", type=int, default=32)
    parser.add_argument("--subgraph-size", type=int, default=5)
    parser.add_argument("--ratio", type=float, default=0.5)
    parser.add_argument("--expand", type=int, default=2)
    parser.add_argument("--d-conv", type=int, default=4)
    parser.add_argument("--tv-dt", type=int, default=0)
    parser.add_argument("--tv-B", type=int, default=0)
    parser.add_argument("--tv-C", type=int, default=0)
    parser.add_argument("--use-D", type=int, default=0)
    parser.add_argument("--dec-in", type=int, default=None)
    parser.add_argument("--c-out", type=int, default=None)
    parser.add_argument("--enc-in", type=int, default=None)
    parser.add_argument("--aux-enc-in", type=int, default=None)
    parser.add_argument("--aux2-enc-in", type=int, default=None)
    return parser.parse_args()


def to_jsonable_dict(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, Path):
            normalized[key] = str(value)
        elif isinstance(value, torch.device):
            normalized[key] = str(value)
        else:
            normalized[key] = value
    return normalized


def monitor_mode(metric_name: str) -> str:
    return "min" if metric_name == "loss" else "max"


def monitor_value(metrics: dict[str, Any], metric_name: str) -> float:
    value = float(metrics[metric_name])
    if np.isnan(value):
        return float("inf") if metric_name == "loss" else float("-inf")
    return value


def build_optimizer(name: str, parameters: Any, lr: float, weight_decay: float):
    normalized = str(name).lower()
    if normalized == "adam":
        return Adam(parameters, lr=lr, weight_decay=weight_decay)
    if normalized == "sgd":
        return SGD(parameters, lr=lr, weight_decay=weight_decay, momentum=0.9)
    if normalized == "rmsprop":
        return RMSprop(parameters, lr=lr, weight_decay=weight_decay, momentum=0.9)
    return AdamW(parameters, lr=lr, weight_decay=weight_decay)


def is_improved(current_value: float, best_value: float | None, mode: str) -> bool:
    if best_value is None:
        return True
    if mode == "min":
        return current_value < best_value
    return current_value > best_value


class ModelEma:
    def __init__(self, model: nn.Module, momentum: float):
        self.momentum = float(momentum)
        self.shadow_state = {
            key: value.detach().clone()
            for key, value in model.state_dict().items()
        }
        self.backup_state: dict[str, torch.Tensor] | None = None

    def update(self, model: nn.Module) -> None:
        with torch.no_grad():
            current_state = model.state_dict()
            for key, shadow_value in self.shadow_state.items():
                current_value = current_state[key].detach()
                if shadow_value.is_floating_point():
                    shadow_value.lerp_(current_value, 1.0 - self.momentum)
                else:
                    shadow_value.copy_(current_value)

    def state_dict(self) -> dict[str, torch.Tensor]:
        return {key: value.detach().clone() for key, value in self.shadow_state.items()}

    def load_state_dict(self, payload: dict[str, torch.Tensor]) -> None:
        self.shadow_state = {
            key: value.detach().clone()
            for key, value in payload.items()
        }

    def store(self, model: nn.Module) -> None:
        self.backup_state = {
            key: value.detach().clone()
            for key, value in model.state_dict().items()
        }

    def copy_to(self, model: nn.Module) -> None:
        model.load_state_dict(self.shadow_state, strict=True)

    def restore(self, model: nn.Module) -> None:
        if self.backup_state is None:
            return
        model.load_state_dict(self.backup_state, strict=True)
        self.backup_state = None


class DistillationWindowDataset(torch.utils.data.Dataset):
    def __init__(
        self,
        x: np.ndarray,
        y: np.ndarray,
        mask: np.ndarray,
        teacher_probs: np.ndarray | None = None,
        aux_x: np.ndarray | None = None,
        aux_mask: np.ndarray | None = None,
        aux2_x: np.ndarray | None = None,
        aux2_mask: np.ndarray | None = None,
    ) -> None:
        self.x = torch.from_numpy(np.ascontiguousarray(x)).float()
        self.y = torch.from_numpy(np.asarray(y, dtype=np.int64))
        self.mask = torch.from_numpy(np.ascontiguousarray(mask)).float()
        self.teacher_probs = None
        self.aux_x = None
        self.aux_mask = None
        self.aux2_x = None
        self.aux2_mask = None
        if teacher_probs is not None:
            self.teacher_probs = torch.from_numpy(np.asarray(teacher_probs, dtype=np.float32))
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
        aux_items: tuple[torch.Tensor, ...] = ()
        if self.aux_x is not None:
            if self.aux_mask is None:
                raise ValueError("aux_x was provided without aux_mask")
            aux_items = (self.aux_x[index], self.aux_mask[index])
            if self.aux2_x is not None:
                if self.aux2_mask is None:
                    raise ValueError("aux2_x was provided without aux2_mask")
                aux_items = aux_items + (self.aux2_x[index], self.aux2_mask[index])
        if self.teacher_probs is None:
            return self.x[index], self.y[index], self.mask[index], *aux_items
        return self.x[index], self.y[index], self.mask[index], self.teacher_probs[index], *aux_items


def create_training_loader(
    x: np.ndarray,
    y: np.ndarray,
    mask: np.ndarray,
    *,
    batch_size: int,
    shuffle: bool,
    seed: int,
    teacher_probs: np.ndarray | None = None,
    aux_x: np.ndarray | None = None,
    aux_mask: np.ndarray | None = None,
    aux2_x: np.ndarray | None = None,
    aux2_mask: np.ndarray | None = None,
) -> torch.utils.data.DataLoader:
    dataset = DistillationWindowDataset(
        x,
        y,
        mask,
        teacher_probs=teacher_probs,
        aux_x=aux_x,
        aux_mask=aux_mask,
        aux2_x=aux2_x,
        aux2_mask=aux2_mask,
    )
    generator = torch.Generator()
    generator.manual_seed(seed)
    return torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=0,
        pin_memory=torch.cuda.is_available(),
        generator=generator if shuffle else None,
    )


def load_teacher_splits(
    path: str | Path | None,
    y_train: np.ndarray,
    y_val: np.ndarray,
    y_test: np.ndarray,
) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    if path is None:
        return None, None, None

    teacher_data = np.load(Path(path).resolve())
    required = ("train_teacher", "val_teacher", "test_teacher")
    missing = [key for key in required if key not in teacher_data]
    if missing:
        raise ValueError(f"teacher probability file missing keys: {missing}")

    for key, expected in (("y_train", y_train), ("y_val", y_val), ("y_test", y_test)):
        if key in teacher_data and not np.array_equal(teacher_data[key], expected):
            raise ValueError(
                f"{key} in teacher probability file does not match the current data split. "
                "Regenerate teacher probabilities with the same seed and data paths."
            )

    teacher_train = teacher_data["train_teacher"].astype(np.float32)
    teacher_val = teacher_data["val_teacher"].astype(np.float32)
    teacher_test = teacher_data["test_teacher"].astype(np.float32)
    if len(teacher_train) != len(y_train) or len(teacher_val) != len(y_val) or len(teacher_test) != len(y_test):
        raise ValueError("teacher probability split lengths do not match current split lengths")
    return teacher_train, teacher_val, teacher_test


def load_wave_inputs(
    wave_data_path: str | Path | None,
    wave_mask_path: str | Path | None,
    wave_quality_path: str | Path | None,
    *,
    expected_count: int,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if wave_data_path is None:
        return None, None

    resolved_wave_data_path = resolve_project_path(wave_data_path)
    with resolved_wave_data_path.open("rb") as handle:
        wave_data = pickle.load(handle)

    wave_array = np.asarray(wave_data)
    if wave_array.shape[0] != expected_count:
        raise ValueError(
            f"wave window count {wave_array.shape[0]} does not match numeric window count {expected_count}"
        )

    wave_mask_array = None
    if wave_mask_path is not None:
        resolved_wave_mask_path = resolve_project_path(wave_mask_path)
        with resolved_wave_mask_path.open("rb") as handle:
            wave_mask = pickle.load(handle)
        wave_mask_array = np.asarray(wave_mask, dtype=np.float32)
        if wave_mask_array.shape[0] != expected_count:
            raise ValueError(
                f"wave mask count {wave_mask_array.shape[0]} does not match numeric window count {expected_count}"
            )
    else:
        wave_mask_array = np.ones(wave_array.shape[:2], dtype=np.float32)

    if wave_quality_path is not None:
        resolved_wave_quality_path = resolve_project_path(wave_quality_path)
        with resolved_wave_quality_path.open("rb") as handle:
            wave_quality = pickle.load(handle)
        wave_quality_array = np.asarray(wave_quality, dtype=np.float32)
        if wave_quality_array.shape[0] != expected_count:
            raise ValueError(
                f"wave quality count {wave_quality_array.shape[0]} does not match numeric window count {expected_count}"
            )
        if wave_quality_array.shape[:2] != wave_mask_array.shape[:2]:
            raise ValueError(
                f"wave quality shape {wave_quality_array.shape} does not match wave mask shape {wave_mask_array.shape}"
            )
        if wave_quality_array.ndim == 2:
            wave_quality_array = wave_quality_array[..., None]
        wave_mask_array = np.concatenate([wave_mask_array[..., None], wave_quality_array], axis=-1)

    return wave_array, wave_mask_array


def load_aux_inputs(
    aux_data_path: str | Path | None,
    aux_mask_path: str | Path | None,
    *,
    expected_count: int,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if aux_data_path is None:
        return None, None

    resolved_aux_data_path = resolve_project_path(aux_data_path)
    with resolved_aux_data_path.open("rb") as handle:
        aux_data = pickle.load(handle)
    aux_array = np.asarray(aux_data, dtype=np.float32)
    if aux_array.shape[0] != expected_count:
        raise ValueError(
            f"aux window count {aux_array.shape[0]} does not match numeric window count {expected_count}"
        )

    aux_mask_array = None
    if aux_mask_path is not None:
        resolved_aux_mask_path = resolve_project_path(aux_mask_path)
        with resolved_aux_mask_path.open("rb") as handle:
            aux_mask = pickle.load(handle)
        aux_mask_array = np.asarray(aux_mask, dtype=np.float32)
        if aux_mask_array.shape[0] != expected_count:
            raise ValueError(
                f"aux mask count {aux_mask_array.shape[0]} does not match numeric window count {expected_count}"
            )
    else:
        aux_mask_array = np.ones(aux_array.shape[:2], dtype=np.float32)
    return aux_array, aux_mask_array


def standardize_aux_splits(
    aux_train: np.ndarray,
    aux_val: np.ndarray,
    aux_test: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()

    def flatten(values: np.ndarray) -> np.ndarray:
        return values.reshape(-1, values.shape[-1])

    scaler.fit(flatten(aux_train))

    def transform(values: np.ndarray) -> np.ndarray:
        return scaler.transform(flatten(values)).reshape(values.shape).astype(np.float32)

    return transform(aux_train), transform(aux_val), transform(aux_test)


def unpack_batch(
    batch: tuple[torch.Tensor, ...],
) -> tuple[
    torch.Tensor,
    torch.Tensor,
    torch.Tensor,
    torch.Tensor | None,
    torch.Tensor | None,
    torch.Tensor | None,
    torch.Tensor | None,
    torch.Tensor | None,
]:
    if len(batch) == 8:
        features, labels, mask, teacher_probs, aux_features, aux_mask, aux2_features, aux2_mask = batch
        return features, labels, mask, teacher_probs, aux_features, aux_mask, aux2_features, aux2_mask
    if len(batch) == 7:
        features, labels, mask, aux_features, aux_mask, aux2_features, aux2_mask = batch
        return features, labels, mask, None, aux_features, aux_mask, aux2_features, aux2_mask
    if len(batch) == 6:
        features, labels, mask, teacher_probs, aux_features, aux_mask = batch
        return features, labels, mask, teacher_probs, aux_features, aux_mask, None, None
    if len(batch) == 5:
        features, labels, mask, aux_features, aux_mask = batch
        return features, labels, mask, None, aux_features, aux_mask, None, None
    if len(batch) == 4:
        features, labels, mask, teacher_probs = batch
        return features, labels, mask, teacher_probs, None, None, None, None
    features, labels, mask = batch
    return features, labels, mask, None, None, None, None, None


def forward_model(
    model: nn.Module,
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


def load_backbone_state(model: nn.Module, checkpoint_path: str | Path, device: torch.device) -> dict[str, Any]:
    if not hasattr(model, "backbone"):
        raise ValueError(f"model {type(model).__name__} does not expose a backbone attribute")
    checkpoint = torch.load(Path(checkpoint_path).resolve(), map_location=device, weights_only=False)
    state = checkpoint.get("backbone_state_dict") or checkpoint.get("model_state_dict") or checkpoint
    target_state = model.backbone.state_dict()
    compatible = {}
    skipped = []
    for key, value in state.items():
        candidate_keys = [key]
        if key.startswith("backbone."):
            candidate_keys.append(key[len("backbone."):])
        matched = False
        for candidate_key in candidate_keys:
            if candidate_key in target_state and tuple(target_state[candidate_key].shape) == tuple(value.shape):
                compatible[candidate_key] = value
                matched = True
                break
        if not matched:
            skipped.append(key)
    missing = [key for key in target_state if key not in compatible]
    model.backbone.load_state_dict(compatible, strict=False)
    return {
        "checkpoint_path": str(Path(checkpoint_path).resolve()),
        "loaded_key_count": len(compatible),
        "skipped_key_count": len(skipped),
        "missing_key_count": len(missing),
        "skipped_keys": skipped[:50],
        "missing_keys": missing[:50],
    }


def set_backbone_trainable(model: nn.Module, trainable: bool) -> None:
    if not hasattr(model, "backbone"):
        return
    for parameter in model.backbone.parameters():
        parameter.requires_grad = trainable


def compute_classification_loss(
    logits: torch.Tensor,
    labels: torch.Tensor,
    hard_criterion: nn.Module,
    *,
    teacher_probs: torch.Tensor | None = None,
    distill_hard_weight: float = 0.7,
    distill_teacher_weight: float = 0.3,
) -> torch.Tensor:
    hard_loss = hard_criterion(logits, labels)
    if hard_loss.ndim != 1:
        hard_loss = hard_loss.reshape(hard_loss.shape[0], -1).mean(dim=1)

    if teacher_probs is None or distill_teacher_weight <= 0:
        return hard_loss.mean()

    teacher_probs = teacher_probs.to(logits.device, non_blocking=True).float().clamp_(0.0, 1.0)
    teacher_targets = torch.stack((1.0 - teacher_probs, teacher_probs), dim=1)
    log_probs = F.log_softmax(logits, dim=1)
    teacher_loss = -(teacher_targets * log_probs).sum(dim=1)
    combined = distill_hard_weight * hard_loss + distill_teacher_weight * teacher_loss
    return combined.mean()




def extract_contrastive_embeddings(
    model: nn.Module,
    features: torch.Tensor,
    mask: torch.Tensor,
    logits: torch.Tensor,
    aux_features: torch.Tensor | None = None,
    aux_mask: torch.Tensor | None = None,
    aux2_features: torch.Tensor | None = None,
    aux2_mask: torch.Tensor | None = None,
) -> torch.Tensor:
    if hasattr(model, "forward_contrastive_features"):
        return model.forward_contrastive_features(features, mask, aux_features, aux_mask, aux2_features, aux2_mask)
    if aux_features is None and aux2_features is None and hasattr(model, "backbone"):
        backbone = getattr(model, "backbone")
        if hasattr(backbone, "forward_features"):
            return backbone.forward_features(features, mask)
    return logits


def supervised_contrastive_loss(
    embeddings: torch.Tensor,
    labels: torch.Tensor,
    *,
    temperature: float = 0.1,
) -> torch.Tensor:
    if embeddings.size(0) < 2:
        return embeddings.new_zeros(())
    temperature = max(float(temperature), 1e-6)
    labels = labels.reshape(-1)
    embeddings = embeddings.reshape(embeddings.size(0), -1).float()
    embeddings = F.normalize(embeddings, dim=1)

    similarity = torch.matmul(embeddings, embeddings.T) / temperature
    similarity = similarity - similarity.max(dim=1, keepdim=True).values.detach()
    self_mask = torch.eye(labels.size(0), device=labels.device, dtype=torch.bool)
    positive_mask = labels.unsqueeze(0).eq(labels.unsqueeze(1)) & ~self_mask
    valid_anchor = positive_mask.any(dim=1)
    if not bool(valid_anchor.any()):
        return embeddings.new_zeros(())

    denominator_mask = ~self_mask
    exp_similarity = torch.exp(similarity) * denominator_mask.float()
    log_prob = similarity - torch.log(exp_similarity.sum(dim=1, keepdim=True).clamp_min(1e-12))
    positive_count = positive_mask.sum(dim=1).clamp_min(1)
    mean_log_prob_pos = (positive_mask.float() * log_prob).sum(dim=1) / positive_count
    return -mean_log_prob_pos[valid_anchor].mean()


def train_one_epoch(
    model: nn.Module,
    loader: torch.utils.data.DataLoader,
    hard_criterion: nn.Module,
    optimizer: AdamW,
    device: torch.device,
    *,
    ema: ModelEma | None = None,
    grad_clip_norm: float = 4.0,
    distill_hard_weight: float = 0.7,
    distill_teacher_weight: float = 0.3,
    contrastive_weight: float = 0.0,
    contrastive_temperature: float = 0.1,
) -> dict[str, Any]:
    model.train()
    total_loss = 0.0
    total_classification_loss = 0.0
    total_contrastive_loss = 0.0
    batch_count = 0
    all_labels: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []

    for raw_batch in loader:
        features, labels, mask, teacher_probs, aux_features, aux_mask, aux2_features, aux2_mask = unpack_batch(raw_batch)
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

        optimizer.zero_grad(set_to_none=True)
        logits = forward_model(model, features, mask, aux_features, aux_mask, aux2_features, aux2_mask)
        classification_loss = compute_classification_loss(
            logits,
            labels,
            hard_criterion,
            teacher_probs=teacher_probs,
            distill_hard_weight=distill_hard_weight,
            distill_teacher_weight=distill_teacher_weight,
        )
        contrastive_loss = logits.new_zeros(())
        if contrastive_weight > 0:
            embeddings = extract_contrastive_embeddings(
                model,
                features,
                mask,
                logits,
                aux_features,
                aux_mask,
                aux2_features,
                aux2_mask,
            )
            contrastive_loss = supervised_contrastive_loss(
                embeddings,
                labels,
                temperature=contrastive_temperature,
            )
        loss = classification_loss + float(contrastive_weight) * contrastive_loss
        loss.backward()
        if grad_clip_norm and grad_clip_norm > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=grad_clip_norm)
        optimizer.step()
        if ema is not None:
            ema.update(model)

        total_loss += float(loss.item())
        total_classification_loss += float(classification_loss.item())
        total_contrastive_loss += float(contrastive_loss.item())
        batch_count += 1
        all_labels.append(labels.detach().cpu().numpy())
        all_scores.append(softmax_positive_scores(logits).detach().cpu().numpy())

    y_true = np.concatenate(all_labels)
    y_score = np.concatenate(all_scores)
    metrics = {
        "loss": float(total_loss / max(batch_count, 1)),
        "classification_loss": float(total_classification_loss / max(batch_count, 1)),
        "contrastive_loss": float(total_contrastive_loss / max(batch_count, 1)),
    }
    metrics.update(
        {
            key: value
            for key, value in evaluate_binary_metrics(y_true, y_score, threshold=0.5).items()
            if key not in {"threshold"}
        }
    )
    metrics["y_true"] = y_true
    metrics["y_score"] = y_score
    return metrics


def evaluate_binary_metrics(y_true: np.ndarray, y_score: np.ndarray, threshold: float) -> dict[str, Any]:
    from scripts.tslib_classification_common import summarize_binary_split

    return summarize_binary_split(y_true, y_score, threshold)


def evaluate_epoch_with_teacher(
    model: nn.Module,
    loader: torch.utils.data.DataLoader,
    hard_criterion: nn.Module,
    device: torch.device,
    *,
    distill_hard_weight: float = 0.7,
    distill_teacher_weight: float = 0.3,
) -> dict[str, Any]:
    total_loss = 0.0
    batch_count = 0
    all_labels: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []

    model.eval()
    with torch.no_grad():
        for raw_batch in loader:
            features, labels, mask, teacher_probs, aux_features, aux_mask, aux2_features, aux2_mask = unpack_batch(raw_batch)
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

            logits = forward_model(model, features, mask, aux_features, aux_mask, aux2_features, aux2_mask)
            loss = compute_classification_loss(
                logits,
                labels,
                hard_criterion,
                teacher_probs=teacher_probs,
                distill_hard_weight=distill_hard_weight,
                distill_teacher_weight=distill_teacher_weight,
            )

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


def evaluate_with_optional_ema(
    model: nn.Module,
    loader: torch.utils.data.DataLoader,
    hard_criterion: nn.Module,
    device: torch.device,
    ema: ModelEma | None = None,
    *,
    distill_hard_weight: float = 0.7,
    distill_teacher_weight: float = 0.3,
) -> dict[str, Any]:
    if ema is None:
        return evaluate_epoch_with_teacher(
            model,
            loader,
            hard_criterion,
            device,
            distill_hard_weight=distill_hard_weight,
            distill_teacher_weight=distill_teacher_weight,
        )
    ema.store(model)
    ema.copy_to(model)
    try:
        return evaluate_epoch_with_teacher(
            model,
            loader,
            hard_criterion,
            device,
            distill_hard_weight=distill_hard_weight,
            distill_teacher_weight=distill_teacher_weight,
        )
    finally:
        ema.restore(model)




def json_scalar(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def window_metadata(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    metadata: dict[str, Any] = {}
    for key, value in record.items():
        scalar = json_scalar(value)
        if scalar is not None:
            metadata[str(key)] = scalar
    return metadata


def build_hard_sample_report(
    *,
    split_name: str,
    y_true: np.ndarray,
    y_score: np.ndarray,
    original_indices: np.ndarray,
    window_info: list[Any],
    selected_threshold: float,
    margin: float,
    top_k: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    y_true = np.asarray(y_true, dtype=np.int64)
    y_score = np.asarray(y_score, dtype=np.float64)
    original_indices = np.asarray(original_indices, dtype=np.int64)
    pred_05 = (y_score >= 0.5).astype(np.int64)
    pred_selected = (y_score >= selected_threshold).astype(np.int64)
    correct_05 = pred_05 == y_true
    correct_selected = pred_selected == y_true
    distance_05 = np.abs(y_score - 0.5)
    distance_selected = np.abs(y_score - selected_threshold)
    true_class_score = np.where(y_true == 1, y_score, 1.0 - y_score)
    difficulty = 1.0 - true_class_score
    near_05 = distance_05 <= margin
    near_selected = distance_selected <= margin
    hard_mask = (~correct_05) | (~correct_selected) | near_05 | near_selected

    summary = {
        "split": split_name,
        "sample_count": int(len(y_true)),
        "selected_threshold": float(selected_threshold),
        "margin": float(margin),
        "misclassified_at_0_5": int((~correct_05).sum()),
        "misclassified_at_selected_threshold": int((~correct_selected).sum()),
        "near_0_5_count": int(near_05.sum()),
        "near_selected_threshold_count": int(near_selected.sum()),
        "hard_sample_count": int(hard_mask.sum()),
        "hard_sample_rate": float(hard_mask.mean()) if len(hard_mask) else 0.0,
        "mean_difficulty": float(difficulty.mean()) if len(difficulty) else 0.0,
        "hard_mean_difficulty": float(difficulty[hard_mask].mean()) if hard_mask.any() else 0.0,
    }

    records: list[dict[str, Any]] = []
    hard_indices = np.flatnonzero(hard_mask)
    hard_indices = hard_indices[np.argsort(-difficulty[hard_indices])]
    if top_k > 0:
        hard_indices = hard_indices[:top_k]
    for split_position in hard_indices:
        original_index = int(original_indices[split_position])
        record = {
            "split": split_name,
            "split_position": int(split_position),
            "original_index": original_index,
            "y_true": int(y_true[split_position]),
            "y_score": float(y_score[split_position]),
            "pred_at_0_5": int(pred_05[split_position]),
            "pred_at_selected_threshold": int(pred_selected[split_position]),
            "correct_at_0_5": bool(correct_05[split_position]),
            "correct_at_selected_threshold": bool(correct_selected[split_position]),
            "distance_to_0_5": float(distance_05[split_position]),
            "distance_to_selected_threshold": float(distance_selected[split_position]),
            "difficulty": float(difficulty[split_position]),
            "near_0_5": bool(near_05[split_position]),
            "near_selected_threshold": bool(near_selected[split_position]),
        }
        if 0 <= original_index < len(window_info):
            for key, value in window_metadata(window_info[original_index]).items():
                record[f"info_{key}"] = value
        records.append(record)
    return summary, records


def write_hard_sample_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def write_hard_sample_outputs(
    *,
    run_dir: Path,
    window_info: list[Any],
    val_indices: np.ndarray,
    test_indices: np.ndarray,
    val_metrics: dict[str, Any],
    test_metrics: dict[str, Any],
    selected_threshold: float,
    margin: float,
    top_k: int,
) -> dict[str, Any]:
    val_summary, val_records = build_hard_sample_report(
        split_name="validation",
        y_true=val_metrics["y_true"],
        y_score=val_metrics["y_score"],
        original_indices=val_indices,
        window_info=window_info,
        selected_threshold=selected_threshold,
        margin=margin,
        top_k=top_k,
    )
    test_summary, test_records = build_hard_sample_report(
        split_name="test",
        y_true=test_metrics["y_true"],
        y_score=test_metrics["y_score"],
        original_indices=test_indices,
        window_info=window_info,
        selected_threshold=selected_threshold,
        margin=margin,
        top_k=top_k,
    )
    write_hard_sample_csv(run_dir / "validation_hard_samples.csv", val_records)
    write_hard_sample_csv(run_dir / "test_hard_samples.csv", test_records)
    summary = {
        "definition": {
            "hard_sample": "misclassified at threshold 0.5 or selected threshold, or within margin of either threshold",
            "difficulty": "1 - predicted probability assigned to the true class",
            "csv_top_k": int(top_k),
        },
        "validation": val_summary,
        "test": test_summary,
        "files": {
            "validation_csv": str(run_dir / "validation_hard_samples.csv"),
            "test_csv": str(run_dir / "test_hard_samples.csv"),
        },
    }
    write_json(run_dir / "hard_samples_summary.json", summary)
    return summary

def main() -> None:
    args = parse_args()
    label = args.label or args.model_id
    trial_dir = Path(args.trial_dir).resolve()
    run_root = trial_dir / "runs"
    run_name = f"{args.model_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = run_root / run_name
    trial_result_path = trial_dir / "trial_result.json"
    trial_error_path = trial_dir / "trial_error.json"
    run_root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        device = default_device()
        set_seed(args.seed)
        if torch.cuda.is_available():
            torch.set_float32_matmul_precision("high")

        dataset_id = infer_dataset_id(
            data_path=args.data_path,
            info_path=args.info_path,
            created_at=datetime.now(),
        )
        data_path = resolve_project_path(args.data_path)
        info_path = resolve_project_path(args.info_path)
        tslib_root = Path(args.tslib_root).resolve()
        tslib_revision = resolve_tslib_revision(tslib_root)

        args_payload = to_jsonable_dict(vars(args))
        args_payload["_resolved_dataset_id"] = dataset_id
        args_payload["_resolved_device"] = str(device)
        args_payload["_resolved_tslib_root"] = str(tslib_root)
        args_payload["_resolved_tslib_revision"] = tslib_revision
        args_payload["_resolved_results_dir"] = str(run_dir)
        write_json(run_dir / "train_args.json", args_payload)

        x_unscaled, y, mask, feature_names, window_info = load_window_dataset(data_path, info_path)
        aux_x_all, aux_mask_all = load_aux_inputs(
            args.aux_data_path,
            args.aux_mask_path,
            expected_count=len(window_info),
        )
        wave_x_all, wave_mask_all = load_wave_inputs(
            args.wave_data_path,
            args.wave_mask_path,
            args.wave_quality_path,
            expected_count=len(window_info),
        )
        if args.split_mode == "grouped":
            train_idx, val_idx, test_idx, split_report = split_dataset_grouped_indices(
                y,
                window_info,
                validation_size=args.validation_size,
                test_size=args.test_size,
                random_state=args.seed,
                group_key=args.group_key,
            )
        else:
            train_idx, val_idx, test_idx, split_report = split_dataset_random_indices(
                y,
                validation_size=args.validation_size,
                test_size=args.test_size,
                random_state=args.seed,
            )
        write_json(run_dir / "split_report.json", split_report)

        x_train_raw = x_unscaled[train_idx]
        x_val_raw = x_unscaled[val_idx]
        x_test_raw = x_unscaled[test_idx]
        x_train, x_val, x_test, scaler = standardize_splits(x_train_raw, x_val_raw, x_test_raw)
        wave_train = None
        wave_val = None
        wave_test = None
        wave_mask_train = None
        wave_mask_val = None
        wave_mask_test = None
        aux_train = None
        aux_val = None
        aux_test = None
        aux_mask_train = None
        aux_mask_val = None
        aux_mask_test = None
        if aux_x_all is not None:
            aux_train = aux_x_all[train_idx].astype(np.float32)
            aux_val = aux_x_all[val_idx].astype(np.float32)
            aux_test = aux_x_all[test_idx].astype(np.float32)
            aux_train, aux_val, aux_test = standardize_aux_splits(aux_train, aux_val, aux_test)
            if aux_mask_all is None:
                aux_mask_train = np.ones(aux_train.shape[:2], dtype=np.float32)
                aux_mask_val = np.ones(aux_val.shape[:2], dtype=np.float32)
                aux_mask_test = np.ones(aux_test.shape[:2], dtype=np.float32)
            else:
                aux_mask_train = aux_mask_all[train_idx].astype(np.float32)
                aux_mask_val = aux_mask_all[val_idx].astype(np.float32)
                aux_mask_test = aux_mask_all[test_idx].astype(np.float32)
        if wave_x_all is not None:
            wave_train = wave_x_all[train_idx].astype(np.float32)
            wave_val = wave_x_all[val_idx].astype(np.float32)
            wave_test = wave_x_all[test_idx].astype(np.float32)
            if np.max(np.abs(wave_train)) > 32.0:
                wave_train = wave_train / 254.0
                wave_val = wave_val / 254.0
                wave_test = wave_test / 254.0
            if wave_mask_all is None:
                wave_mask_train = np.ones(wave_train.shape[:2], dtype=np.float32)
                wave_mask_val = np.ones(wave_val.shape[:2], dtype=np.float32)
                wave_mask_test = np.ones(wave_test.shape[:2], dtype=np.float32)
            else:
                wave_mask_train = wave_mask_all[train_idx].astype(np.float32)
                wave_mask_val = wave_mask_all[val_idx].astype(np.float32)
                wave_mask_test = wave_mask_all[test_idx].astype(np.float32)
        mask_train = mask[train_idx]
        mask_val = mask[val_idx]
        mask_test = mask[test_idx]
        y_train = y[train_idx]
        y_val = y[val_idx]
        y_test = y[test_idx]
        teacher_train, teacher_val, _ = load_teacher_splits(args.teacher_probs_path, y_train, y_val, y_test)

        with (run_dir / "scaler.pkl").open("wb") as handle:
            pickle.dump(scaler, handle)

        train_loader = create_training_loader(
            x_train,
            y_train,
            mask_train,
            batch_size=args.batch_size,
            shuffle=True,
            seed=args.seed,
            teacher_probs=teacher_train,
            aux_x=aux_train if aux_train is not None else wave_train,
            aux_mask=aux_mask_train if aux_train is not None else wave_mask_train,
            aux2_x=wave_train if aux_train is not None else None,
            aux2_mask=wave_mask_train if aux_train is not None else None,
        )
        val_loader = create_training_loader(
            x_val,
            y_val,
            mask_val,
            batch_size=args.batch_size,
            shuffle=False,
            seed=args.seed,
            teacher_probs=teacher_val,
            aux_x=aux_val if aux_val is not None else wave_val,
            aux_mask=aux_mask_val if aux_val is not None else wave_mask_val,
            aux2_x=wave_val if aux_val is not None else None,
            aux2_mask=wave_mask_val if aux_val is not None else None,
        )
        metric_val_loader = create_data_loader(
            x_val,
            y_val,
            mask_val,
            batch_size=args.batch_size,
            shuffle=False,
            seed=args.seed,
            aux_x=aux_val if aux_val is not None else wave_val,
            aux_mask=aux_mask_val if aux_val is not None else wave_mask_val,
            aux2_x=wave_val if aux_val is not None else None,
            aux2_mask=wave_mask_val if aux_val is not None else None,
        )
        test_loader = create_data_loader(
            x_test,
            y_test,
            mask_test,
            batch_size=args.batch_size,
            shuffle=False,
            seed=args.seed,
            aux_x=aux_test if aux_test is not None else wave_test,
            aux_mask=aux_mask_test if aux_test is not None else wave_mask_test,
            aux2_x=wave_test if aux_test is not None else None,
            aux2_mask=wave_mask_test if aux_test is not None else None,
        )

        config = build_tslib_config(
            args.model_name,
            seq_len=x_train.shape[1],
            enc_in=x_train.shape[2],
            num_class=2,
            device=device,
            overrides=vars(args),
        )
        write_json(run_dir / "tslib_model_config.json", to_jsonable_dict(vars(config)))

        model = instantiate_tslib_model(args.model_name, config, tslib_root=tslib_root).to(device)
        from scripts.provenance import write_provenance
        write_provenance(run_dir / "runtime_provenance.json")
        if args.init_model_state_path:
            init_checkpoint = torch.load(Path(args.init_model_state_path).resolve(), map_location=device, weights_only=False)
            init_state = init_checkpoint.get("model_state_dict", init_checkpoint)
            model.load_state_dict(init_state, strict=True)
            if args.verbose:
                print(f"loaded initial model state from {args.init_model_state_path}")
        if args.init_backbone_state_path:
            backbone_load_report = load_backbone_state(model, args.init_backbone_state_path, device)
            write_json(run_dir / "backbone_load_report.json", backbone_load_report)
            if args.verbose:
                print(f"loaded backbone state from {args.init_backbone_state_path}: {backbone_load_report}")
        if args.freeze_backbone_epochs > 0:
            set_backbone_trainable(model, False)
        pos_weight = positive_class_weight(y_train) * args.pos_weight_scale
        class_weights = torch.tensor([1.0, pos_weight], dtype=torch.float32, device=device)
        train_criterion = nn.CrossEntropyLoss(
            weight=class_weights,
            label_smoothing=args.label_smoothing,
            reduction="none",
        )
        eval_criterion = nn.CrossEntropyLoss(weight=class_weights, label_smoothing=args.label_smoothing)
        optimizer = build_optimizer(args.optimizer, model.parameters(), args.lr, args.weight_decay)
        ema = ModelEma(model, momentum=args.ema_momentum) if args.use_ema else None
        scheduler = None
        if args.reduce_lr_patience > 0:
            scheduler = ReduceLROnPlateau(
                optimizer,
                mode=monitor_mode(args.lr_monitor),
                factor=args.reduce_lr_factor,
                patience=args.reduce_lr_patience,
                min_lr=args.min_lr,
            )
        if args.verbose and args.teacher_probs_path:
            print(
                "using teacher probabilities from {path} hard_weight={hard:.3f} teacher_weight={teacher:.3f}".format(
                    path=args.teacher_probs_path,
                    hard=args.distill_hard_weight,
                    teacher=args.distill_teacher_weight,
                )
            )

        history: dict[str, list[float]] = {
            "loss": [],
            "classification_loss": [],
            "contrastive_loss": [],
            "auc": [],
            "accuracy": [],
            "precision": [],
            "recall": [],
            "f1": [],
            "val_loss": [],
            "val_auc": [],
            "val_accuracy": [],
            "val_precision": [],
            "val_recall": [],
            "val_f1": [],
            "lr": [],
        }
        best_checkpoint_path = run_dir / "best_model.pt"
        best_monitor_value: float | None = None
        best_epoch = 0
        best_metrics: dict[str, Any] | None = None
        epochs_without_improve = 0
        selection_mode = monitor_mode(args.selection_monitor)
        initial_val_metrics = evaluate_with_optional_ema(
            model,
            val_loader,
            train_criterion,
            device,
            ema=ema,
            distill_hard_weight=args.distill_hard_weight,
            distill_teacher_weight=args.distill_teacher_weight,
        )
        best_monitor_value = monitor_value(initial_val_metrics, args.selection_monitor)
        best_metrics = {
            key: value
            for key, value in initial_val_metrics.items()
            if key not in {"y_true", "y_score"}
        }
        torch.save(
            {
                "model_state_dict": ema.state_dict() if ema is not None else copy.deepcopy(model.state_dict()),
                "epoch": 0,
                "selection_metric": args.selection_monitor,
                "selection_value": best_monitor_value,
                "uses_ema": bool(ema is not None),
            },
            best_checkpoint_path,
        )
        if args.verbose:
            print(
                "epoch=000 init_val_loss={val_loss:.4f} init_val_auc={val_auc:.4f} init_val_f1={val_f1:.4f} "
                "best_epoch=0".format(
                    val_loss=initial_val_metrics["loss"],
                    val_auc=initial_val_metrics["auc"],
                    val_f1=initial_val_metrics["f1"],
                )
            )

        start_time = time.time()
        for epoch in range(1, args.epochs + 1):
            if args.freeze_backbone_epochs > 0 and epoch == args.freeze_backbone_epochs + 1:
                set_backbone_trainable(model, True)
                optimizer = build_optimizer(args.optimizer, model.parameters(), args.lr, args.weight_decay)
                if args.reduce_lr_patience > 0:
                    scheduler = ReduceLROnPlateau(
                        optimizer,
                        mode=monitor_mode(args.lr_monitor),
                        factor=args.reduce_lr_factor,
                        patience=args.reduce_lr_patience,
                        min_lr=args.min_lr,
                    )
                if args.verbose:
                    print(f"unfroze backbone at epoch={epoch}")
            train_metrics = train_one_epoch(
                model,
                train_loader,
                train_criterion,
                optimizer,
                device,
                ema=ema,
                grad_clip_norm=args.grad_clip_norm,
                distill_hard_weight=args.distill_hard_weight,
                distill_teacher_weight=args.distill_teacher_weight,
                contrastive_weight=args.contrastive_weight,
                contrastive_temperature=args.contrastive_temperature,
            )
            val_metrics = evaluate_with_optional_ema(
                model,
                val_loader,
                train_criterion,
                device,
                ema=ema,
                distill_hard_weight=args.distill_hard_weight,
                distill_teacher_weight=args.distill_teacher_weight,
            )

            history["loss"].append(float(train_metrics["loss"]))
            history["classification_loss"].append(float(train_metrics.get("classification_loss", train_metrics["loss"])))
            history["contrastive_loss"].append(float(train_metrics.get("contrastive_loss", 0.0)))
            history["auc"].append(float(train_metrics["auc"]))
            history["accuracy"].append(float(train_metrics["accuracy"]))
            history["precision"].append(float(train_metrics["precision"]))
            history["recall"].append(float(train_metrics["recall"]))
            history["f1"].append(float(train_metrics["f1"]))
            history["val_loss"].append(float(val_metrics["loss"]))
            history["val_auc"].append(float(val_metrics["auc"]))
            history["val_accuracy"].append(float(val_metrics["accuracy"]))
            history["val_precision"].append(float(val_metrics["precision"]))
            history["val_recall"].append(float(val_metrics["recall"]))
            history["val_f1"].append(float(val_metrics["f1"]))
            history["lr"].append(float(optimizer.param_groups[0]["lr"]))

            selection_value = monitor_value(val_metrics, args.selection_monitor)
            if is_improved(selection_value, best_monitor_value, selection_mode):
                best_monitor_value = selection_value
                best_epoch = epoch
                best_metrics = {
                    key: value
                    for key, value in val_metrics.items()
                    if key not in {"y_true", "y_score"}
                }
                torch.save(
                    {
                        "model_state_dict": ema.state_dict() if ema is not None else copy.deepcopy(model.state_dict()),
                        "epoch": epoch,
                        "selection_metric": args.selection_monitor,
                        "selection_value": selection_value,
                        "uses_ema": bool(ema is not None),
                    },
                    best_checkpoint_path,
                )
                epochs_without_improve = 0
            elif epoch >= args.early_stop_start_epoch:
                epochs_without_improve += 1

            if scheduler is not None:
                scheduler.step(monitor_value(val_metrics, args.lr_monitor))

            if args.verbose:
                elapsed = time.time() - start_time
                print(
                    "epoch={epoch:03d} train_loss={train_loss:.4f} train_ce={train_ce:.4f} "
                    "train_ctr={train_ctr:.4f} train_auc={train_auc:.4f} "
                    "val_loss={val_loss:.4f} val_auc={val_auc:.4f} val_f1={val_f1:.4f} "
                    "lr={lr:.6g} best_epoch={best_epoch} elapsed_s={elapsed:.1f}".format(
                        epoch=epoch,
                        train_loss=train_metrics["loss"],
                        train_ce=train_metrics.get("classification_loss", train_metrics["loss"]),
                        train_ctr=train_metrics.get("contrastive_loss", 0.0),
                        train_auc=train_metrics["auc"],
                        val_loss=val_metrics["loss"],
                        val_auc=val_metrics["auc"],
                        val_f1=val_metrics["f1"],
                        lr=optimizer.param_groups[0]["lr"],
                        best_epoch=best_epoch,
                        elapsed=elapsed,
                    )
                )

            if epoch >= args.early_stop_start_epoch and epochs_without_improve >= args.early_stop_patience:
                if args.verbose:
                    print(
                        f"early stopping at epoch {epoch} after {epochs_without_improve} non-improving epochs "
                        f"on validation {args.selection_monitor}"
                    )
                break

        if not best_checkpoint_path.exists():
            raise FileNotFoundError(f"best checkpoint was not created at {best_checkpoint_path}")

        checkpoint = torch.load(best_checkpoint_path, map_location=device, weights_only=False)
        model.load_state_dict(checkpoint["model_state_dict"])
        torch.save(checkpoint, run_dir / "model_state.pt")

        final_val_metrics = evaluate_epoch(model, metric_val_loader, eval_criterion, device)
        final_test_metrics = evaluate_epoch(model, test_loader, eval_criterion, device)
        selected_threshold, threshold_report = build_threshold_report(
            final_val_metrics["y_true"],
            final_val_metrics["y_score"],
            final_test_metrics["y_true"],
            final_test_metrics["y_score"],
            selection_metric=args.threshold_metric,
        )
        threshold_report["split_report"] = split_report
        threshold_report["best_epoch"] = int(best_epoch)
        if best_metrics is not None:
            threshold_report["best_validation_monitor_snapshot"] = best_metrics
        write_json(run_dir / f"threshold_report_{args.threshold_metric}.json", threshold_report)
        # Stable platform artifact name; selection_metric records the actual criterion.
        threshold_report["selection_metric"] = args.threshold_metric
        write_json(run_dir / "threshold_report_f1.json", threshold_report)
        np.savez(
            run_dir / "split_indices.npz",
            train_idx=train_idx,
            val_idx=val_idx,
            test_idx=test_idx,
        )
        hard_samples_summary = write_hard_sample_outputs(
            run_dir=run_dir,
            window_info=window_info,
            val_indices=val_idx,
            test_indices=test_idx,
            val_metrics=final_val_metrics,
            test_metrics=final_test_metrics,
            selected_threshold=selected_threshold,
            margin=args.hard_sample_margin,
            top_k=args.hard_sample_top_k,
        )

        test_metrics_payload = make_test_metrics_payload(
            final_test_metrics["y_true"],
            final_test_metrics["y_score"],
            threshold=0.5,
        )
        write_json(run_dir / "test_metrics.json", test_metrics_payload)
        write_json(run_dir / "history.json", history)

        np.savez(
            run_dir / "validation_predictions.npz",
            y_true=final_val_metrics["y_true"],
            y_score=final_val_metrics["y_score"],
        )
        np.savez(
            run_dir / "test_predictions.npz",
            y_true=final_test_metrics["y_true"],
            y_score=final_test_metrics["y_score"],
            selected_threshold=selected_threshold,
        )

        trial_result = {
            "trial_id": args.trial_id,
            "stage": "main",
            "group_id": args.group_id,
            "order_index": args.order_index,
            "model_id": args.model_id,
            "model_name": args.model_name,
            "label": label,
            "description": args.description,
            "params": {
                key: value
                for key, value in to_jsonable_dict(vars(args)).items()
                if key
                not in {
                    "trial_dir",
                    "trial_id",
                    "group_id",
                    "order_index",
                    "model_id",
                    "model_name",
                    "label",
                    "description",
                    "verbose",
                }
            },
            "run_dir": str(run_dir),
            "selection_metric": args.threshold_metric,
            "selected_threshold": float(selected_threshold),
            "validation_auc": float(threshold_report["validation"]["auc"]),
            "validation_f1": float(threshold_report["validation"]["f1"]),
            "validation_accuracy": float(threshold_report["validation"]["accuracy"]),
            "validation_precision": float(threshold_report["validation"]["precision"]),
            "validation_recall": float(threshold_report["validation"]["recall"]),
            "test_auc_selected": float(threshold_report["test"]["auc"]),
            "test_f1_selected": float(threshold_report["test"]["f1"]),
            "test_accuracy_selected": float(threshold_report["test"]["accuracy"]),
            "test_precision_selected": float(threshold_report["test"]["precision"]),
            "test_recall_selected": float(threshold_report["test"]["recall"]),
            "test_auc_at_0_5": float(threshold_report["test_at_0_5"]["auc"]),
            "test_f1_at_0_5": float(threshold_report["test_at_0_5"]["f1"]),
            "test_accuracy_at_0_5": float(threshold_report["test_at_0_5"]["accuracy"]),
            "test_precision_at_0_5": float(threshold_report["test_at_0_5"]["precision"]),
            "test_recall_at_0_5": float(threshold_report["test_at_0_5"]["recall"]),
            "test_metrics_path": str(run_dir / "test_metrics.json"),
            "threshold_report_path": str(run_dir / f"threshold_report_{args.threshold_metric}.json"),
            "hard_samples_summary_path": str(run_dir / "hard_samples_summary.json"),
            "hard_samples_summary": hard_samples_summary,
            "raw_test_threshold": 0.5,
            "raw_test_auc": float(test_metrics_payload["auc_roc"]),
        }
        write_json(trial_result_path, trial_result)
        if trial_error_path.exists():
            trial_error_path.unlink()

        print(
            json.dumps(
                {
                    "trial_id": args.trial_id,
                    "model_name": args.model_name,
                    "best_epoch": best_epoch,
                    "validation_auc": trial_result["validation_auc"],
                    "validation_f1": trial_result["validation_f1"],
                    "test_auc_selected": trial_result["test_auc_selected"],
                    "test_f1_selected": trial_result["test_f1_selected"],
                    "run_dir": str(run_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        error_payload = {
            "trial_id": args.trial_id,
            "model_id": args.model_id,
            "model_name": args.model_name,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
            "trial_dir": str(trial_dir),
            "run_dir": str(run_dir),
        }
        write_json(trial_error_path, error_payload)
        raise
    finally:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


if __name__ == "__main__":
    main()

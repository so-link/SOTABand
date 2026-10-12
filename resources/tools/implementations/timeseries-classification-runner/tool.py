"""Generic time-series classification workflow tool.

The first adapter is the five-channel SpO2 V3 model.  Long-running work is
returned as a task request and is executed by SOTABand's task manager.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


_TOOL_DIR = Path(os.environ.get("TOOL_DIR", Path(__file__).resolve().parent)).resolve()
_PROJECT_ROOT = _TOOL_DIR.parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.runtime.paths import (
    model_registry_path,
    processed_data_dir,
    runtime_temp_dir,
    task_registry_path,
    timeseries_runs_dir,
    data_registry_path,
    RUNTIME_ROOT,
)

FEATURES = ["SpO2", "HR", "PR", "RR", "PI"]
from resources.workflows.hypoxemia.runtime_config import COMPONENT_ROOT, compute_python, compute_env
from resources.workflows.hypoxemia.conversation_config import (
    MAMBA_MODEL_NAMES, MULTISCALE_MODEL_NAMES,
    UNSUPPORTED_CLASSIFICATION_MODEL_NAMES, model_architecture_defaults,
)

DEFAULT_PIPELINE_SCRIPT = str(COMPONENT_ROOT / "raw" / "run_dedup_pipeline.py")
DEFAULT_ARTIFACT = os.environ.get("SPO2_HYPOXEMIA_V3_ARTIFACT", "")
REQUIRED_DATA_FILES = ("window_features.pkl", "window_info.pkl")
REQUIRED_MODEL_FILES = ("best_model.pt", "scaler.pkl", "tslib_model_config.json")

RAW_PROCESSING_DEFAULTS = {
    "lookback_seconds": 256,
    "prediction_interval_seconds": 30,
    "min_segment_seconds": 600,
    "spo2_threshold": 94.0,
    "hypo_operator": "le",
    "drop_spo2_na_before_segment": False,
    "spo2_na_break_segment": True,
    "crop_seconds": 30,
    "edge_trim_mode": "zhang",
    "nan_ratio_threshold": 0.1,
    "fill_method": "interpolate",
    "apply_physiologic_bounds": True,
    "pi_threshold": 0.3,
    "low_pi_ratio_threshold": 0.3,
    "hypoxemia_duration_seconds": 20,
    "preprocess_hypo_operator": "lt",
    "window_hypo_operator": "le",
    "min_hypo_duration_seconds": 20,
    "max_non_hypo_ratio": 2.0,
    "seed": 42,
}

# The adapter exposes only models that accept the fixed five-channel window input.
FIVE_CHANNEL_MODELS = (
    ("Autoformer", "Autoformer", False),
    ("Crossformer", "Crossformer", False),
    ("DLinear", "DLinear", False),
    ("ETSformer", "ETSformer", False),
    ("FEDformer", "FEDformer", False),
    ("FiLM", "FiLM", False),
    ("Informer", "Informer", False),
    ("KANAD", "KANAD", False),
    ("LightTS", "LightTS", False),
    ("MICN", "MICN", False),
    ("MSGNet", "MSGNet", False),
    ("MambaSingleLayer", "Mamba single layer", False),
    ("MultiPatchFormer", "MultiPatchFormer", False),
    ("Nonstationary_Transformer", "Nonstationary Transformer", False),
    ("PatchTST", "PatchTST", False),
    ("Pyraformer", "Pyraformer", False),
    ("Reformer", "Reformer", False),
    ("SegRNN", "SegRNN", True),
    ("TiDE", "TiDE", False),
    ("TimeFilter", "TimeFilter", False),
    ("TimeMixer", "TimeMixer", False),
    ("TimesNet", "TimesNet", False),
    ("Transformer", "Transformer", False),
    ("WPMixer", "WPMixer", False),
    ("iTransformer", "iTransformer", False),
    ("SegRNNClsPool", "SegRNN pooled head", False),
    ("SegRNNDeepHead", "SegRNN deep head", False),
    ("SegRNNDiffFusion", "SegRNN difference fusion", False),
    ("SegRNNDiffTimeMixerGate", "SegRNN-TimeMixer gated fusion", False),
    ("SegRNNHybridHead", "SegRNN hybrid head", False),
    ("SegRNNiTransformerFusion", "SegRNN-iTransformer fusion", False),
    ("SegRNNiTransformerGate", "SegRNN-iTransformer gated fusion", False),
    ("SegRNNMambaFusion", "SegRNN-Mamba fusion", False),
    ("SegRNNNumericQualityAware", "SegRNN numeric quality aware", False),
    ("SegRNNResidualAux", "SegRNN residual statistics", False),
    ("SegRNNRichFusion", "SegRNN rich fusion", False),
    ("SegRNNTimeMixerFusion", "SegRNN-TimeMixer fusion", False),
    ("SegRNNTimeMixerGate", "SegRNN-TimeMixer gated fusion", False),
    ("SegRNNTrendResidualFusion", "SegRNN trend-residual fusion", False),
    ("SegRNNDualScale", "SegRNN dual scale", False),
    ("SegRNNDualScaleV2", "SegRNN dual scale V2", True),
    ("TimeMixerClsPool", "TimeMixer pooled head", False),
    ("TimeMixeriTransformerFusion", "TimeMixer-iTransformer fusion", False),
    ("TransformerClsPool", "Transformer pooled head", False),
)

DEFAULT_MODEL_CONFIG = {
    "d_model": 128, "n_heads": 4, "e_layers": 3, "d_layers": 3, "d_ff": 256,
    "dropout": 0.15, "activation": "gelu", "patch_len": 16, "stride": 8,
    "top_k": 3, "num_kernels": 6, "moving_avg": 25, "seg_len": 32,
    "p_hidden_dims": [128, 128], "p_hidden_layers": 2,
    "embed": "fixed", "freq": "h", "factor": 1, "distil": True,
    "individual": False, "channel_independence": 0, "down_sampling_layers": 0,
    "down_sampling_window": 1, "down_sampling_method": "avg",
    "use_norm": 1, "decomp_method": "moving_avg", "alpha": 0.1,
    "top_p": 0.5, "pos": 1, "node_dim": 10, "gcn_depth": 2,
    "propalpha": 0.3, "conv_channel": 32, "skip_channel": 32,
    "subgraph_size": 5, "ratio": 0.5, "expand": 2, "d_conv": 4,
}
DEFAULT_TRAINING_CONFIG = {
    "epochs": 140, "batch_size": 32, "learning_rate": 0.00025,
    "optimizer": "adamw", "seed": 42,
    "weight_decay": 0.00005, "pos_weight_scale": 0.7, "use_ema": True,
    "ema_momentum": 0.99, "label_smoothing": 0.015, "grad_clip_norm": 4.0,
    "early_stop_patience": 35, "early_stop_start_epoch": 25,
    "reduce_lr_patience": 4, "reduce_lr_factor": 0.5, "min_lr": 0.000005,
    "selection_monitor": "auc", "lr_monitor": "loss", "threshold_metric": "f1",
}
CONFIG_LIMITS = {
    "d_model": (8, 1024), "n_heads": (1, 32), "e_layers": (1, 12), "d_layers": (1, 12),
    "d_ff": (8, 2048), "dropout": (0.0, 0.8), "patch_len": (1, 256), "stride": (1, 256),
    "top_k": (1, 64), "num_kernels": (1, 64), "moving_avg": (1, 255), "seg_len": (1, 256),
    "p_hidden_layers": (1, 8), "epochs": (1, 500), "batch_size": (1, 512),
    "learning_rate": (0.000001, 1.0), "weight_decay": (0.0, 1.0),
    "pos_weight_scale": (0.01, 100.0), "ema_momentum": (0.0, 0.99999),
    "label_smoothing": (0.0, 0.5), "grad_clip_norm": (0.0, 100.0),
    "early_stop_patience": (0, 200), "early_stop_start_epoch": (0, 200),
    "reduce_lr_patience": (0, 100), "reduce_lr_factor": (0.01, 0.99), "min_lr": (0.0, 1.0),
    "factor": (1, 20), "channel_independence": (0, 1), "down_sampling_layers": (0, 8),
    "down_sampling_window": (1, 32), "use_norm": (0, 1), "alpha": (0.0, 10.0),
    "top_p": (0.0, 1.0), "pos": (0, 1), "node_dim": (1, 512), "gcn_depth": (1, 16),
    "propalpha": (0.0, 1.0), "conv_channel": (1, 1024), "skip_channel": (1, 1024),
    "subgraph_size": (1, 128), "ratio": (0.0, 1.0), "expand": (1, 16), "d_conv": (1, 64),
    "seed": (0, 2147483647),
}

EVALUATION_METRICS = (
    "auc", "auprc", "f1", "accuracy", "balanced_accuracy", "precision", "recall",
    "specificity", "sensitivity", "npv", "ppv", "mcc", "loss", "threshold",
)


def _call_api(api_name: str, **params: Any) -> dict[str, Any]:
    from core.api import get_api

    result = get_api(api_name).call(**params)
    if asyncio.iscoroutine(result):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(result)
        finally:
            loop.close()
    return result


def _ok(message: str, data: dict[str, Any], card_type: str = "result-summary") -> dict[str, Any]:
    return {"status": "success", "output_format": "text", "message": message,
            "card_type": card_type, "data": data}


def _fail(message: str, **data: Any) -> dict[str, Any]:
    return {"status": "failed", "output_format": "text", "message": message, "data": data}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _model_catalog() -> list[dict[str, Any]]:
    return [
        {
            "model_name": model_name,
            "label": label,
            "available": model_name not in UNSUPPORTED_CLASSIFICATION_MODEL_NAMES and (
                model_name not in MAMBA_MODEL_NAMES
                or os.environ.get("SOTABAND_HYPOXEMIA_ENABLE_MAMBA") == "1"
            ),
            "verified_baseline": verified_baseline,
            "input_format": "timeseries-256x5",
            "reason": (
                "上游模型尚未实现分类任务"
                if model_name in UNSUPPORTED_CLASSIFICATION_MODEL_NAMES else
                "需要单独配置并验证 CUDA / mamba-ssm 环境"
                if model_name in MAMBA_MODEL_NAMES else ""
            ),
        }
        for model_name, label, verified_baseline in FIVE_CHANNEL_MODELS
    ]


def _available_model_names() -> set[str]:
    return {item[0] for item in FIVE_CHANNEL_MODELS} - UNSUPPORTED_CLASSIFICATION_MODEL_NAMES


def _parse_config(value: Any, field_name: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be a JSON object: {exc.msg}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field_name} must be an object")


def _validate_config(overrides: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise ValueError(f"unsupported configuration fields: {', '.join(unknown)}")

    normalized: dict[str, Any] = {}
    for key, value in overrides.items():
        if key == "p_hidden_dims":
            if not isinstance(value, list) or not value or not all(isinstance(item, int) and 1 <= item <= 2048 for item in value):
                raise ValueError("p_hidden_dims must be a non-empty integer array in [1, 2048]")
            normalized[key] = value
            continue
        if key in {
            "activation", "selection_monitor", "lr_monitor", "threshold_metric",
            "optimizer", "embed", "freq", "down_sampling_method", "decomp_method",
        }:
            normalized[key] = str(value)
            continue
        if key in {"use_ema", "distil", "individual"}:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be boolean")
            normalized[key] = value
            continue
        lower, upper = CONFIG_LIMITS[key]
        numeric = float(value) if isinstance(lower, float) or isinstance(upper, float) else int(value)
        if not lower <= numeric <= upper:
            raise ValueError(f"{key} must be in [{lower}, {upper}]")
        normalized[key] = numeric
    return normalized


def _resolve_model_resource(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    entries = _read_json_list(model_registry_path())
    for entry in entries:
        if entry.get("id") == value or entry.get("name") == value:
            return entry
    return None


def _list_models() -> dict[str, Any]:
    catalog = _model_catalog()
    registered = [
        entry for entry in _read_json_list(model_registry_path())
        if entry.get("associated_tool_id") == "timeseries-classification-runner"
    ]
    return _ok(
        f"已发现 {len(catalog)} 个目录模型和 {len(registered)} 个已注册模型",
        {
            "models": catalog,
            "registered_models": [{
                "id": item.get("id", ""), "name": item.get("name", ""),
                "model_name": item.get("model_name", ""),
                "runtime_status": item.get("runtime_status", ""),
                "creation_type": item.get("creation_type", ""),
            } for item in registered],
            "model_config_schema": {
                "architecture": sorted(DEFAULT_MODEL_CONFIG),
                "training": sorted(DEFAULT_TRAINING_CONFIG),
                "constraints": "Only bounded schema fields are accepted for five-channel models.",
            },
        },
        "model-catalog",
    )


def _model_parameter_schema(params: dict[str, Any]) -> dict[str, Any]:
    model_id = str(params.get("model_id") or params.get("model_name") or "").strip()
    resource = _resolve_model_resource(model_id)
    model_name = str((resource or {}).get("model_name") or model_id or "SegRNNDualScaleV2")
    stored = (resource or {}).get("training_config", {})
    architecture = stored.get("architecture", {}) if isinstance(stored, dict) else {}
    training = stored.get("training", {}) if isinstance(stored, dict) else {}
    parameter_specs = {
        key: {"min": bounds[0], "max": bounds[1]}
        for key, bounds in CONFIG_LIMITS.items()
    }
    return _ok("已读取模型、训练和评测可配置参数", {
        "model_id": (resource or {}).get("id", model_id),
        "model_name": model_name,
        "architecture": {
            **DEFAULT_MODEL_CONFIG, **model_architecture_defaults(model_name),
            **(architecture if isinstance(architecture, dict) else {}),
        },
        "training": {**DEFAULT_TRAINING_CONFIG, **(training if isinstance(training, dict) else {})},
        "architecture_groups": {
            "基础维度": ["d_model", "d_ff", "dropout", "activation", "embed", "freq"],
            "层与注意力": ["n_heads", "e_layers", "d_layers", "factor", "distil", "individual"],
            "窗口与分块": ["patch_len", "stride", "seg_len", "moving_avg", "top_k", "num_kernels"],
            "Mixer/分解": ["channel_independence", "down_sampling_layers", "down_sampling_window", "down_sampling_method", "use_norm", "decomp_method", "alpha", "top_p", "pos"],
            "图与状态空间": ["node_dim", "gcn_depth", "propalpha", "conv_channel", "skip_channel", "subgraph_size", "ratio", "expand", "d_conv"],
            "分类头": ["p_hidden_dims", "p_hidden_layers"],
        },
        "training_groups": {
            "优化器": ["optimizer", "learning_rate", "weight_decay", "batch_size", "epochs", "seed"],
            "损失": ["pos_weight_scale", "label_smoothing", "grad_clip_norm"],
            "EMA": ["use_ema", "ema_momentum"],
            "早停": ["early_stop_patience", "early_stop_start_epoch", "selection_monitor"],
            "学习率调度": ["reduce_lr_patience", "reduce_lr_factor", "min_lr", "lr_monitor"],
            "阈值": ["threshold_metric"],
        },
        "parameter_specs": parameter_specs,
        "optimizer_options": ["adamw", "adam", "sgd", "rmsprop"],
        "evaluation_metrics": list(EVALUATION_METRICS),
        "defaults": {"evaluate_all_metrics": True, "export_excel": False, "save_trained_model": True},
    }, "model-parameters")


def _model_creation_guide() -> dict[str, Any]:
    """Explain the executable and design-only creation paths before a resource is registered."""
    text = """## 创建新模型

两种方式使用同一份模型描述，区别只在“模型来源”。

- 来源为已有模型：填写 `模型来源：DLinear` 或已有的 `model-...`。系统只改变参数，创建后可训练。
- 来源为全新结构：填写 `模型来源：自定义结构`。系统先提取和回显设计，随后才进入 PyTorch 实现与验证，不会直接注册为可训练模型。

请按以下字段描述：

- 模型名称：<名称>
- 模型来源：<已有模型名称 / model-... / 自定义结构>
- 输入：固定为 256x5，通道为 SpO2、HR、PR、RR、PI
- 编码器：<逐层结构>
- 时序聚合：<时序模块>
- 分类头：<二分类输出结构>
- 训练：<轮数、批次大小、学习率>

全新结构示例：

- 模型名称：transformer_gru_v1
- 模型来源：自定义结构
- 输入：256x5 五通道生理信号
- 编码器：3 层 Transformer，隐藏维度 128，4 个注意力头，dropout 0.2
- 时序聚合：单层 GRU，隐藏维度 64
- 分类头：GRU 最后时刻输出接二分类全连接层
- 训练：120 轮，批次大小 32，学习率 0.0002"""
    return _ok("请先选择派生模型或全新网络结构", {"text": text}, "model-creation-guide")


def _select_dataset_source(params: dict[str, Any]) -> dict[str, Any]:
    value = str(params.get("dataset_source_type", "")).strip().lower()
    source_path = str(params.get("source_path", "")).strip()
    raw_processing_config = {
        key: params[key] for key in RAW_PROCESSING_DEFAULTS
        if key in params and params[key] not in (None, "")
    }
    for key in ("configs", "lookback_seconds", "window_seconds", "sequence_length", "prediction_interval_seconds", "interval_seconds", "max_non_hypo_ratio", "negative_positive_ratio"):
        if key in params and params[key] not in (None, ""):
            raw_processing_config[key] = params[key]
    if value in {"raw", "原始", "原始数据"}:
        return _ok("已选择原始数据，将进入原始数据处理流程", {
            "dataset_source_type": "raw",
            "source_path": source_path,
            "staging_root": str(params.get("staging_root", "")).strip(),
            "raw_processing_config": raw_processing_config,
            "next_mode": "process-raw-dataset",
            "next_step": (
                "已记录原始数据目录，请回复“继续”以开始处理"
                if source_path else "请提供原始数据目录或上传原始数据目录"
            ),
        }, "workflow-choice")
    if value in {
        "processed", "已处理", "已处理数据", "已处理的", "已处理的v3数据",
        "处理好的", "处理好的数据", "处理好的v3数据", "v3",
    }:
        return _ok("已选择已处理数据，将进入五通道窗口数据检查流程", {
            "dataset_source_type": "processed",
            "source_path": source_path,
            "next_mode": "validate",
            "next_step": (
                "已记录已处理数据目录，请回复“继续”以检查数据"
                if source_path else "请提供已处理数据目录、文件路径或已注册数据集 ID"
            ),
        }, "workflow-choice")
    return _fail(
        "数据集类型只能选择 raw（原始数据）或 processed（已处理的五通道窗口数据）",
        allowed=["raw", "processed"],
    )


def _raw_processing_configuration(params: dict[str, Any]) -> dict[str, Any]:
    """Normalize raw pipeline settings and window configuration."""
    config = dict(RAW_PROCESSING_DEFAULTS)
    aliases = {
        "window_seconds": "lookback_seconds",
        "sequence_length": "lookback_seconds",
        "interval_seconds": "prediction_interval_seconds",
        "prediction_interval": "prediction_interval_seconds",
        "negative_positive_ratio": "max_non_hypo_ratio",
    }
    for key, value in params.items():
        target = aliases.get(key, key)
        if target in config and value not in (None, ""):
            config[target] = value

    raw_configs = params.get("configs")
    if isinstance(raw_configs, str):
        text = raw_configs.strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
                raw_configs = decoded if isinstance(decoded, list) else [text]
            except json.JSONDecodeError:
                raw_configs = [item.strip() for item in text.strip("[]").split(",") if item.strip()]
        else:
            raw_configs = [item.strip() for item in text.split(",") if item.strip()]
    if raw_configs:
        configs = [str(item).strip() for item in raw_configs]
    else:
        configs = [f"{int(config['lookback_seconds'])}:{int(config['prediction_interval_seconds'])}"]
    normalized_configs = []
    for item in configs:
        match = re.fullmatch(r"(\d+)\s*:\s*(\d+)", item)
        if not match or int(match.group(1)) <= 0 or int(match.group(2)) < 0:
            raise ValueError(f"invalid window config '{item}', expected LOOKBACK:INTERVAL")
        normalized_configs.append(f"{int(match.group(1))}:{int(match.group(2))}")
    config["configs"] = normalized_configs
    first_lookback, first_interval = normalized_configs[0].split(":")
    config["lookback_seconds"] = int(first_lookback)
    config["prediction_interval_seconds"] = int(first_interval)
    if float(config["max_non_hypo_ratio"]) <= 0:
        raise ValueError("max_non_hypo_ratio must be greater than 0")
    if float(config["nan_ratio_threshold"]) < 0 or float(config["nan_ratio_threshold"]) > 1:
        raise ValueError("nan_ratio_threshold must be between 0 and 1")
    if float(config["low_pi_ratio_threshold"]) < 0 or float(config["low_pi_ratio_threshold"]) > 1:
        raise ValueError("low_pi_ratio_threshold must be between 0 and 1")
    if not 0 < float(config["spo2_threshold"]) <= 100:
        raise ValueError("spo2_threshold must be between 0 and 100")
    if str(config["hypo_operator"]) not in {"le", "lt"}:
        raise ValueError("hypo_operator must be le or lt")
    if str(config["preprocess_hypo_operator"]) not in {"le", "lt"}:
        raise ValueError("preprocess_hypo_operator must be le or lt")
    if str(config["window_hypo_operator"]) not in {"le", "lt"}:
        raise ValueError("window_hypo_operator must be le or lt")
    if float(config["max_non_hypo_ratio"]) <= 0:
        raise ValueError("max_non_hypo_ratio must be greater than 0")
    for key in ("min_segment_seconds", "hypoxemia_duration_seconds", "min_hypo_duration_seconds", "crop_seconds", "seed"):
        if int(config[key]) < 0:
            raise ValueError(f"{key} must be non-negative")
    config["sampling_hz"] = 1
    return config


def _build_raw_processing_request(params: dict[str, Any]) -> dict[str, Any]:
    source_path = str(params.get("source_path", "")).strip()
    if not source_path:
        return _fail("请提供原始数据目录或 ZIP 文件")
    source = Path(source_path).expanduser().resolve()
    is_archive = source.is_file() and source.suffix.lower() == ".zip"
    if not source.exists() or (not source.is_dir() and not is_archive):
        return _fail("原始数据目录或 ZIP 文件不存在", source_path=str(source))

    pipeline = Path(DEFAULT_PIPELINE_SCRIPT).expanduser().resolve()
    if not pipeline.is_file():
        return _fail("未找到原始数据处理流水线入口", pipeline_script=str(pipeline))
    python_exe = compute_python(params.get("python_executable"))
    if not Path(python_exe).is_file():
        return _fail("原始数据处理 Python 解释器不存在", python_executable=python_exe)

    try:
        processing_config = _raw_processing_configuration(params)
    except ValueError as exc:
        return _fail(str(exc))
    output_root = Path(
        str(params.get("output_root") or (processed_data_dir() / str(int(time.time()))))
    ).expanduser().resolve()
    window_paths = [
        output_root / f"balanced_windows_{item.replace(':', 's_')}s_all"
        for item in processing_config["configs"]
    ]
    window_path = window_paths[0]
    wrapper = COMPONENT_ROOT / "raw_zip_pipeline.py"
    if not wrapper.is_file():
        return _fail("未找到原始数据 ZIP 兼容处理入口", wrapper_script=str(wrapper))
    archive_count = 1 if is_archive else sum(
        1 for item in source.rglob("*") if item.is_file() and item.suffix.lower() == ".zip"
    )
    archive_uncompressed_bytes = 0
    if archive_count:
        archives = [source] if is_archive else [
            item for item in source.rglob("*") if item.is_file() and item.suffix.lower() == ".zip"
        ]
        try:
            with zipfile.ZipFile(archives[0]) as archive:
                archive_uncompressed_bytes += sum(item.file_size for item in archive.infolist())
            for archive_path in archives[1:]:
                with zipfile.ZipFile(archive_path) as archive:
                    archive_uncompressed_bytes += sum(item.file_size for item in archive.infolist())
        except (OSError, zipfile.BadZipFile) as exc:
            return _fail("原始 ZIP 文件无法读取", source_path=str(source), error=str(exc))
    staging_root = str(
        params.get("staging_root")
        or os.environ.get("SOTABAND_RAW_STAGING_ROOT", "")
        or (runtime_temp_dir() if archive_count else "")
    ).strip()
    if archive_count and staging_root:
        staging_path = Path(staging_root).expanduser().resolve()
        source_contains_staging = source.is_dir() and source in staging_path.parents
        if staging_path == source or source_contains_staging:
            return _fail(
                "staging_root 不能与原始数据目录相同，也不能位于原始数据目录内部；请提供独立的临时解压目录。",
                source_path=str(source),
                staging_root=str(staging_path),
            )
    if archive_uncompressed_bytes:
        check_root = Path(staging_root).expanduser().resolve() if staging_root else runtime_temp_dir()
        check_root.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(check_root).free
        if archive_uncompressed_bytes > free_bytes:
            return _fail(
                f"ZIP 原始数据展开约需 {archive_uncompressed_bytes / 1024**3:.1f} GB，"
                f"当前临时目录可用 {free_bytes / 1024**3:.1f} GB，空间不足。"
                "请提供 staging_root 或设置 SOTABAND_RAW_STAGING_ROOT 指向有足够空间的磁盘。",
                source_path=str(source), archive_count=archive_count,
                archive_uncompressed_bytes=archive_uncompressed_bytes,
                staging_root=str(check_root), free_bytes=free_bytes,
            )
    command = [
        python_exe, str(wrapper),
        "--pipeline", str(pipeline),
        "--source-root", str(source),
        "--output-root", str(output_root),
        "--configs", *processing_config["configs"],
    ]
    if staging_root:
        command.extend(["--staging-root", staging_root])
    pipeline_keys = (
        "min_segment_seconds", "spo2_threshold", "hypo_operator",
        "drop_spo2_na_before_segment", "spo2_na_break_segment", "crop_seconds",
        "edge_trim_mode", "nan_ratio_threshold", "fill_method",
        "apply_physiologic_bounds", "pi_threshold", "low_pi_ratio_threshold",
        "hypoxemia_duration_seconds", "preprocess_hypo_operator",
        "window_hypo_operator", "min_hypo_duration_seconds", "max_non_hypo_ratio", "seed",
    )
    for key in (name for name in pipeline_keys if name in params):
        command.extend([f"--{key.replace('_', '-')}", str(processing_config[key]).lower() if isinstance(processing_config[key], bool) else str(processing_config[key])])
    pipeline_settings = {
        key: value for key, value in processing_config.items() if key != "configs"
    }
    task_request = {
        "name": "处理五通道低氧原始数据",
        "kind": "hypoxemia-data-processing",
        "command": command,
        "cwd": str(processed_data_dir()),
        "metadata": {
            "source_path": str(source),
            "dataset_source_type": "raw",
            "output_root": str(output_root),
            "processed_dataset_path": str(window_path),
            "processed_dataset_paths": [str(item) for item in window_paths],
            "pipeline_script": str(pipeline),
            "archive_count": archive_count,
            "processing_config": processing_config,
        },
    }
    return _ok("原始数据处理配置已校验，等待任务层提交", {
        "task_request": task_request,
        "source_path": str(source),
        "dataset_source_type": "raw",
        "output_root": str(output_root),
        "processed_dataset_path": str(window_path),
        "processed_dataset_paths": [str(item) for item in window_paths],
        "pipeline_stages": ["raw", "preprocess", "merge", "windows"],
        "window_config": ",".join(processing_config["configs"]),
        "dataset_name": f"spo2_windows_{processing_config['lookback_seconds']}s_{processing_config['prediction_interval_seconds']}s_{time.strftime('%Y%m%d_%H%M%S')}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "processing_config": pipeline_settings,
        "archive_count": archive_count,
        "archive_uncompressed_bytes": archive_uncompressed_bytes,
        "staging_root": staging_root,
        "archive_support": True,
    }, "task-progress")


def _analyze_custom_model_design(params: dict[str, Any]) -> dict[str, Any]:
    description = str(params.get("description", "")).strip()
    if not description:
        return _fail("请提供自定义模型的结构描述")
    structured_description = str(params.get("structured_description") or "").strip()

    def section(*labels: str) -> str:
        for text in (description, structured_description):
            for label in labels:
                match = re.search(
                    rf"(?:^|\n)[ \t]*(?:[-*][ \t]*)?{re.escape(label)}[ \t]*[：:][ \t]*([^\r\n]*)",
                    text,
                    re.IGNORECASE,
                )
                if match and match.group(1).strip():
                    value = match.group(1).strip().rstrip("。")
                    if not re.fullmatch(r"(?:输入|输入数据|编码器|时序聚合|时序模块|分类头|训练|训练设置)[：:]?", value):
                        return value
        return ""

    name_match = re.search(
        r"(?:创建自定义模型\s*|自定义模型设计\s*[：:])\s*([A-Za-z][A-Za-z0-9_-]*)",
        description,
        re.IGNORECASE,
    )
    name = str(params.get("model_resource_name") or "").strip() or (
        name_match.group(1) if name_match else section("模型名称") or "未命名"
    )
    base_model = str(params.get("base_model") or "").strip()
    model_overrides = _validate_config(
        _parse_config(params.get("model_config"), "model_config"), set(DEFAULT_MODEL_CONFIG),
    )
    training_overrides = _validate_config(
        _parse_config(params.get("training_config"), "training_config"), set(DEFAULT_TRAINING_CONFIG),
    )
    design = {
        "name": name,
        "input": section("输入", "输入数据") or "256x5，SpO2、HR、PR、RR、PI",
        "encoder": section("编码器") or (f"沿用 {base_model} 编码器" if base_model else ""),
        "temporal_module": section("时序聚合", "时序模块") or (f"沿用 {base_model} 时序聚合" if base_model else ""),
        "classification_head": section("分类头", "融合/分类头") or (f"沿用 {base_model} 分类头" if base_model else ""),
        "training": section("训练", "训练设置") or (
            json.dumps(training_overrides, ensure_ascii=False) if training_overrides else ""
        ),
    }
    required = {
        "编码器": design["encoder"],
        "时序聚合": design["temporal_module"],
        "分类头": design["classification_head"],
        "训练": design["training"],
    }
    data = {
        "design": design,
        "missing_sections": [label for label, value in required.items() if not value],
        "runtime_status": "design-submitted",
    }
    if params.get("register_model"):
        executable = base_model in _available_model_names()
        configuration = {
            "model_name": base_model or design["name"],
            "architecture": model_overrides,
            "training": training_overrides,
            "design": design,
        }
        registered = _call_api(
            "api-model-register", name=str(params.get("model_resource_name") or design["name"]),
            preserve_name=bool(params.get("model_resource_name")),
            raw_md=(f"# {design['name']}\n\n大模型辅助解析的五通道低氧预测自定义模型设计。\n\n"
                    f"```json\n{json.dumps(configuration, ensure_ascii=False, indent=2)}\n```\n"),
            framework="PyTorch", model_path="", input_format="timeseries-256x5",
            output_format="binary-classification", tags=["时序分类", "低氧预测", "自定义模型"],
            associated_tool_id="timeseries-classification-runner", group_path="低氧血症预测",
            dataset_id=str(params.get("dataset_id", "")),
            model_name=base_model or design["name"], model_type=base_model or "custom-design",
            runtime_status="configured" if executable else "design-submitted",
            training_config=configuration, creation_type="custom-model",
        )
        data.update(registered)
        data["model_name"] = base_model or design["name"]
        data["runtime_status"] = "configured" if executable else "design-submitted"
        data["trainable"] = executable
    return _ok("自定义模型结构已解析" + ("并注册" if params.get("register_model") else ""), data, "custom-model-design")


def _delete_model(params: dict[str, Any]) -> dict[str, Any]:
    model_id = str(params.get("model_id", "")).strip()
    if not model_id:
        return _fail("请提供要删除的模型资源 ID")
    from core.resource.registry.model_registry import ModelRegistry

    registry = ModelRegistry()
    entries = registry._read()
    if not any(item.get("id") == model_id for item in entries):
        return _fail("未找到模型资源", model_id=model_id)
    registry._write([item for item in entries if item.get("id") != model_id])
    spec_path = registry._get_def_dir() / f"{model_id}.md"
    if spec_path.is_file():
        spec_path.unlink()
    return _ok("模型资源已删除", {"model_id": model_id}, "model-resource")


def _find_named_file(root: Path, name: str) -> Path | None:
    direct = root / name
    if direct.is_file():
        return direct
    matches = list(root.rglob(name)) if root.is_dir() else []
    return matches[0] if len(matches) == 1 else None


def _resolve_dataset_source(value: str) -> tuple[Path | None, dict[str, Any] | None]:
    path = Path(value).expanduser()
    if path.exists():
        resolved = path.resolve()
        for entry in _read_json_list(data_registry_path()):
            data_path = Path(str(entry.get("data_path") or ""))
            if not data_path.is_absolute():
                from core.resource.registry.data_registry import DataRegistry
                data_path = DataRegistry().resolve_data_path(str(data_path))
            if data_path.resolve() == resolved or data_path.resolve() == resolved.parent:
                from core.resource.registry.data_registry import DataRegistry
                from core.user_context import get_current_user_id
                if DataRegistry().is_visible(entry, get_current_user_id()):
                    return resolved, entry
        return resolved, None

    registry_path = data_registry_path()
    try:
        entries = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        entries = []
    needle = value.strip().lower()
    for entry in entries:
        if str(entry.get("id", "")).lower() == needle or str(entry.get("name", "")).lower() == needle:
            from core.user_context import get_current_user_id
            from core.resource.registry.data_registry import DataRegistry
            registry = DataRegistry()
            if not registry.is_visible(entry, get_current_user_id()):
                return None, None
            data_path = Path(entry.get("data_path", ""))
            if not data_path.is_absolute():
                data_path = registry.resolve_data_path(str(data_path))
            return data_path.resolve(), entry
    return None, None


def _dataset_files(source: Path) -> tuple[Path | None, Path | None, Path | None]:
    root = source if source.is_dir() else source.parent
    features = source if source.name == "window_features.pkl" else _find_named_file(root, "window_features.pkl")
    info = _find_named_file(root, "window_info.pkl")
    stats = _find_named_file(root, "window_stats.json")
    return features, info, stats


def _validate_dataset(source_value: str) -> dict[str, Any]:
    source, entry = _resolve_dataset_source(source_value)
    if source is None or not source.exists():
        return _fail(f"数据集路径或资源不存在: {source_value}")
    features, info, stats_path = _dataset_files(source)
    missing = [name for name, path in zip(REQUIRED_DATA_FILES, (features, info)) if path is None]
    if missing:
        return _fail("数据集不是五通道窗口格式", missing_files=missing, source_path=str(source))

    stats = _read_json(stats_path) if stats_path else {}
    manifest = _read_json(features.parent / "dataset_manifest.json")
    for declaration in (stats, manifest):
        if declaration and (declaration.get("sampling_hz", 1) != 1
                            or declaration.get("feature_order", FEATURES) != FEATURES):
            return _fail("数据集采样率或通道顺序不兼容：需要 1 Hz，SpO2、HR、PR、RR、PI")
    if manifest and stats and manifest.get("sequence_length") != stats.get("lookback_seconds"):
        return _fail("数据集声明窗口长度与统计信息不一致")
    lookback_seconds = int(stats.get("lookback_seconds", 256))
    interval_seconds = int(stats.get("interval_seconds", 30))
    stats_available = bool(stats_path and stats)
    total_windows = int(stats["total_windows"]) if "total_windows" in stats else None
    hypoxemia_windows = int(stats["hypoxemia_windows"]) if "hypoxemia_windows" in stats else None
    non_hypoxemia_windows = int(stats["non_hypoxemia_windows"]) if "non_hypoxemia_windows" in stats else None
    window_ratio = (
        hypoxemia_windows / total_windows
        if total_windows and hypoxemia_windows is not None
        else None
    )
    generated_at = ""
    if stats_path:
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stats_path.stat().st_mtime))
    data = {
        "dataset_id": (entry or {}).get("id") or manifest.get("dataset_id", ""),
        "dataset_name": source.name,
        "generated_at": generated_at,
        "source_path": str(source),
        "feature_file": str(features),
        "info_file": str(info),
        "stats_file": str(stats_path) if stats_path else "",
        "stats_status": (
            "已提供 window_stats.json"
            if stats_available
            else "未提供 window_stats.json；窗口统计将在训练或评测阶段计算"
        ),
        "feature_order": FEATURES,
        "sequence_length": lookback_seconds,
        "window_seconds": lookback_seconds,
        "sampling_hz": int(stats.get("sampling_hz", 1)),
        "prediction_interval_seconds": interval_seconds,
        "window_count": total_windows,
        "hypoxemia_window_count": hypoxemia_windows,
        "non_hypoxemia_window_count": non_hypoxemia_windows,
        "hypoxemia_ratio": window_ratio,
        "class_balance": {
            "hypoxemia": hypoxemia_windows,
            "non_hypoxemia": non_hypoxemia_windows,
            "hypoxemia_ratio": window_ratio,
        },
        "split_mode": "grouped",
        "group_key": "group_id",
        "stats": stats,
        "validation_notes": [
            "未反序列化 pickle；训练前由受信任的专用运行环境读取",
            "模型输入固定为 SpO2、HR、PR、RR、PI",
        ],
    }
    message = "五通道窗口数据集文件检查通过"
    if not stats_available:
        message += "；未提供 window_stats.json，窗口统计将在训练或评测阶段计算"
    return _ok(message, data, "dataset-summary")


def _register_dataset(params: dict[str, Any]) -> dict[str, Any]:
    source_value = str(params.get("source_path", "")).strip()
    checked = _validate_dataset(source_value)
    if checked["status"] != "success":
        return checked
    source = Path(checked["data"]["source_path"])
    root = source if source.is_dir() else source.parent
    name = str(params.get("dataset_name") or root.name or "spo2-five-channel-v3")
    dataset_id = str(params.get("dataset_id") or f"spo2-five-channel-{uuid.uuid4().hex[:12]}")
    files = [item for item in root.rglob("*") if item.is_file()]
    spec = (
        f"# {name}\n\n"
        "五通道低氧预测窗口数据集。\n\n"
        "- 通道顺序: `SpO2, HR, PR, RR, PI`\n"
        "- 序列长度: 256\n- 采样率: 1 Hz\n- 预测间隔: 30 秒\n"
        "- 划分: 按 `group_id` 分组，seed=42，validation/test=0.15/0.15\n"
        "- 用途: 研究用途，不用于临床诊断\n"
    )
    result = _call_api(
        "api-data-register", id=dataset_id, name=name, raw_md=spec, data_path=str(root),
        file_count=len(files), total_size=sum(item.stat().st_size for item in files),
        formats=sorted({item.suffix.lstrip(".").lower() for item in files if item.suffix}),
    )
    return _ok("数据集已注册", {**checked["data"], **result, "_action": "register_dataset"}, "dataset-register")


def _inspect_artifact(params: dict[str, Any]) -> dict[str, Any]:
    value = str(params.get("artifact_path") or DEFAULT_ARTIFACT).strip()
    if not value:
        return _fail("请提供 V3 模型产物目录，或设置 SPO2_HYPOXEMIA_V3_ARTIFACT")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        return _fail(f"模型产物目录不存在: {root}")
    missing = [name for name in (*REQUIRED_MODEL_FILES, "manifest.json", "threshold_report_f1.json")
               if not (root / name).is_file()]
    if missing:
        return _fail("模型产物不完整", missing_files=missing, artifact_path=str(root))

    manifest = _read_json(root / "manifest.json")
    config = _read_json(root / "tslib_model_config.json")
    sequence_length = int(config.get("seq_len") or manifest.get("sequence_length") or 256)
    if manifest:
        if tuple(manifest.get("feature_order", FEATURES)) != tuple(FEATURES):
            return _fail("模型产物通道顺序与当前五通道输入不兼容", artifact_path=str(root))
        if int(manifest.get("sequence_length", 0)) != sequence_length or int(manifest.get("sampling_hz", 0)) <= 0:
            return _fail("模型产物序列长度或采样率与当前数据不兼容", artifact_path=str(root))

    report = _read_json(root / "threshold_report_f1.json")
    model_name = str(config.get("model", ""))
    if sequence_length <= 0 or model_name not in _available_model_names() or int(config.get("enc_in", 0)) != 5:
        return _fail("该产物不是当前支持的五通道时序分类模型")
    threshold = report.get("validation", {}).get("threshold", 0.63)
    data = {
        "artifact_path": str(root), "model_name": model_name, "config": config,
        "threshold": threshold, "validation_metrics": report.get("validation", {}),
        "test_metrics": report.get("test", {}), "feature_order": FEATURES,
        "manifest": manifest,
    }
    if params.get("register_model", True):
        existing = next(
            (item for item in _read_json_list(model_registry_path())
             if item.get("runtime_status") == "trained"
             and item.get("model_path") == str(root / "best_model.pt")),
            None,
        )
        if existing:
            data.update({"model_id": existing.get("id", ""), "name": existing.get("name", "")})
            return _ok("训练模型已在模型空间中", data, "model-resource")
        raw_md = (
            "# V3 五通道低氧预测模型\n\n"
            f"{model_name}，输入 {sequence_length}x5 生理信号窗口，输出二分类概率。\n\n"
            f"- 默认阈值: {threshold}\n- 通道: {', '.join(FEATURES)}\n"
            "- 研究用途，不用于临床诊断。\n"
        )
        registered = _call_api(
            "api-model-register", id=f"model-imported-{uuid.uuid4().hex[:12]}",
            name=str(params.get("model_resource_name") or "V3_dualscale_t1"),
            raw_md=raw_md, framework="PyTorch", model_path=str(root / "best_model.pt"),
            input_format=f"timeseries-{sequence_length}x5", output_format="binary-classification",
            tags=["时序分类", "低氧预测", "五通道", "SegRNN"],
            associated_tool_id="timeseries-classification-runner", group_path="低氧血症预测",
            dataset_id=str(params.get("dataset_id") or manifest.get("dataset_id", "")), model_name=model_name,
            task_id=str(manifest.get("task_id", "")), manifest_path=str(root / "manifest.json"),
            model_type=model_name, config_path=str(root / "tslib_model_config.json"),
            scaler_path=str(root / "scaler.pkl"), metrics_path=str(root / "threshold_report_f1.json"),
            threshold=threshold, runtime_status="trained", training_config=config,
        )
        data.update(registered)
        if registered.get("model_id") and manifest:
            manifest["registered_model_id"] = registered["model_id"]
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return _ok("V3 五通道模型产物检查通过", data, "model-resource")


def _create_model(params: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(params.get("dataset_id", "")).strip()
    model_name = str(params.get("model_name", "")).strip()
    if not model_name:
        return _fail("请指定作为实现基础的五通道模型名称；未指定时请先使用创建模型引导")
    if model_name not in _available_model_names():
        return _fail("该模型不适用于当前五通道数据，请先查看可用模型目录", model_name=model_name)
    name = str(params.get("model_resource_name") or model_name)
    model_overrides = _validate_config(
        _parse_config(params.get("model_config"), "model_config"), set(DEFAULT_MODEL_CONFIG),
    )
    training_overrides = _validate_config(
        _parse_config(params.get("training_config"), "training_config"), set(DEFAULT_TRAINING_CONFIG),
    )
    configuration = {
        "model_name": model_name,
        "architecture": {**DEFAULT_MODEL_CONFIG, **model_architecture_defaults(model_name), **model_overrides},
        "training": {**DEFAULT_TRAINING_CONFIG, **training_overrides},
    }
    result = _call_api(
        "api-model-register", name=name, raw_md=(
            f"# {model_name} 五通道低氧预测模型配置\n\n"
            "待训练的时序分类模型资源。输入固定为 256x5，按 group_id 分组划分。\n\n"
            f"```json\n{json.dumps(configuration, ensure_ascii=False, indent=2)}\n```\n"
        ), framework="PyTorch", model_path="", input_format="timeseries-256x5",
        output_format="binary-classification", tags=["时序分类", "低氧预测", "五通道", model_name],
        associated_tool_id="timeseries-classification-runner", group_path="低氧血症预测", dataset_id=dataset_id,
        model_name=model_name, model_type=model_name, threshold=0.63,
        runtime_status="configured", training_config=configuration, creation_type="catalog-model",
    )
    return _ok("模型资源已创建，可提交训练", {
        **result, "dataset_id": dataset_id, "model_name": model_name, "creation_type": "derived-from-catalog",
        "model_type": model_name, "threshold": 0.63, "training_config": configuration,
    }, "model-resource")


def _create_derived_model(params: dict[str, Any]) -> dict[str, Any]:
    source_model_id = str(params.get("source_model_id", "")).strip()
    source = _resolve_model_resource(source_model_id)
    if source is None:
        return _fail("未找到要派生的模型资源", source_model_id=source_model_id)

    source_configuration = source.get("training_config", {})
    if not isinstance(source_configuration, dict):
        source_configuration = {}
    model_name = str(source_configuration.get("model_name") or source.get("model_name") or "").strip()
    if model_name not in _available_model_names():
        return _fail("源模型不是当前五通道目录中的可训练模型", source_model_id=source_model_id)

    dataset_id = str(params.get("dataset_id") or source.get("dataset_id") or "").strip()
    if not dataset_id:
        return _fail("源模型未关联数据集，请提供 dataset_id")
    source_architecture = source_configuration.get("architecture", {})
    source_training = source_configuration.get("training", {})
    architecture = {
        **DEFAULT_MODEL_CONFIG, **model_architecture_defaults(model_name),
        **(source_architecture if isinstance(source_architecture, dict) else {}),
    }
    training = {**DEFAULT_TRAINING_CONFIG, **(source_training if isinstance(source_training, dict) else {})}
    architecture.update(_validate_config(
        _parse_config(params.get("model_config"), "model_config"), set(DEFAULT_MODEL_CONFIG),
    ))
    training.update(_validate_config(
        _parse_config(params.get("training_config"), "training_config"), set(DEFAULT_TRAINING_CONFIG),
    ))
    configuration = {"model_name": model_name, "architecture": architecture, "training": training}
    name = str(params.get("model_resource_name") or f"{source.get('name', model_name)}_variant")
    result = _call_api(
        "api-model-register", name=name, raw_md=(
            f"# {name}\n\n"
            f"基于模型资源 `{source_model_id}` 派生的五通道低氧预测模型。模型实现为 {model_name}；"
            "仅结构化参数和训练参数不同。\n\n"
            f"```json\n{json.dumps(configuration, ensure_ascii=False, indent=2)}\n```\n"
        ), framework="PyTorch", model_path="", input_format="timeseries-256x5",
        output_format="binary-classification", tags=["时序分类", "低氧预测", "五通道", model_name, "派生模型"],
        associated_tool_id="timeseries-classification-runner", group_path="低氧血症预测", dataset_id=dataset_id,
        model_name=model_name, model_type=model_name, threshold=0.63,
        runtime_status="configured", training_config=configuration, creation_type="derived-from-resource",
        derived_from_model_id=source_model_id,
    )
    return _ok("派生模型资源已创建，可提交训练", {
        **result, "dataset_id": dataset_id, "model_name": model_name, "model_type": model_name,
        "creation_type": "derived-from-resource", "derived_from_model_id": source_model_id,
        "threshold": 0.63, "training_config": configuration,
    }, "model-resource")


def _get_model_configuration(params: dict[str, Any]) -> dict[str, Any]:
    model_id = str(params.get("model_id", "")).strip()
    resource = _resolve_model_resource(model_id)
    if resource is None:
        return _fail("未找到模型资源", model_id=model_id)
    return _ok("模型配置已读取", {
        "model_id": resource.get("id", ""),
        "model_name": resource.get("model_name", resource.get("name", "")),
        "dataset_id": resource.get("dataset_id", ""),
        "runtime_status": resource.get("runtime_status", ""),
        "creation_type": resource.get("creation_type", ""),
        "derived_from_model_id": resource.get("derived_from_model_id", ""),
        "training_config": resource.get("training_config", {}),
    }, "model-resource")


def _is_trusted_pickle(path: Path) -> bool:
    # Explicitly supplied dataset paths are validated by schema checks and
    # executed only by the isolated compute process. No per-directory allow-list
    # or backend restart is required.
    return path.expanduser().is_file()


def _resolve_training_configuration(params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    requested_id = str(params.get("model_id", "")).strip()
    if requested_id in UNSUPPORTED_CLASSIFICATION_MODEL_NAMES:
        raise ValueError(f"{requested_id} 的上游实现尚未支持分类任务，不能用于低氧训练")
    resource = _resolve_model_resource(requested_id)
    if resource is None and requested_id and requested_id in _available_model_names():
        resource = {"model_name": requested_id, "training_config": {}}
    if requested_id and resource is None:
        raise ValueError(f"model resource not found: {requested_id}")

    stored = resource.get("training_config", {}) if resource else {}
    if not isinstance(stored, dict):
        stored = {}
    model_name = str(
        params.get("model_name") or stored.get("model_name") or (resource or {}).get("model_name") or "SegRNNDualScaleV2"
    ).strip()
    if model_name in UNSUPPORTED_CLASSIFICATION_MODEL_NAMES:
        raise ValueError(f"{model_name} 的上游实现尚未支持分类任务，不能用于低氧训练")
    if model_name not in _available_model_names():
        raise ValueError(f"model is not available for the five-channel dataset: {model_name}")

    architecture = dict(stored.get("architecture", {})) if isinstance(stored.get("architecture"), dict) else {}
    training = dict(stored.get("training", {})) if isinstance(stored.get("training"), dict) else {}
    architecture.update(_parse_config(params.get("model_config"), "model_config"))
    training.update(_parse_config(params.get("training_config"), "training_config"))
    for key in DEFAULT_TRAINING_CONFIG:
        if params.get(key) is not None:
            training[key] = params[key]

    architecture = {
        **DEFAULT_MODEL_CONFIG, **model_architecture_defaults(model_name),
        **_validate_config(architecture, set(DEFAULT_MODEL_CONFIG)),
    }
    if model_name in MULTISCALE_MODEL_NAMES and (
        int(architecture["down_sampling_layers"]) < 1
        or int(architecture["down_sampling_window"]) < 2
    ):
        raise ValueError(
            f"{model_name} 需要 down_sampling_layers >= 1 且 down_sampling_window >= 2；"
            "旧模型配置请修改这两个字段，或重新选择目录模型。"
        )
    training = {**DEFAULT_TRAINING_CONFIG, **_validate_config(training, set(DEFAULT_TRAINING_CONFIG))}
    for key, allowed in {
        "activation": {"gelu", "relu", "tanh"},
        "optimizer": {"adamw", "adam", "sgd", "rmsprop"},
        "embed": {"fixed", "timeF", "learned"},
        "down_sampling_method": {"avg", "max", "conv"},
        "decomp_method": {"moving_avg", "dft_decomp"},
        "selection_monitor": {"auc", "accuracy", "loss"},
        "lr_monitor": {"auc", "accuracy", "loss"},
        "threshold_metric": {"f1", "accuracy", "balanced"},
    }.items():
        if training.get(key, architecture.get(key)) not in allowed:
            raise ValueError(f"{key} must be one of: {', '.join(sorted(allowed))}")

    resource_id = str((resource or {}).get("id") or requested_id or model_name)
    return resource_id, model_name, {"model_name": model_name, "architecture": architecture, "training": training}


def _append_option(command: list[str], name: str, value: Any) -> None:
    option_names = {"learning_rate": "lr"}
    command.extend([f"--{option_names.get(name, name).replace('_', '-')}", str(value)])


def _build_train_request(params: dict[str, Any]) -> dict[str, Any]:
    source_value = str(params.get("dataset") or params.get("dataset_id") or params.get("source_path") or "").strip()
    checked = _validate_dataset(source_value)
    if checked["status"] != "success":
        return checked
    feature_path = Path(checked["data"]["feature_file"])
    info_path = Path(checked["data"]["info_file"])
    if False and (not _is_trusted_pickle(feature_path) or not _is_trusted_pickle(info_path)):
        return _fail(
            "拒绝反序列化未受信任目录中的 pickle。请把本地数据根目录加入 "
            "SPO2_HYPOXEMIA_TRUSTED_DATA_ROOTS 后重启后端。"
        )

    external = COMPONENT_ROOT
    script = external / "scripts" / "run_tslib_classification_trial.py"
    tslib_root = external / "vendor" / "Time-Series-Library"
    if not script.is_file() or not tslib_root.is_dir():
        return _fail("内置低氧组件不完整，请重新安装 SOTABand", project_path=str(external))
    python_exe = compute_python(params.get("python_executable"))
    if not python_exe or not Path(python_exe).is_file():
        return _fail("低氧计算环境尚未安装，请运行 scripts/install_hypoxemia.ps1")

    model_id, model_name, configuration = _resolve_training_configuration(params)
    catalog_entry = next(item for item in _model_catalog() if item["model_name"] == model_name)
    if not catalog_entry["available"]:
        return _fail(f"{model_name} 当前不可训练：{catalog_entry['reason']}")
    architecture = configuration["architecture"]
    if model_name in MULTISCALE_MODEL_NAMES and (
        int(architecture["down_sampling_window"]) ** int(architecture["down_sampling_layers"])
        > int(checked["data"]["sequence_length"])
    ):
        return _fail("下采样尺度超过数据集的实际序列长度，请减小层数或下采样窗口")
    training = configuration["training"]
    epochs = int(training["epochs"])
    batch_size = int(training["batch_size"])
    lr = float(training["learning_rate"])
    run_root = timeseries_runs_dir() / f"{model_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    command = [
        python_exe, str(script), "--trial-dir", str(run_root), "--trial-id", model_id,
        "--group-id", model_name, "--order-index", "0", "--model-id", model_id,
        "--model-name", model_name, "--label", f"SOTABand {model_name}",
        "--tslib-root", str(tslib_root), "--data-path", str(feature_path), "--info-path", str(info_path),
        "--split-mode", "grouped", "--group-key", "group_id", "--validation-size", "0.15",
    ]
    for key, value in architecture.items():
        if key == "distil":
            if not value:
                command.append("--no-distil")
            continue
        if key == "individual":
            if value:
                command.append("--individual")
            continue
        _append_option(command, key, ",".join(map(str, value)) if key == "p_hidden_dims" else value)
    for key, value in training.items():
        if key == "optimizer":
            _append_option(command, key, value)
            continue
        if key == "use_ema":
            if value:
                command.append("--use-ema")
            continue
        _append_option(command, key, value)
    task_request = {
        "name": f"训练 {model_id}", "kind": "hypoxemia-training", "command": command, "cwd": str(timeseries_runs_dir()),
        "metadata": {"epochs": epochs, "model_id": model_id, "model_name": model_name,
                     "dataset_id": checked["data"].get("dataset_id", ""), "trial_dir": str(run_root),
                     "expected_model": model_name, "training_config": configuration,
                     "feature_order": checked["data"]["feature_order"],
                     "sequence_length": checked["data"]["sequence_length"],
                     "sampling_hz": checked["data"]["sampling_hz"],
                     "save_trained_model": False},
    }
    return _ok("训练配置已校验，等待任务层提交", {
        "task_request": task_request, "model_id": model_id, "model_name": model_name,
        "training_config": configuration,
    }, "task-progress")


def _check_evaluation_compatibility(root: Path, dataset: Path) -> dict[str, Any] | None:
    required = (*REQUIRED_MODEL_FILES, "threshold_report_f1.json", "manifest.json")
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        return _fail("模型产物缺少评测所需文件或 manifest，请先完成模型登记", missing_files=missing)
    model = _read_json(root / "manifest.json")
    config = _read_json(root / "tslib_model_config.json")
    data_manifest = _read_json(dataset / "dataset_manifest.json")
    stats = _read_json(dataset / "window_stats.json")
    if not model or not config or not (data_manifest or stats):
        return _fail("模型或数据集缺少可读取的兼容性元数据")
    try:
        model_length = int(model["sequence_length"])
        model_rate = int(model["sampling_hz"])
        # Legacy processed datasets may omit sampling_hz from window_stats.
        # The built-in SpO2 window contract is 1 Hz, which is also the default
        # used by dataset validation when no explicit rate is declared.
        data_length = int(data_manifest.get("sequence_length") or stats.get("lookback_seconds", 0))
        data_rate = int(data_manifest.get("sampling_hz") or stats.get("sampling_hz", 1))
        config_length = int(config["seq_len"])
        channels = int(config["enc_in"])
    except (KeyError, TypeError, ValueError):
        return _fail("模型或数据集缺少序列长度、通道数或采样率")
    model_features = model.get("feature_order")
    data_features = data_manifest.get("feature_order") or stats.get("feature_order") or FEATURES
    if (model_length <= 0 or model_rate <= 0 or data_length <= 0 or data_rate <= 0
            or model_length != config_length or channels != len(FEATURES)
            or model_length != data_length or model_rate != data_rate
            or model_features != FEATURES or data_features != FEATURES):
        return _fail("模型与数据集的通道顺序、序列长度或采样率不兼容")
    if data_manifest:
        if (stats and (int(stats.get("lookback_seconds", data_length)) != data_length
                       or int(stats.get("sampling_hz", data_rate)) != data_rate)):
            return _fail("数据集清单与窗口统计不一致")
    return None


def _evaluate(params: dict[str, Any]) -> dict[str, Any]:
    artifact_value = str(params.get("artifact_path") or "").strip()
    dataset_value = str(
        params.get("dataset") or params.get("source_path") or params.get("processed_dataset_path") or ""
    ).strip()
    task_id = str(params.get("task_id") or "").strip()
    model_id = str(params.get("model_id") or "").strip()
    resource = _resolve_model_resource(model_id) if model_id else None
    if model_id and resource is None and model_id not in _available_model_names():
        return _fail("未找到指定的模型资源", model_id=model_id)
    if resource:
        if resource.get("runtime_status") != "trained" or not resource.get("model_path"):
            return _fail("所选模型尚无可评测的训练产物", model_id=model_id)
        registered_root = Path(str(resource["model_path"])).expanduser().resolve().parent
        if artifact_value and Path(artifact_value).expanduser().resolve() != registered_root:
            return _fail("所选模型 ID 与产物目录不一致", model_id=model_id)
        artifact_value = str(registered_root)
    if task_id:
        tasks = _read_json_list(task_registry_path())
        task = next((item for item in tasks if item.get("id") == task_id), None)
        if task and task.get("status") != "completed":
            return _fail("训练任务尚未成功完成，无法评测", task_id=task_id, task_status=task.get("status"))
        if task and not artifact_value:
            artifact_value = str(task.get("metadata", {}).get("artifact_path")
                                 or task.get("metadata", {}).get("trial_dir", ""))
    if not artifact_value and model_id:
        return _fail("无法通过模型 ID 找到训练产物", model_id=model_id)
    root = Path(artifact_value or DEFAULT_ARTIFACT).expanduser().resolve()
    if dataset_value:
        dataset, _entry = _resolve_dataset_source(dataset_value)
        if dataset is None or not dataset.exists():
            return _fail(f"评测数据集不存在: {dataset_value}")
        dataset = dataset if dataset.is_dir() else dataset.parent
        if False and not all(_is_trusted_pickle(dataset / name) for name in REQUIRED_DATA_FILES):
            return _fail("拒绝评测未受信任目录中的 pickle，请配置受信任的数据根目录")
        evaluator = COMPONENT_ROOT / "evaluate_dataset.py"
        python_exe = compute_python(params.get("python_executable"))
        if root.is_dir() and not (root / "best_model.pt").is_file():
            # Training tasks record the trial directory, while the runner
            # writes the actual artifact under runs/<run-name>. Resolve that
            # layout before declaring a completed task unusable.
            nested_roots = [
                candidate.parent
                for candidate in root.rglob("best_model.pt")
                if all((candidate.parent / name).is_file() for name in REQUIRED_MODEL_FILES)
            ]
            if nested_roots:
                root = max(nested_roots, key=lambda path: path.stat().st_mtime)
        if not root.is_dir() or not (root / "best_model.pt").is_file():
            return _fail("模型产物不完整，无法在当前数据集上评测", artifact_path=str(root))
        requested_dataset_id = str(params.get("dataset_id") or "")
        dataset_manifest = _read_json(dataset / "dataset_manifest.json")
        resolved_dataset_id = str((_entry or {}).get("id") or dataset_manifest.get("dataset_id") or "")
        if requested_dataset_id and resolved_dataset_id and requested_dataset_id != resolved_dataset_id:
            return _fail("所选数据集 ID 与数据目录不一致", dataset_id=requested_dataset_id)
        compatibility_error = _check_evaluation_compatibility(root, dataset)
        if compatibility_error:
            return compatibility_error
        output_path = timeseries_runs_dir() / f"evaluation-{uuid.uuid4().hex}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            python_exe, str(evaluator),
            "--artifact", str(root), "--dataset", str(dataset), "--output", str(output_path),
            "--metrics", ",".join(params.get("metrics", []) if isinstance(params.get("metrics"), list) else [str(params.get("metrics", "all"))]),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, cwd=str(output_path.parent), env=compute_env(), timeout=1800, encoding="utf-8")
        if completed.returncode != 0:
            return _fail("当前数据集评测失败", dataset_path=str(dataset), artifact_path=str(root), error=completed.stderr[-4000:])
        try:
            current = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _fail("当前数据集评测结果无法读取", error=str(exc))
        data = {
            "report_path": str(output_path),
            "dataset_id": resolved_dataset_id or requested_dataset_id,
            "task_id": task_id or (resource or {}).get("task_id", ""),
            "dataset_path": str(dataset),
            "evaluation_scope": current.get("evaluation_scope", "validation_split"),
            "evaluation_split": current.get("evaluation_split", "validation"),
            "evaluation_scope_label": current.get(
                "evaluation_scope_label",
                "验证集（按训练时的分组规则重新划分；训练集和测试集未参与本次评测）",
            ),
            "dataset_window_count": current.get("dataset_window_count", 0),
            "dataset_positive_count": current.get("dataset_positive_count", 0),
            "dataset_negative_count": current.get("dataset_negative_count", 0),
            "source_dataset_window_count": current.get("source_dataset_window_count", 0),
            "source_dataset_positive_count": current.get("source_dataset_positive_count", 0),
            "split_report": current.get("split_report", {}),
            "model_artifact": current.get("model_artifact", str(root)),
            "model_resource_id": str(resource.get("id") or "") if resource else "",
            "model_files": {
                "weights": str(root / "best_model.pt"),
                "scaler": str(root / "scaler.pkl"),
                "config": str(root / "tslib_model_config.json"),
                "threshold_report": str(root / "threshold_report_f1.json"),
            },
            "threshold": current.get("threshold"),
            "selected_metrics": current.get("metrics", {}),
            "validation": current.get("metrics", {}),
            "test": {},
            "research_only": True,
        }
        if params.get("export_excel"):
            export_dir = timeseries_runs_dir() / "evaluations"
            export_dir.mkdir(parents=True, exist_ok=True)
            excel_path = export_dir / f"hypoxemia_evaluation_{uuid.uuid4().hex[:12]}.xlsx"
            _write_metrics_xlsx(excel_path, data)
            data["file_path"] = str(excel_path)
            data["excel_path"] = str(excel_path)
        return _ok("已使用当前输入数据集的验证集完成模型评测（训练集用于训练，测试集保留作独立最终测评）", data, "evaluation-result")
    reports = list(root.rglob("threshold_report_f1.json")) if root.is_dir() else []
    if not reports:
        return _fail("未找到 threshold_report_f1.json", artifact_path=str(root))
    report_path = max(reports, key=lambda item: item.stat().st_mtime)
    report = _read_json(report_path)
    requested = params.get("metrics")
    include_all = requested is None or requested == "all"
    requested = requested or list(EVALUATION_METRICS)
    if isinstance(requested, str):
        requested = [item.strip() for item in requested.split(",") if item.strip()]
    metrics = [item for item in requested if item in EVALUATION_METRICS]
    if not metrics:
        metrics = list(EVALUATION_METRICS)

    def select_metrics(values: Any) -> dict[str, Any]:
        if not isinstance(values, dict):
            return {}
        if include_all:
            return values
        return {key: value for key, value in values.items() if key in metrics}

    data = {
        "report_path": str(report_path), "threshold": report.get("validation", {}).get("threshold"),
        "selected_metrics": metrics,
        "validation": select_metrics(report.get("validation", {})),
        "test": select_metrics(report.get("test", {})),
        "test_at_0_5": select_metrics(report.get("test_at_0_5", {})),
        "split_report": report.get("split_report", {}),
        "research_only": True,
    }
    if params.get("export_excel"):
        export_dir = timeseries_runs_dir() / "evaluations"
        export_dir.mkdir(parents=True, exist_ok=True)
        excel_path = export_dir / f"hypoxemia_evaluation_{int(time.time())}.xlsx"
        _write_metrics_xlsx(excel_path, data)
        data["file_path"] = str(excel_path)
        data["excel_path"] = str(excel_path)
    if params.get("save_trained_model"):
        inspected = _inspect_artifact({
            "artifact_path": str(report_path.parent), "register_model": True,
            "model_resource_name": params.get("model_resource_name") or report_path.parent.name,
            "dataset_id": params.get("dataset_id", ""),
        })
        if inspected.get("status") == "success":
            data["saved_model"] = {
                "model_id": inspected.get("data", {}).get("model_id", ""),
                "name": inspected.get("data", {}).get("name", ""),
            }
        else:
            data["save_model_error"] = inspected.get("message", "模型保存失败")
    return _ok("评测报告读取完成；阈值来自验证集，未使用测试集选阈值", data, "evaluation-result")


def _write_metrics_xlsx(path: Path, data: dict[str, Any]) -> None:
    rows: list[list[Any]] = [["split", "metric", "value"]]
    for key in ("model_resource_id", "dataset_id", "task_id", "model_artifact", "dataset_path",
                "evaluation_scope", "threshold", "report_path"):
        rows.append(["provenance", key, data.get(key, "")])
    for split in ("validation", "test", "test_at_0_5"):
        for metric, value in (data.get(split) or {}).items():
            rows.append([split, metric, value])

    def cell(value: Any, column: int, row: int) -> str:
        ref = f"{chr(64 + column)}{row}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    sheet_rows = "".join(
        f'<row r="{row_index}">' + "".join(
            cell(value, column_index, row_index)
            for column_index, value in enumerate(values, 1)
        ) + "</row>"
        for row_index, values in enumerate(rows, 1)
    )
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="评测结果" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""",
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet_rows}</sheetData></worksheet>""",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8"))


def execute(**kwargs: Any) -> dict[str, Any]:
    try:
        mode = str(kwargs.get("mode", "validate"))
        if mode == "list-models":
            return _list_models()
        if mode == "model-parameters":
            return _model_parameter_schema(kwargs)
        if mode == "model-creation-guide":
            return _model_creation_guide()
        if mode == "dataset-source":
            return _select_dataset_source(kwargs)
        if mode == "process-raw-dataset":
            return _build_raw_processing_request(kwargs)
        if mode == "custom-model-design":
            return _analyze_custom_model_design(kwargs)
        if mode == "delete-model":
            return _delete_model(kwargs)
        if mode == "validate":
            return _validate_dataset(str(kwargs.get("source_path", "")))
        if mode == "register-dataset":
            return _register_dataset(kwargs)
        if mode == "create-model":
            return _create_model(kwargs)
        if mode == "derive-model":
            return _create_derived_model(kwargs)
        if mode == "model-config":
            return _get_model_configuration(kwargs)
        if mode == "inspect-model":
            return _inspect_artifact(kwargs)
        if mode == "train":
            return _build_train_request(kwargs)
        if mode == "evaluate":
            return _evaluate(kwargs)
        if mode == "predict":
            return _fail("第一版预测入口需要单个 256x5 CSV/JSON 样本；请先完成训练与评测流程")
        return _fail(f"未知模式: {mode}")
    except Exception as exc:
        return _fail(f"时序分类工具执行失败: {exc}", error=type(exc).__name__)
